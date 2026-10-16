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

"""What a port's `public` slot accepts."""

import uuid as sys_uuid

import pytest
from restalchemy.common import exceptions as ra_exc

from exordos_core.compute.dm import models as compute_models

CONFIG = compute_models.Port.properties.properties["config"].get_property_type()


def _config(public):
    return CONFIG.from_simple_type({"kind": "simple", "public": public})


@pytest.mark.parametrize(
    "public",
    [
        # An IP literal compiled to no floating address at all.
        {"address": "192.0.2.7"},
        {"floating_from": "192.0.2.0/24"},
        {"address": str(sys_uuid.uuid4()), "floating_from": str(sys_uuid.uuid4())},
        {"addr": str(sys_uuid.uuid4())},
    ],
)
def test_anything_but_one_reference_is_refused(public):
    with pytest.raises(ra_exc.RestAlchemyException):
        _config(public)


@pytest.mark.parametrize("key", ["address", "floating_from"])
def test_one_reference_is_accepted(key):
    ref = sys_uuid.uuid4()
    assert _config({key: str(ref)}).public == {key: ref}
