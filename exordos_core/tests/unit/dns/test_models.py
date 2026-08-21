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

import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.common import constants as c
from exordos_core.user_api.dns.dm import models

REALM_ID = "a5f3957"


def _domain(**kwargs):
    values = {
        "id": 1,
        "name": "exordos.io",
        "realm_id": REALM_ID,
        "sync_only": True,
        "sync_to_ecosystem": True,
        "project_id": c.ZERO_UUID,
    }
    values.update(kwargs)
    return models.Domain.restore(**values)


def _a_record(domain, name="workspace"):
    return models.Record(
        uuid=sys_uuid.uuid4(),
        domain=domain,
        type="A",
        record=models.ARecord(
            name=name,
            address=netaddr.IPAddress("192.0.2.10"),
        ),
        project_id=c.ZERO_UUID,
    )


def test_realm_record_keeps_logical_name_and_exposes_full_name():
    record = _a_record(_domain())

    assert record.record.name == "workspace"
    assert record.full_name == "workspace-a5f3957.exordos.io"


@pytest.mark.parametrize("name", ["", "@"])
def test_realm_apex_uses_realm_id_as_owner(name):
    record = _a_record(_domain(), name=name)

    assert record.full_name == "a5f3957.exordos.io"


def test_realm_full_name_is_canonical_lowercase_without_changing_logical_name():
    record = _a_record(_domain(), name="Workspace")

    assert record.record.name == "Workspace"
    assert record.full_name == "workspace-a5f3957.exordos.io"


@pytest.mark.parametrize(
    "record",
    [
        models.TXTRecord(name="verification", content="token"),
        models.NSRecord(name="delegation", content="ns.example.com."),
    ],
)
def test_realm_scope_applies_to_supported_owner_names(record):
    assert record.get_name(_domain()) == (f"{record.name}-{REALM_ID}.exordos.io")


@pytest.mark.parametrize("name", ["*.workspace", "api.workspace"])
def test_realm_scope_rejects_names_that_cannot_stay_flat(name):
    with pytest.raises(models.RealmRecordNameNotSupported):
        _a_record(_domain(), name=name)


def test_regular_domain_naming_is_unchanged():
    domain = _domain(
        name="example.com",
        realm_id=None,
        sync_only=False,
        sync_to_ecosystem=False,
    )

    assert _a_record(domain).full_name == "workspace.example.com"


def test_soa_remains_at_the_physical_zone_apex():
    assert models.SOARecord(name="").get_name(_domain()) == "exordos.io"
