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

"""nginx authorizes the raw request URI but serves the normalized one, so
the project is taken from the raw URI only when no normalization could turn
it into another project's path."""

from unittest import mock
import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.repo import internal

PROJECT_A = sys_uuid.UUID("00000000-0000-4000-8000-00000000000a")
PROJECT_B = sys_uuid.UUID("00000000-0000-4000-8000-00000000000b")


@pytest.mark.parametrize(
    "uri",
    [
        f"/repo/{PROJECT_A}/",
        f"/repo/{PROJECT_A}/core/1.0.0/inventory.json",
        f"/repo/{PROJECT_A}/core/1.0.0/inventory.json?x=1",
    ],
)
def test_project_is_the_first_segment(uri):
    assert internal.parse_project_id(uri) == PROJECT_A


@pytest.mark.parametrize(
    "uri",
    [
        f"/repo/{PROJECT_A}/../{PROJECT_B}/x",
        f"/repo/{PROJECT_A}/%2e%2e/{PROJECT_B}/x",
        f"/repo/{PROJECT_A}%2F..%2F{PROJECT_B}/x",
        f"/repo/{PROJECT_A}/./x",
        f"/repo//{PROJECT_B}/x",
        f"/repo/{PROJECT_A}\\..\\{PROJECT_B}/x",
        f"/other/{PROJECT_A}/x",
        "/repo/not-a-uuid/x",
        "",
    ],
)
def test_anything_that_could_normalize_elsewhere_is_refused(uri):
    assert internal.parse_project_id(uri) is None


@pytest.fixture
def subnets():
    subnet = mock.MagicMock(cidr=netaddr.IPNetwork("10.20.0.0/22"))
    with mock.patch.object(
        internal.compute_models.Subnet, "objects", mock.MagicMock()
    ) as objects:
        objects.get_all.return_value = [subnet]
        yield


@pytest.mark.parametrize(
    "address, expected",
    [
        ("10.20.1.7", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("203.0.113.5", False),
        ("", False),
        ("not-an-ip", False),
    ],
)
def test_realm_address(subnets, address, expected):
    assert internal.is_realm_address(address) is expected


def test_repository_uuid_is_stable_per_project():
    assert internal.repository_uuid(PROJECT_A) == internal.repository_uuid(PROJECT_A)
    assert internal.repository_uuid(PROJECT_A) != internal.repository_uuid(PROJECT_B)
