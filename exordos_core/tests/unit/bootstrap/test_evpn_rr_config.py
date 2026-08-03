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

import netaddr
from restalchemy.storage.sql import orm

from exordos_core.bootstrap import defaults
from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.compute.builders import pool as pool_builder
from exordos_core.compute.dm import models


def _spec():
    return {
        "stand": {
            # Naming a private network is what asks for an overlay at all,
            # and the reflector config exists to serve one.
            "private_network": {},
            "network": {"name": "flat", "cidr": "10.20.0.0/22"},
            "bootstraps": [
                {
                    "uuid": "5ded6d70-a989-4f88-8216-1c64274a1d6a",
                    "ports": [{"ip": "10.20.0.2", "mac": "52:54:00:82:4b:8f"}],
                }
            ],
        }
    }


def test_render_matches_live_reflector_config():
    # This is the exact [evpn] content proven to bring up the RR on a live
    # core (bgp_rr resource -> agent renders reflector gobgpd -> gobgpd up).
    assert defaults.render_evpn_rr_config(_spec()) == (
        "[evpn]\n"
        "rr_agent = 5ded6d70-a989-4f88-8216-1c64274a1d6a\n"
        "rr_addresses = 10.20.0.2\n"
        "rr_peer_prefixes = 10.20.0.0/22\n"
        "dns_forwarders = 10.20.0.2\n"
    )


def test_render_none_without_bootstraps():
    assert defaults.render_evpn_rr_config({"stand": {"network": {}}}) is None


def test_render_none_without_core_ip():
    spec = _spec()
    spec["stand"]["bootstraps"][0]["ports"] = [{"ip": None}]
    assert defaults.render_evpn_rr_config(spec) is None


def test_render_overrides_rr_address_and_peer_prefixes():
    # Multi-hypervisor realm: clients peer the RR through the control node's
    # flat address, and the RR accepts them from the flat network too.
    spec = _spec()
    spec["stand"]["evpn_rr_address"] = "10.20.0.23"
    spec["stand"]["evpn_peer_prefixes"] = ["10.20.0.0/22"]
    assert defaults.render_evpn_rr_config(spec) == (
        "[evpn]\n"
        "rr_agent = 5ded6d70-a989-4f88-8216-1c64274a1d6a\n"
        "rr_addresses = 10.20.0.23\n"
        "rr_peer_prefixes = 10.20.0.0/22\n"
        # dns_forwarders still points at the nested core's own resolver.
        "dns_forwarders = 10.20.0.2\n"
    )


def test_render_none_when_the_installation_asked_for_no_overlay():
    """No private network named, no route-reflector drop-in.

    The file is written on every bootstrap and restarts `ec-gservice`; upgrading
    an installation without an overlay must change nothing.
    """
    spec = _spec()
    del spec["stand"]["private_network"]

    assert defaults.render_evpn_rr_config(spec) is None


def test_bootstrap_keeps_the_real_port_through_ownership_validation():
    spec = _spec()
    bootstrap = spec["stand"]["bootstraps"][0]
    bootstrap.update(
        cores=1,
        memory=1024,
        disks=[{"size": 8, "label": "root"}],
        image_uri="https://example.com/core.raw.zst",
    )
    spec["stand"]["hypervisors"] = [{"machine_prefix": "vm-"}]
    network = models.Network(project_id=c.ZERO_UUID)
    subnet = models.Subnet(
        uuid=c.MAIN_SUBNET_UUID,
        network=network.uuid,
        project_id=c.ZERO_UUID,
        cidr=netaddr.IPNetwork("10.20.0.0/22"),
    )
    core_node = mock.MagicMock(project_id=nc.NODE_SET_PROJECT)

    def fetch(collection, *args, **kwargs):
        return {
            models.Subnet: subnet,
            models.Network: network,
            models.Node: core_node,
        }.get(collection.model_cls)

    inserted_ports = []

    def insert(instance, session=None):
        if isinstance(instance, models.Port):
            inserted_ports.append(instance)

    with (
        mock.patch.object(orm.ObjectCollection, "get_one_or_none", fetch),
        mock.patch.object(orm.ObjectCollection, "get_all", return_value=[]),
        mock.patch.object(orm.SQLStorableMixin, "insert", insert),
        mock.patch.object(defaults.node_set_models.NodeSet, "insert"),
        mock.patch.object(models.Node, "insert", autospec=True) as insert_node,
        mock.patch.object(defaults, "seed_core_set_target"),
    ):
        defaults.add_core_set(spec)

    assert len(inserted_ports) == 1
    port = inserted_ports[0]
    assert port.node == sys_uuid.UUID(bootstrap["uuid"])
    assert port.project_id == c.EM_PROJECT_ID
    assert port.provenance == nc.PortProvenance.GENERATED.value
    assert port.mac == bootstrap["ports"][0]["mac"]
    assert str(port.ipv4) == bootstrap["ports"][0]["ip"]
    node = insert_node.call_args.args[0]
    assert node.name.startswith("core-set-node")
    assert pool_builder.PoolBuilderService._is_core_machine(None, node)


def test_bootstrap_target_survives_unresolved_profile_values():
    """EM must retain a desired set before its profile references resolve."""
    node_set = defaults.node_set_models.NodeSet(
        uuid=c.CORE_SET_UUID,
        cores=1,
        ram=1024,
        project_id=c.EM_PROJECT_ID,
        disk_spec=defaults.infra_models.SetDisksSpec(
            disks=[{"size": 8, "image": "https://example.com/core.raw.zst"}]
        ),
    )
    resource = mock.Mock(target_resource=None, kind="em_core_compute_sets")
    with (
        mock.patch.object(defaults.element_models.Resource, "objects") as objects,
        mock.patch.object(defaults.ua_models.TargetResource, "insert") as insert,
    ):
        objects.get_one.return_value = resource
        defaults.seed_core_set_target(node_set)
    target = resource.target_resource
    assert target.kind == "em_core_compute_sets"
    assert target.value["cores"] == 1
    assert target.value["ram"] == 1024
    insert.assert_called_once_with()
    resource.update.assert_called_once_with()
    resource.render_target_state.side_effect = KeyError("value")
    defaults.element_models.Resource.actualize(resource)
    assert resource.target_resource is target
    # A second bootstrap must preserve the already rendered desired state.
    with (
        mock.patch.object(defaults.element_models.Resource, "objects") as objects,
        mock.patch.object(
            defaults.ua_models.TargetResourceMixin, "to_ua_resource"
        ) as convert,
    ):
        objects.get_one.return_value = resource
        defaults.seed_core_set_target(node_set)
    convert.assert_not_called()
