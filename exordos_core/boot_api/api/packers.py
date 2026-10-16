#    Copyright 2025 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import base64
import hmac
import os
import shutil
import time
import uuid as sys_uuid

from gcl_sdk.agents.universal.api import boot_identity
from gcl_sdk.agents.universal.api import packers as sdk_packers
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
import netaddr
from restalchemy.api import packers
from restalchemy.common import exceptions as ra_exceptions
from restalchemy.dm import filters as dm_filters

from exordos_core.boot_api.dm import models
from exordos_core.compute.dm import models as compute_models

BOOT_NONCES_DIR = "/var/lib/exordos/boot-api-delegations"


class BootIdentityError(ra_exceptions.RestAlchemyException):
    code = 403
    message = "%(reason)s"


class BootEncryptionInformation(sdk_packers.NoEncryptionInformation):
    @property
    def node_uuid(self):
        if hasattr(self, "_node_uuid"):
            return self._node_uuid
        claimed = self._request.headers.get(sdk_packers.EXORDOS_NODE_UUID_HEADER)
        try:
            node = sys_uuid.UUID(claimed) if claimed else None
        except ValueError:
            raise ra_exceptions.ParseError(value="Boot node identity") from None
        if self._request.headers.get(boot_identity.HOST_HEADER):
            if node is None:
                raise BootIdentityError(reason="Missing delegated Boot identity")
            self._verify_proxy(node)
        else:
            port = compute_models.Port.objects.get_one(
                filters={
                    "ipv4": dm_filters.EQ(netaddr.IPAddress(self._request.remote_addr))
                }
            )
            source_node = port.node or port.machine
            if source_node is None or (node is not None and node != source_node):
                raise BootIdentityError(
                    reason="Boot caller identity does not match its port"
                )
            node = source_node
        self._node_uuid = node
        return node

    def _verify_proxy(self, node):
        headers = self._request.headers
        try:
            host = sys_uuid.UUID(headers[boot_identity.HOST_HEADER])
            timestamp = headers.get(boot_identity.TIME_HEADER, "")
            issued_at = int(timestamp)
            nonce = str(sys_uuid.UUID(headers.get(boot_identity.NONCE_HEADER, "")))
        except ValueError:
            raise ra_exceptions.ParseError(value="Boot delegation identity") from None
        now = int(time.time())
        if not 0 <= now - issued_at <= boot_identity.MAX_AGE:
            raise BootIdentityError(reason="Expired Boot delegation")
        supplied = headers.get(boot_identity.SIGNATURE_HEADER, "")
        if len(supplied) != 64 or any(c not in "0123456789abcdef" for c in supplied):
            raise ra_exceptions.ParseError(value="Boot delegation signature")
        key = ua_models.NodeEncryptionKey.objects.get_one(
            filters={"uuid": dm_filters.EQ(host)}
        )
        expected = boot_identity.signature(
            base64.b64decode(key.private_key),
            host,
            node,
            timestamp,
            nonce,
            self._request.method,
            self._request.path_qs,
            self._request.body,
        )
        if not hmac.compare_digest(expected, supplied):
            raise BootIdentityError(reason="Invalid Boot delegation signature")
        machine = compute_models.Machine.objects.get_one(
            filters={"node": dm_filters.EQ(node)}
        )
        pool = compute_models.MachinePool.objects.get_one(
            filters={"uuid": dm_filters.EQ(machine.pool)}
        )
        if pool.hypervisor_node != host:
            raise BootIdentityError(reason="Boot delegate does not own the caller")
        # Atomic markers are shared by all Boot API workers and survive restarts.
        os.makedirs(BOOT_NONCES_DIR, mode=0o700, exist_ok=True)
        for bucket in os.listdir(BOOT_NONCES_DIR):
            if bucket.isdecimal() and int(bucket) < now - boot_identity.MAX_AGE:
                shutil.rmtree(os.path.join(BOOT_NONCES_DIR, bucket), ignore_errors=True)
        directory = os.path.join(BOOT_NONCES_DIR, timestamp)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        marker = os.path.join(directory, "%s-%s" % (host, nonce))
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise BootIdentityError(reason="Replayed Boot delegation") from None
        os.close(fd)


_from_net_template = """#!ipxe
:kernel
kernel {kernel} showopts ip=dhcp net.ifnames=0 biosdevname=0 gc_boot_api={gc_boot_api} console=ttyS0,115200 console=tty0 || goto kernel

:initrd
initrd {initrd} || goto initrd
boot
"""


_from_hd_template = """#!ipxe

echo Boot from local disk {disk_number}
sanboot --no-describe --drive 0x8{disk_number}
"""


class IPXEPacker(packers.JSONPacker):
    def pack(self, obj: models.MachineNetboot):
        boot = ua_pool.BootAlternative[obj.boot]

        if boot == ua_pool.BootAlternative.network:
            return _from_net_template.format(
                gc_boot_api=obj.gc_boot_api,
                kernel=obj.kernel,
                initrd=obj.initrd,
            )
        elif boot.boot_type == "hd":
            return _from_hd_template.format(disk_number=boot.value[2])

        raise ValueError(f"Invalid boot alternative: {boot}")
