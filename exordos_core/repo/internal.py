#    Copyright 2026 Genesis Corporation.
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
"""Per-project internal element repositories.

The core LB serves ``/repo/<project_id>/`` from the core node as WebDAV and
asks the user API (``/v1/repo/auth/``) whether each request may pass.
"""

import typing as tp
from urllib import parse as urllib_parse
import uuid as sys_uuid

import netaddr
from restalchemy.common import exceptions as ra_exceptions
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.compute.dm import models as compute_models
from exordos_core.repo.dm import models
from exordos_core.vs.dm import models as vs_models

URL_PREFIX = "/repo/"
# The CLI pushes elements under this dir of the project's repo.
ELEMENTS_DIR = "exordos-elements/"
READ_METHODS = frozenset({"GET", "HEAD"})
# MOVE and COPY are left out: their target comes in the Destination
# header, which the project check below never sees.
WRITE_METHODS = frozenset({"PUT", "DELETE", "MKCOL"})

_NS_INTERNAL_REPO = sys_uuid.UUID("4b0f7d3e-2c7a-4f5e-9a51-6f0e8f3c2d17")


def repository_uuid(project_id: sys_uuid.UUID) -> sys_uuid.UUID:
    return sys_uuid.uuid5(_NS_INTERNAL_REPO, str(project_id))


def parse_project_id(uri: str) -> tp.Optional[sys_uuid.UUID]:
    """Return the project a ``/repo/<project_id>/...`` URI belongs to.

    The URI is the raw request URI, while nginx serves the normalized one,
    so anything that could normalize into another project is refused.
    """
    path = urllib_parse.unquote(urllib_parse.urlsplit(uri).path)
    if not path.startswith(URL_PREFIX):
        return None
    segments = path[len(URL_PREFIX) :].split("/")
    if any(s in (".", "..") or "\\" in s for s in segments):
        return None
    try:
        return sys_uuid.UUID(segments[0])
    except ValueError:
        return None


def is_realm_address(address: str) -> bool:
    try:
        ip = netaddr.IPAddress(address)
    except (ValueError, netaddr.AddrFormatError):
        return False
    if ip.is_loopback():
        return True
    return any(ip in s.cidr for s in compute_models.Subnet.objects.get_all())


def ensure_repository(project_id: sys_uuid.UUID) -> None:
    repo_uuid = repository_uuid(project_id)
    if models.Repository.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(repo_uuid)}
    ):
        return

    core_ip = vs_models.Value.objects.get_one(
        filters={"uuid": dm_filters.EQ(c.VALUE_CORE_IP_ADDRESS_UUID)}
    ).value
    repository = models.Repository(
        uuid=repo_uuid,
        name="internal",
        description="Elements pushed to this realm by the project",
        project_id=project_id,
        sync_mode=models.SyncMode.COPY.value,
        driver_spec=models.InternalDriverSpec(
            url=f"http://{core_ip}{URL_PREFIX}{project_id}/{ELEMENTS_DIR}"
        ),
    )
    try:
        repository.insert()
    except ra_exceptions.ConflictRecords:
        # A concurrent first push created it.
        pass
