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

import datetime
import os
import re
import shutil
import time
import uuid as sys_uuid

from gcl_sdk.agents.universal.api import boot_identity
from gcl_sdk.agents.universal.api import middlewares as sdk_mw
from gcl_sdk.agents.universal.api import packers as sdk_packers
from gcl_sdk.agents.universal.drivers import pool as ua_pool
import netaddr
from oslo_config import cfg
from restalchemy.api import packers
from restalchemy.common import exceptions as ra_exceptions
from restalchemy.dm import filters as dm_filters

from exordos_core.boot_api.dm import models
from exordos_core.compute.dm import models as compute_models

BOOT_NONCES_DIR = "/var/lib/exordos/boot-api-delegations"
CONF = cfg.CONF


class BootIdentityError(ra_exceptions.RestAlchemyException):
    code = 403
    message = "%(reason)s"


def consume_nonce(host, nonce, issued_at):
    now = int(time.time())
    if type(issued_at) is not int or not 0 <= now - issued_at <= boot_identity.MAX_AGE:
        raise BootIdentityError(reason="Expired Boot delegation")
    os.makedirs(BOOT_NONCES_DIR, mode=0o700, exist_ok=True)
    # The replay store is shared by workers. Expired buckets are not identities.
    for bucket in os.listdir(BOOT_NONCES_DIR):
        if bucket.isdecimal() and int(bucket) < now - boot_identity.MAX_AGE:
            shutil.rmtree(os.path.join(BOOT_NONCES_DIR, bucket), ignore_errors=True)
    directory = os.path.join(BOOT_NONCES_DIR, str(issued_at))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        fd = os.open(
            os.path.join(directory, "%s-%s" % (host, nonce)),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError:
        raise BootIdentityError(reason="Replayed Boot delegation") from None
    os.close(fd)


def _lease_mac(address):
    try:
        with open(CONF.boot_api.dhcp_leases_file) as leases:
            content = leases.read()
    except OSError:
        raise BootIdentityError(reason="Boot leases are unavailable") from None
    blocks = re.findall(
        r"(?m)^lease " + re.escape(str(address)) + r" \{(.*?)^\}", content, re.S | re.M
    )
    if not blocks:
        raise BootIdentityError(reason="No Boot lease for caller")
    lease = blocks[-1]
    mac = re.search(r"hardware ethernet ([0-9a-fA-F:]{17});", lease)
    ends = re.search(r"ends (?:\d )?(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2});", lease)
    if not mac or "binding state active;" not in lease or not ends:
        raise BootIdentityError(reason="Inactive Boot lease")
    try:
        expires = datetime.datetime.strptime(ends[1], "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        raise BootIdentityError(reason="Invalid Boot lease expiry") from None
    if expires.timestamp() <= time.time():
        raise BootIdentityError(reason="Expired Boot lease")
    return mac[1].lower()


class BootEncryptionInformation(sdk_packers.NoEncryptionInformation):
    @property
    def node_uuid(self):
        if hasattr(self, "_node_uuid"):
            return self._node_uuid
        delegated = self._request.environ.get(boot_identity.NODE_ENV)
        if delegated is not None:
            self._node_uuid = sys_uuid.UUID(delegated)
            return self._node_uuid
        # Legacy Seed has no key yet. Its lease must come from a configured
        # Boot subnet, not from a source address claimed in HTTP headers.
        address = netaddr.IPAddress(self._request.remote_addr)
        # Legacy flat Boot subnets predate the overlay DHCP toggle.
        subnets = compute_models.Subnet.objects.get_all()
        if not any(
            s.next_server and s.ip_discovery_range and address in s.cidr
            for s in subnets
        ):
            raise BootIdentityError(reason="Caller is outside the Boot network")
        port = compute_models.Port.objects.get_one_or_none(
            filters={"ipv4": dm_filters.EQ(address)}
        )
        if port is None:
            port = compute_models.Port.objects.get_one(
                filters={"mac": dm_filters.EQ(_lease_mac(address))}
            )
        source_node = port.node or port.machine
        claimed = self._request.headers.get(sdk_packers.EXORDOS_NODE_UUID_HEADER)
        try:
            if source_node is None or (
                claimed and sys_uuid.UUID(claimed) != source_node
            ):
                raise BootIdentityError(
                    reason="Boot caller identity does not match its port"
                )
        except ValueError:
            raise ra_exceptions.ParseError(value="Boot node identity") from None
        self._node_uuid = source_node
        return source_node


class BootContextMiddleware(sdk_mw.SdkContextMiddleware):
    def _get_response(self, ctx, req):
        # Authentication is eager; no handler can bypass it by not asking for
        # node_uuid (including actions and serializers).
        if not (
            req.method == "GET"
            and re.fullmatch(r"/v1/boots/[0-9a-fA-F-]{36}", req.path_info)
        ):
            ctx.encryption_information.node_uuid
        return super()._get_response(ctx, req)


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
