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
import uuid as sys_uuid

from gcl_sdk.paas.services import builder as paas_builder
import pytest

from exordos_core.network.lb.builders import iaas as lb_iaas
from exordos_core.network.lb.builders import paas as lb_paas
from exordos_core.network.lb.dm import models as lb_models
from exordos_core.user_api.network.dm import models as network_models


def _external_node_lb(node):
    inst = mock.MagicMock()
    inst.uuid = sys_uuid.uuid4()
    inst.type.kind = "external_node"
    inst.type.external_node = node
    inst.get_vhosts.return_value = [{"uuid": "v1"}]
    inst.get_backend_pools.return_value = {"p1": {"endpoints": []}}
    return inst


def test_external_node_lb_is_pinned_to_the_node_agent():
    node = sys_uuid.uuid4()
    inst = _external_node_lb(node)

    res = lb_paas.LBBuilder().actualize_paas_objects(
        inst, paas_builder.PaaSCollection(paas_objects=tuple())
    )

    assert len(res) == 1
    (obj,) = res
    assert isinstance(obj, lb_models.PaasLBNode)
    # The LB uuid, so that several LBs can share one node.
    assert obj.uuid == inst.uuid
    assert obj.agent_uuid == node
    assert obj.vhosts == [{"uuid": "v1"}]
    assert obj.backend_pools == {"p1": {"endpoints": []}}


def test_external_node_lb_needs_no_infra():
    builder = lb_iaas.LBBuilder(lb_models.IaasLB, project_id=sys_uuid.uuid4())

    assert builder.create_infra(_external_node_lb(sys_uuid.uuid4())) == []


def test_switching_to_external_node_drops_the_old_vm_addresses():
    builder = lb_iaas.LBBuilder(lb_models.IaasLB, project_id=sys_uuid.uuid4())
    inst = _external_node_lb(sys_uuid.uuid4())
    inst.ipsv4 = ["10.0.0.5"]

    assert builder.actualize_infra(inst, mock.MagicMock()) == tuple()
    assert inst.ipsv4 == []


@mock.patch.object(lb_paas.repo_models.RepoArtifact, "objects")
def test_download_artifact_is_resolved_before_agent_delivery(objects):
    urn = f"urn:artifacts:{sys_uuid.uuid4()}"
    low = mock.Mock(uri="https://low.example/web.tar.zst")
    low.element.repository.priority = 1
    high = mock.Mock(uri="https://high.example/web.tar.zst")
    high.element.repository.priority = 10
    objects.get_all.return_value = [low, high]
    inst = _external_node_lb(sys_uuid.uuid4())
    inst.get_vhosts.return_value = [
        {
            "routes": {
                "r1": {
                    "cond": {
                        "actions": [
                            {"kind": "local_dir_download", "url": urn, "is_spa": True},
                            {
                                "kind": "local_dir_download",
                                "url": "https://example.com/web.tar.gz",
                            },
                        ]
                    }
                }
            }
        }
    ]

    (obj,) = lb_paas.LBBuilder().actualize_paas_objects(
        inst, paas_builder.PaaSCollection(paas_objects=tuple())
    )

    actions = obj.vhosts[0]["routes"]["r1"]["cond"]["actions"]
    assert actions[0] == {"kind": "local_dir_download", "url": high.uri, "is_spa": True}
    assert actions[1]["url"] == "https://example.com/web.tar.gz"
    objects.get_all.assert_called_once()
    assert objects.get_all.call_args.kwargs["filters"]["urn"].value == urn


@mock.patch.object(lb_paas.repo_models.RepoArtifact, "objects")
def test_missing_download_artifact_fails_reconciliation(objects):
    objects.get_all.return_value = []
    with pytest.raises(ValueError, match="Artifact for URN .* not found"):
        lb_paas.LBBuilder()._resolve_artifact(f"urn:artifacts:{sys_uuid.uuid4()}")


def test_download_url_accepts_artifact_urns_and_archive_urls():
    for value in [
        f"urn:artifacts:{sys_uuid.uuid4()}",
        "https://example.com/web.tar.zst",
        "https://example.com/web.tar.gz",
    ]:
        assert network_models.ArchivedTarUrl().validate(value)
        assert network_models.RuleStaticDownloadKind(url=value).url == value
    for value in [
        "urn:artifacts:invalid",
        f"urn:images:{sys_uuid.uuid4()}",
        "https://example.com/web.zip",
        "web.tar.gz",
        None,
    ]:
        assert not network_models.ArchivedTarUrl().validate(value)
