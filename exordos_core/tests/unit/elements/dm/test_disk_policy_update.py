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

from unittest import mock
import uuid

import pytest

from exordos_core.compute.dm import models as compute
from exordos_core.elements.dm import models as em


@pytest.mark.parametrize(
    "prefix,model",
    [("$core.compute.nodes", compute.Node), ("$core.compute.sets", compute.NodeSet)],
)
def test_em_rejects_policy_change_before_saving_target(prefix, model):
    spec = {"kind": "root_disk", "image": "https://example.com/root.raw", "size": 10}
    node = model.restore_from_simple_view(
        name="vm", project_id=uuid.uuid4(), cores=1, ram=512, disk_spec=spec
    )
    resource = em.Resource(
        uuid=node.uuid,
        name="vm",
        element=em.Element(name="core", version="1.0.0", link="$core"),
        resource_link_prefix=prefix,
        value={"disk_spec": spec},
    )
    with mock.patch.object(
        model, "objects", mock.Mock(get_one_or_none=mock.Mock(return_value=node))
    ):
        with pytest.raises(compute.ImmutableDiskPolicyError):
            resource.update_dm({"value": {"disk_spec": {**spec, "ephemeral": False}}})
        assert resource.value["disk_spec"] == spec
        resource.update_dm({"value": {"disk_spec": {**spec, "size": 12}}})
        assert resource.value["disk_spec"]["size"] == 12

        manifest = em.Manifest(
            name="core",
            version="1.0.0",
            resources={prefix: {"vm": {"disk_spec": {**spec, "ephemeral": False}}}},
        )
        with (
            mock.patch.object(
                em.Resource,
                "objects",
                mock.Mock(get_all=mock.Mock(return_value=[resource])),
            ),
            mock.patch.object(
                em, "LinkResolver", return_value=mock.Mock(full_link_original=prefix)
            ),
            mock.patch.object(em.Resource, "save") as save,
        ):
            with pytest.raises(compute.ImmutableDiskPolicyError):
                manifest.apply_resources(resource.element)
            save.assert_not_called()
            assert resource.value["disk_spec"]["size"] == 12
