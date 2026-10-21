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

"""End-to-end test of the ovs_evpn stack across core and gcl_sdk.

The real CP driver runs through ``NetworkService`` against the migrated
database; the resulting ``ua_target_resources`` rows are fed to gcl_sdk's
``EvpnAgentCapabilityDriver``, which renders the evpn_connector and gobgpd
configs (only ``ovs-vsctl``/``systemctl``/``ip`` are mocked).
"""

import json
import os
import pathlib
import runpy
from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra.dm import models as sdk_models
import netaddr
from oslo_config import cfg
import pytest
from psycopg.errors import DivisionByZero
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_storage_exc
from restalchemy.storage.sql import utils as sql_utils

from exordos_core.bootstrap import defaults as bootstrap_defaults
from exordos_core.common import constants as c
from exordos_core.compute.dm import models
from exordos_core.network import service

# Importing the driver registers its [evpn] config options.
from exordos_core.network.driver import evpn as cp_evpn  # noqa: F401

# The agent driver ships in gcl_sdk, which this installation pins to a published
# release. Until one carries the SDN work a clean environment cannot import
# it at all — and a collection error reads as a broken suite rather than as a
# missing dependency. Skip with the remedy in the message; an editable
# checkout of gcl_sdk runs these in full.
dp_evpn = pytest.importorskip(
    "gcl_sdk.agents.universal.drivers.evpn",
    reason=(
        "needs a gcl_sdk that carries the evpn agent driver "
        "(unreleased; install gcl_sdk from a checkout to run this)"
    ),
)
dp_base = pytest.importorskip("gcl_sdk.agents.universal.drivers.evpn.base")
dp_sh = pytest.importorskip("gcl_sdk.agents.universal.drivers.evpn.sh")

CONF = cfg.CONF
RR = "10.77.0.10"


def _rendered(dp_model, source_ip):
    """What the host driver puts in front of gobgpd for this resource.

    The daemon's file is assembled on the host from one fragment per
    fabric role (a node can be both reflector and hypervisor); this is
    the fragment of the role under test.
    """
    return dp_model._render_global(source_ip) + dp_model._render_body(source_ip)


def _resources(kind, master=None):
    filters = {"kind": dm_filters.EQ(kind)}
    if master is not None:
        filters["master"] = dm_filters.EQ(master)
    return ua_models.TargetResource.objects.get_all(filters=filters)


@pytest.mark.usefixtures("user_api_client", "auth_user_admin")
class _EvpnStand:
    """The stand both suites are written against: a core, a network, a
    hypervisor and a guest on it. Shared as a base without tests of its
    own — a suite that inherited another's would run it twice."""

    def setup_method(self):
        self._service = service.NetworkService()
        CONF.set_override("rr_addresses", [RR], group="evpn")
        self._created = []

    def teardown_method(self):
        CONF.clear_override("rr_addresses", group="evpn")
        for obj in reversed(self._created):
            try:
                obj.delete()
            except Exception:
                pass
        for kind in ("evpn_port", "evpn_host", "evpn_subnet"):
            for res in _resources(kind):
                try:
                    res.delete()
                except Exception:
                    pass

    def _node(self, name="evpn-hyp"):
        node = models.Node(
            uuid=sys_uuid.uuid4(),
            name=name,
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=c.ZERO_UUID,
        )
        node.insert()
        self._created.append(node)
        # The node's universal agent (target resources are scheduled to
        # its uuid; a real hypervisor registers this on boot).
        agent = ua_models.UniversalAgent(
            uuid=node.uuid,
            name="evpn-hyp-agent",
            node=node.uuid,
            capabilities={"evpn_node": ["evpn_port", "evpn_host"]},
        )
        agent.insert()
        self._created.append(agent)
        return node

    def _network(self):
        network = models.Network(
            name="evpn-net",
            driver_spec={"driver": "ovs_evpn"},
            project_id=c.ZERO_UUID,
        )
        network.insert()
        self._created.append(network)
        subnet = models.Subnet(
            network=network.uuid,
            cidr=netaddr.IPNetwork("10.42.0.0/24"),
            project_id=c.ZERO_UUID,
            dns_servers=["10.42.0.254"],
            routers=[
                {
                    "to": netaddr.IPNetwork("0.0.0.0/0"),
                    "via": netaddr.IPAddress("10.42.0.254"),
                }
            ],
        )
        subnet.insert()
        self._created.append(subnet)
        return network, subnet

    def _flat_network(self):
        network = models.Network(
            name="flat-net",
            driver_spec={"driver": "flat_bridge"},
            project_id=c.ZERO_UUID,
        )
        network.insert()
        self._created.append(network)
        subnet = models.Subnet(
            network=network.uuid,
            cidr=netaddr.IPNetwork("10.99.0.0/24"),
            project_id=c.ZERO_UUID,
        )
        subnet.insert()
        self._created.append(subnet)
        return network, subnet

    def _floating_pool(self, cidr):
        network = models.Network(
            name="public-net",
            driver_spec={"driver": "flat_bridge"},
            project_id=c.ZERO_UUID,
        )
        network.insert()
        self._created.append(network)
        subnet = models.Subnet(
            network=network.uuid,
            cidr=netaddr.IPNetwork(cidr),
            project_id=c.ZERO_UUID,
            placeable=False,
            floating_pool=True,
        )
        subnet.insert()
        self._created.append(subnet)
        return subnet

    def _pinned_node(self, network_uuid, name):
        node = models.Node(
            uuid=sys_uuid.uuid4(),
            name=name,
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=c.ZERO_UUID,
            default_network={"network": str(network_uuid)},
        )
        node.insert()
        self._created.append(node)
        agent = ua_models.UniversalAgent(
            uuid=node.uuid,
            name=name + "-agent",
            node=node.uuid,
            capabilities={"evpn_node": ["evpn_port", "evpn_host"]},
        )
        agent.insert()
        self._created.append(agent)
        return node

    def _pool(self, hypervisor_node=None):
        pool = models.MachinePool(
            name="evpn-pool",
            hypervisor_node=hypervisor_node,
            driver_spec=ua_pool.DummyPoolDriverSpec(),
        )
        pool.insert()
        self._created.append(pool)
        return pool

    def _machine(self, node, pool):
        machine = models.Machine(
            name=node.name + "-machine",
            project_id=c.ZERO_UUID,
            cores=node.cores,
            ram=node.ram,
            node=node.uuid,
            pool=pool.uuid,
        )
        machine.insert()
        self._created.append(machine)
        return machine

    def _port(self, subnet, node):
        port = models.Port(
            subnet=subnet.uuid,
            node=node.uuid,
            project_id=subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)
        return port

    def _security_group(self, name, rules):
        from exordos_core.user_api.network.dm import models as catalog

        group = catalog.SecurityGroup(name=name, project_id=c.ZERO_UUID)
        group.insert()
        self._created.append(group)
        for value in rules:
            rule = catalog.SecurityGroupRule(
                security_group=group.uuid, project_id=c.ZERO_UUID, **value
            )
            rule.insert()
            self._created.append(rule)
        return group

    def _bind_sg(self, port, group):
        from exordos_core.user_api.network.dm import models as catalog

        binding = catalog.PortSecurityGroup(
            project_id=port.project_id, port=port.uuid, security_group=group.uuid
        )
        binding.insert()
        self._created.append(binding)

    def _selective_load(self):
        """Patch context: real driver for ovs_evpn, no-op for the rest."""
        orig_load = models.Network.load_driver

        def selective_load(net):
            if net.driver_spec.get("driver") == "ovs_evpn":
                return orig_load(net)
            noop = mock.MagicMock()
            noop.list_subnets.return_value = []
            noop.list_ports.return_value = []
            return noop

        return mock.patch.object(models.Network, "load_driver", selective_load)

    # --- the tests -----------------------------------------------------

    def test_two_nodes_land_on_pinned_ovs_evpn_network(self):
        # Two networks coexist: the flat management one and an ovs_evpn
        # private one. Two nodes pinned to the private network must both be
        # allocated ports there (exercising per-node network selection),
        # not on the flat network.
        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        # The guests' machines live on a pool mapped to a hypervisor node
        # whose agent owns the wiring (the fabric lives on the hypervisor).
        # The hypervisor itself sits on the flat management network.
        hyp = self._pinned_node(flat_net.uuid, "hyp")
        pool = self._pool(hypervisor_node=hyp.uuid)
        n1 = self._pinned_node(evpn_net.uuid, "guest-1")
        n2 = self._pinned_node(evpn_net.uuid, "guest-2")
        self._machine(n1, pool)
        self._machine(n2, pool)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        # Both nodes got a port on the ovs_evpn subnet (not the flat one).
        ports = models.Port.objects.get_all(
            filters={"subnet": dm_filters.EQ(str(evpn_subnet.uuid))}
        )
        assert {str(p.node) for p in ports} == {str(n1.uuid), str(n2.uuid)}, (
            "both pinned nodes must land on the ovs_evpn subnet"
        )
        # Machine creation gates on ACTIVE ports, so an emitted evpn_port
        # must activate its port (regression: guests never provisioned).
        assert {p.status for p in ports} == {"ACTIVE"}

        # One evpn_port target resource per node, both scheduled to the
        # hypervisor node's agent (not to the guests themselves).
        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        assert len(eports) == 2
        assert {str(r.agent) for r in eports} == {str(hyp.uuid)}
        # ... and one evpn_host, for the hypervisor.
        ehosts = _resources("evpn_host")
        assert {str(r.uuid) for r in ehosts} == {str(hyp.uuid)}
        # Distinct overlay IPs, both from the private subnet's CIDR.
        ips = {netaddr.IPAddress(r.value["ipv4"]) for r in eports}
        assert len(ips) == 2
        assert all(ip in evpn_subnet.cidr for ip in ips)
        # Same VNI/RT for both (one private network, one VRF).
        assert len({r.value["vni"] for r in eports}) == 1

    def test_port_toggles_switch_a_network_function_off(self):
        # Port's dhcp/dns toggles are the friendly surface of
        # "this guest manages its own addressing": they must reach the host
        # as a *missing function*, not as a flag the agent may ignore.
        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-toggle")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-toggle")
        self._machine(guest, pool)

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            dhcp=False,
        )
        port.insert()
        self._created.append(port)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        kinds = [nf["kind"] for nf in ep.value["nfs"]]
        assert "dhcp" not in kinds
        # ... while the functions it did not switch off are still there
        assert "dns" in kinds and "proxy" in kinds

    def test_subnet_without_dhcp_leases_nothing(self):
        # `Subnet.dhcp` is the switch that already existed: the overlay used
        # to ignore it and lease anyway.
        evpn_net, evpn_subnet = self._network()
        evpn_subnet.dhcp = False
        evpn_subnet.update()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-bare")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-bare")
        self._machine(guest, pool)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        assert eports
        assert all("dhcp" not in [nf["kind"] for nf in r.value["nfs"]] for r in eports)

    def test_security_groups_compile_into_evpn_port(self):
        # Standalone SG rules and a many-to-many binding reach the host ACL.

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-sg")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-sg")
        self._machine(guest, pool)

        sg = self._security_group(
            "web-sg",
            [
                {"direction": "ingress", "protocol": "tcp", "port": 443},
                {"direction": "ingress", "protocol": "tcp", "port": 22},
            ],
        )

        # A port carrying the simple kind referencing the SG — exactly what the
        # user-facing API creates.
        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)
        self._bind_sg(port, sg)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        rules = ep.value.get("security_rules")
        assert {(r["proto"], r.get("port")) for r in rules} == {
            ("tcp", 443),
            ("tcp", 22),
        }

    def test_editing_a_group_reaches_a_port_that_already_exists(self):
        # Everything a port references is edited *after* the port exists —
        # tightening a rule after an incident, attaching a group, switching a
        # function off. None of that touches the port's own fields, so a
        # compiler that only runs at creation would leave the change sitting
        # in the database with no path to a hypervisor.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-edit")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-edit")
        self._machine(guest, pool)

        sg = self._security_group(
            "edited-sg",
            [
                {"direction": "ingress", "protocol": "tcp", "port": 443},
            ],
        )

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)
        self._bind_sg(port, sg)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        def _rules():
            eports = _resources("evpn_port", master=evpn_subnet.uuid)
            res = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
            return {(r["proto"], r.get("port")) for r in res.value["security_rules"]}

        assert _rules() == {("tcp", 443)}

        # the operator narrows the group, and switches the subnet's DHCP off
        # (through a freshly loaded row, as the API does)
        rule = net_api_models.SecurityGroupRule.objects.get_one(
            filters={"security_group": dm_filters.EQ(sg.uuid)}
        )
        rule.port = 22
        rule.update()
        stored_subnet = models.Subnet.objects.get_one(
            filters={"uuid": dm_filters.EQ(evpn_subnet.uuid)}
        )
        stored_subnet.dhcp = False
        stored_subnet.update()

        with self._selective_load():
            self._service._iteration()

        assert _rules() == {("tcp", 22)}
        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        assert "dhcp" not in [nf["kind"] for nf in ep.value["nfs"]]
        # the host resource says nothing about functions at all: the guests
        # that need the responder start and configure it themselves
        hosts = [h for h in _resources("evpn_host") if str(h.uuid) == str(hyp.uuid)]
        assert hosts and "nfs" not in hosts[0].value

        binding = net_api_models.PortSecurityGroup.objects.get_one(
            filters={"port": dm_filters.EQ(port.uuid)}
        )
        binding.delete()
        with self._selective_load():
            self._service._iteration()
        assert _rules() == set()

        self._bind_sg(port, sg)
        with self._selective_load():
            self._service._iteration()
        assert _rules() == {("tcp", 22)}

    def test_floating_ip_compiles_into_evpn_port(self):
        # A floating Address owns the Port reference and reaches host NAT.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-fip")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-fip")
        self._machine(guest, pool)
        pub_subnet = self._floating_pool("203.0.115.0/24")

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)

        addr = net_api_models.Address(
            uuid=sys_uuid.uuid4(),
            project_id=c.ZERO_UUID,
            subnet=pub_subnet.uuid,
            address="203.0.115.207",
            kind="floating",
            port=port.uuid,
        )
        addr.insert()
        self._created.append(addr)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        assert ep.value.get("fips") == [{"public": "203.0.115.207"}]

    def test_owned_address_is_allocated_once_and_compiled(self):
        from exordos_core.user_api.network.dm import models as catalog

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-ff")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-ff")
        self._machine(guest, pool)

        pub_subnet = self._floating_pool("203.0.113.0/24")

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)
        catalog.Address(
            subnet=pub_subnet.uuid,
            project_id=port.project_id,
            kind="floating",
            port=port.uuid,
            owner_port=port.uuid,
        ).insert()

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            eports = _resources("evpn_port", master=evpn_subnet.uuid)
            ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
            fips = ep.value.get("fips")
            assert len(fips) == 1
            assert netaddr.IPAddress(fips[0]["public"]) in pub_subnet.cidr
            allocated_ip = fips[0]["public"]
            # Idempotent: a further reconcile reuses the same floating IP.
            self._service._iteration()
            eports = _resources("evpn_port", master=evpn_subnet.uuid)
            ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
            assert ep.value.get("fips") == [{"public": allocated_ip}]

    def test_deleting_a_port_releases_its_owned_address_after_ack(self):
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-ffdel")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-ffdel")
        self._machine(guest, pool)

        pub_subnet = self._floating_pool("203.0.114.0/24")

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)
        net_api_models.Address(
            subnet=pub_subnet.uuid,
            project_id=port.project_id,
            kind="floating",
            port=port.uuid,
            owner_port=port.uuid,
        ).insert()

        def _owned():
            return net_api_models.Address.objects.get_all(
                filters=dm_filters.OR(
                    {"owner_port": dm_filters.EQ(port.uuid)},
                    {"association": dm_filters.EQ(port.uuid)},
                )
            )

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            assert len(_owned()) == 1  # the floating address was allocated

            driver = evpn_net.load_driver()
            port.delete()
            driver.delete_port(port)
            assert _owned() == []
            assert _owned() == []

    @pytest.mark.parametrize("owned_fip", [False, True])
    def test_node_owned_networking_releases_without_host_withdrawal(self, owned_fip):
        from exordos_core.user_api.network.dm import models as catalog

        network, subnet = self._network()
        flat, _ = self._flat_network()
        hyp = self._pinned_node(flat.uuid, "ownership-hyp")
        pool = self._pool(hypervisor_node=hyp.uuid)
        public = self._floating_pool("203.0.119.0/24")
        request = {"subnet": str(subnet.uuid)}
        if owned_fip:
            request.update(
                auto_floating_ip=True,
                floating_network=str(getattr(public.network, "uuid", public.network)),
            )
        node = models.Node(
            name="ownership-guest",
            project_id=c.ZERO_UUID,
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            default_network=request,
        )
        node.insert()
        self._created.append(node)
        self._machine(node, pool)
        port = models.Port.objects.get_one(filters={"node": dm_filters.EQ(node.uuid)})
        if not owned_fip:
            address = catalog.Address(
                project_id=c.ZERO_UUID,
                subnet=public.uuid,
                kind="floating",
                port=port.uuid,
            )
            address.insert()
            self._created.append(address)
        group = self._security_group("ownership-sg", [])
        self._bind_sg(port, group)

        def address_row():
            return catalog.Address.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(address.uuid)}
            )

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            address = catalog.Address.objects.get_one(
                filters={"port": dm_filters.EQ(port.uuid)}
            )
            assert address.association == port.uuid
            target = _resources("evpn_port", master=subnet.uuid)[0]
            assert not {"owner_node", "owner_port", "provenance"} & target.value.keys()
            node.delete()
            assert (
                models.Port.objects.get_one_or_none(
                    filters={"uuid": dm_filters.EQ(port.uuid)}
                )
                is None
            )
            assert (
                catalog.PortSecurityGroup.objects.get_all(
                    filters={"port": dm_filters.EQ(port.uuid)}
                )
                == []
            )
            assert catalog.SecurityGroup.objects.get_one(
                filters={"uuid": dm_filters.EQ(group.uuid)}
            )
            driver = network.load_driver()
            driver.delete_port(port)
            if owned_fip:
                assert address_row() is None
            else:
                assert address_row().port is None and address_row().association is None

    def test_a_cited_address_is_marked_used_and_given_back(self):
        # Unbind releases publication without a report from the old host.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-assoc")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-assoc")
        self._machine(guest, pool)
        pub_subnet = self._floating_pool("203.0.116.0/24")

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
        )
        port.insert()
        self._created.append(port)

        addr = net_api_models.Address(
            uuid=sys_uuid.uuid4(),
            project_id=c.ZERO_UUID,
            subnet=pub_subnet.uuid,
            address="203.0.116.215",
            kind="floating",
            port=port.uuid,
        )
        addr.insert()
        self._created.append(addr)

        def _stored():
            return net_api_models.Address.objects.get_one(
                filters={"uuid": dm_filters.EQ(addr.uuid)}
            )

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            assert str(_stored().association) == str(port.uuid)

            # A move needs an explicit unbind and a current-host withdrawal ack.
            other = self._pinned_node(evpn_net.uuid, "guest-assoc-2")
            self._machine(other, pool)
            second = self._port(evpn_subnet, other)
            stored = _stored()
            stored.port = second.uuid
            with pytest.raises(ra_storage_exc.ConflictRecords):
                stored.update()
            stored.port = None
            stored.update()
            driver = evpn_net.load_driver()
            driver.update_port(port)
            assert _stored().association is None
            released = _stored()
            assert released.association is None
            released.port = second.uuid
            released.update()

    def test_a_host_that_lost_its_guests_loses_their_function_slices(self):
        # A slice is what the host answers a guest with, and its arrival is
        # what makes the host serve that VNI at all. Left behind after the
        # last guest, it made the responder keep a network the host has
        # nothing on — entering a namespace nothing created, failing, and
        # restarting for as long as the slice stayed.
        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-collect")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-collect")
        self._machine(guest, pool)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        assert _resources("evpn_nf", master=evpn_subnet.uuid)
        port = models.Port.objects.get_all(filters={"node": dm_filters.EQ(guest.uuid)})[
            0
        ]

        with self._selective_load():
            evpn_net.load_driver().delete_port(port)

        assert _resources("evpn_nf", master=evpn_subnet.uuid) == []

    def test_the_services_of_a_network_are_seeded_as_objects(self):
        # DHCP, the resolver and the proxy are generated NFs owned by the subnet
        # (dhcp) and the network (dns, proxy); the compiler reads them from there.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-svc")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-svc")
        self._machine(guest, pool)

        with self._selective_load():
            # the port appears on the first pass and is compiled on the next
            self._service._iteration()
            self._service._iteration()

        seeded = {
            nf.kind: nf
            for nf in net_api_models.NetworkFunction.objects.get_all()
            if nf.kind in ("dhcp", "dns", "proxy")
        }
        assert sorted(seeded) == ["dhcp", "dns", "proxy"]
        assert str(seeded["dhcp"].owner_subnet) == str(evpn_subnet.uuid)
        assert str(seeded["dns"].owner_network) == str(evpn_net.uuid)
        assert str(seeded["proxy"].owner_network) == str(evpn_net.uuid)
        for nf in seeded.values():
            self._created.append(nf)

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        assert eports
        kinds = [nf["kind"] for nf in eports[0].value["nfs"]]
        assert kinds == ["dhcp", "dns", "proxy"]
        port_hash_before = eports[0].hash

        # An edit of the object reaches the host on the next compile — and
        # seeding never overwrites it back.
        seeded["dns"].config = {"forwarders": ["9.9.9.9"], "zone_suffix": "corp"}
        seeded["dns"].update()
        with self._selective_load():
            self._service._iteration()

        # The port still merely names the function — what changed is the
        # function's own slice on the host, which is the point: an edited
        # resolver moves one small resource and re-hashes no port.
        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        dns_slices = [
            r
            for r in _resources("evpn_nf", master=evpn_subnet.uuid)
            if r.value["kind"] == "dns"
        ]
        assert dns_slices
        assert dns_slices[0].value["config"]["forwarders"] == ["9.9.9.9"]
        assert dns_slices[0].value["config"]["zone_suffix"] == "corp"
        assert dns_slices[0].value["config"]["platform_names"]
        # ... and the ports were left exactly as they were: an edited
        # function must not disturb a guest that is already being served
        assert [nf["kind"] for nf in eports[0].value["nfs"]] == [
            "dhcp",
            "dns",
            "proxy",
        ]
        assert eports[0].hash == port_hash_before
        assert net_api_models.NetworkFunction.objects.get_one(
            filters={"uuid": dm_filters.EQ(seeded["dns"].uuid)}
        ).config["forwarders"] == ["9.9.9.9"]

        CONF.set_override("dns_zone_suffix", "fresh", "evpn")
        CONF.set_override("dns_forwarders", ["8.8.8.8"], "evpn")
        try:
            with self._selective_load():
                self._service._iteration()
            dns = [
                r
                for r in _resources("evpn_nf", master=evpn_subnet.uuid)
                if r.value["kind"] == "dns"
            ][0]
            assert dns.value["config"]["zone_suffix"] == "corp"
            assert dns.value["config"]["forwarders"] == ["9.9.9.9"]
            seeded["dns"].config = {}
            seeded["dns"].update()
            with self._selective_load():
                self._service._iteration()
            dns = [
                r
                for r in _resources("evpn_nf", master=evpn_subnet.uuid)
                if r.value["kind"] == "dns"
            ][0]
            assert dns.value["config"]["zone_suffix"] == "fresh"
            assert dns.value["config"]["forwarders"] == ["8.8.8.8"]
            assert (
                _resources("evpn_port", master=evpn_subnet.uuid)[0].hash
                == port_hash_before
            )
            assert (
                net_api_models.NetworkFunction.objects.get_one(
                    filters={"uuid": dm_filters.EQ(seeded["dns"].uuid)}
                ).config
                == {}
            )
        finally:
            CONF.clear_override("dns_zone_suffix", "evpn")
            CONF.clear_override("dns_forwarders", "evpn")

    def test_a_guest_without_sg_is_unfiltered_and_unidentified(self):
        from exordos_core.user_api.network.dm import models as catalog

        network, subnet = self._network()
        flat, _ = self._flat_network()
        hyp = self._pinned_node(flat.uuid, "hyp-def")
        guest = self._pinned_node(network.uuid, "guest-def")
        self._machine(guest, self._pool(hypervisor_node=hyp.uuid))
        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
        assert not catalog.SecurityGroup.objects.get_all()
        target = _resources("evpn_port", master=subnet.uuid)[0]
        assert target.value["security_rules"] == []
        assert target.value["identity"] == 0

    def test_legacy_flat_project_keeps_new_nodes_off_overlay(self):
        self._network()
        _, flat_subnet = self._flat_network()
        previous = models.Port(subnet=flat_subnet.uuid, project_id=c.ZERO_UUID)
        previous.insert()
        self._created.append(previous)
        node = self._node()

        with self._selective_load():
            self._service._iteration()

        ports = models.Port.objects.get_all(
            filters={"node": dm_filters.EQ(str(node.uuid))}
        )
        for p in ports:
            self._created.append(p)
        assert len(ports) == 1
        port_subnet = getattr(ports[0].subnet, "uuid", ports[0].subnet)
        assert str(port_subnet) == str(flat_subnet.uuid)

    def test_dns_zone_crosses_hosts_and_tracks_network_lifecycle(self, tmp_path):
        import socket
        import struct

        from gcl_sdk.agents.universal.drivers import evpn_nf
        from gcl_sdk.agents.universal.drivers import evpn_proxy
        from gcl_sdk.agents.universal.drivers.evpn import base as dp_base
        from exordos_core.user_api.network.dm import models as catalog

        network, subnet = self._network()
        other_network, other_subnet = self._network()
        flat, _ = self._flat_network()
        hosts = [
            self._pinned_node(flat.uuid, name) for name in ("hyp-alpha", "hyp-beta")
        ]
        pools = [self._pool(hypervisor_node=host.uuid) for host in hosts]
        alpha = self._pinned_node(network.uuid, "alpha")
        beta = self._pinned_node(network.uuid, "beta")
        outsider = self._pinned_node(other_network.uuid, "outsider")
        for guest, pool in ((alpha, pools[0]), (beta, pools[1]), (outsider, pools[1])):
            self._machine(guest, pool)
        beta_port = models.Port.objects.get_one(
            filters={"node": dm_filters.EQ(beta.uuid)}
        )

        def targets(net):
            return [
                res
                for res in _resources("evpn_nf")
                if res.value["kind"] == "dns"
                and res.value["vni"] == net.driver_state["vni"]
            ]

        def records(net):
            return [res.value["config"]["records"] for res in targets(net)]

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            dns = catalog.NetworkFunction.objects.get_one(
                filters={
                    "kind": dm_filters.EQ("dns"),
                    "owner_network": dm_filters.EQ(network.uuid),
                }
            )
            dns.config = {"zone_suffix": "corp"}
            dns.update()
            self._service._iteration()
            expected = [
                {"name": "alpha", "ipv4": "10.42.0.1"},
                {"name": "beta", "ipv4": "10.42.0.2"},
            ]
            assert records(network) == [expected, expected]
            assert records(other_network) == [
                [{"name": "outsider", "ipv4": "10.42.0.1"}]
            ]
            for target in targets(network):
                root = tmp_path / str(target.agent)
                with mock.patch.object(dp_base, "NF_RECORDS_DIR", str(root)):
                    dp_evpn.EvpnNf.from_ua_resource(target).dump_to_dp()
                    for port in _resources("evpn_port"):
                        if (
                            port.agent == target.agent
                            and port.value["vni"] == target.value["vni"]
                        ):
                            local = dp_evpn.EvpnPort.from_ua_resource(port)
                            local._write_nf_record({nf["kind"]: {} for nf in local.nfs})
                directory = str(root / str(target.value["vni"]))
                cache = evpn_nf.RecordCache(directory)
                cache.refresh()
                assert len(cache.by_mac) == 1
                assert evpn_nf.resolve_internal(cache.zone, "outsider.corp", 1) is None
                local = next(iter(cache.by_mac.values()))
                remote = beta if local["node"] == str(alpha.uuid) else alpha
                remote_name, remote_ip = (
                    ("beta", "10.42.0.2") if remote is beta else ("alpha", "10.42.0.1")
                )
                assert evpn_proxy.caller_uuid(directory, remote_ip) is None
                query = (
                    struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0)
                    + evpn_nf._encode_name(remote_name + ".corp")
                    + struct.pack("!HH", 1, 1)
                )
                mac = bytes.fromhex(local["mac"].replace(":", ""))
                frame = evpn_nf._eth(
                    evpn_nf.RESPONDER_MAC,
                    mac,
                    0x0800,
                    evpn_nf._ipv4_udp(local["ipv4"], "10.42.0.254", 5000, 53, query),
                )
                reply = evpn_nf.handle_dns(
                    frame, 14, mac, cache.by_mac, cache.zone, [], pool=None
                )
                assert reply is not None and reply[-4:] == socket.inet_aton(remote_ip)
            beta.name = "renamed"
            beta.update()
            self._service._iteration()
            renamed = [expected[0], {"name": "renamed", "ipv4": "10.42.0.2"}]
            assert records(network) == [renamed, renamed]
            beta_port.dns = False
            beta_port.update()
            self._service._iteration()
            assert records(network) == [[expected[0]], [expected[0]]]
            beta_port.dns = True
            beta_port.update()
            self._service._iteration()
            assert records(network) == [renamed, renamed]
            beta_port.delete()
            self._service._iteration()
            assert records(network) == [[expected[0]]]
            assert records(other_network) == [
                [{"name": "outsider", "ipv4": "10.42.0.1"}]
            ]
            orphan = targets(network)[0]
            models.Port.objects.get_one(
                filters={"node": dm_filters.EQ(alpha.uuid)}
            ).delete()
            self._service._iteration()
            assert not targets(network)
            # Older loops also left slices on idle hosts with live owners.
            orphan.insert()
            self._service._iteration()
            assert not targets(network)
            subnet.delete()
            network.delete()
            # An upgrade must collect old leftovers even after their owners
            # have disappeared, without touching another network's slices.
            orphan.insert()
            self._service._iteration()
            assert orphan.uuid not in {target.uuid for target in _resources("evpn_nf")}
            assert records(other_network) == [
                [{"name": "outsider", "ipv4": "10.42.0.1"}]
            ]


class TestOvsEvpnE2E(_EvpnStand):
    @pytest.mark.parametrize("overlay", [False, True])
    def test_inline_port_sets_flat_source(self, overlay):
        _, subnet = self._network() if overlay else self._flat_network()
        subnet.name = "eno1"
        subnet.update()
        node = models.Node(
            name="inline-port-source",
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=c.ZERO_UUID,
            default_network={"subnet": str(subnet.uuid)},
        )
        node.insert()
        port = models.Port.objects.get_one(filters={"node": dm_filters.EQ(node.uuid)})
        self._created.extend([port, node])
        assert port.source == (None if overlay else subnet.name)

    @pytest.mark.parametrize(
        "overlay, source", [(False, None), (False, "custom-network"), (True, None)]
    )
    def test_flat_source_migration_preserves_other_ports(self, overlay, source):
        network, subnet = self._network() if overlay else self._flat_network()
        subnet.name = "eno1"
        subnet.update()
        node = self._pinned_node(network.uuid, "source-migration")
        pool = self._pool()
        machine = self._machine(node, pool)
        port = self._port(subnet, node)
        machine = models.Machine.objects.get_one(
            filters={"uuid": dm_filters.EQ(machine.uuid)}
        )
        migration = runpy.run_path(
            str(
                pathlib.Path(__file__).parents[4]
                / "migrations/0010-network-sdn-9e41c7.py"
            )
        )["migration_step"]
        with contexts.Context().session_manager() as session:
            session.execute(
                "UPDATE compute_ports SET source = %s WHERE uuid = %s",
                (source, str(port.uuid)),
            )
            migration.repair_flat_sources(session)
        repaired = models.Port.objects.get_one(
            filters={"uuid": dm_filters.EQ(port.uuid)}
        )
        refreshed = models.Machine.objects.get_one(
            filters={"uuid": dm_filters.EQ(machine.uuid)}
        )
        expected = subnet.name if not overlay and source is None else source
        assert repaired.source == expected
        assert repaired.uuid == port.uuid
        assert repaired.mac == port.mac
        assert repaired.ipv4 == port.ipv4
        assert (refreshed.updated_at > machine.updated_at) == (
            not overlay and source is None
        )
        with contexts.Context().session_manager() as session:
            migration.repair_flat_sources(session)
        assert (
            models.Machine.objects.get_one(
                filters={"uuid": dm_filters.EQ(machine.uuid)}
            ).updated_at
            == refreshed.updated_at
        )

    def test_binding_projection_sql_error_preserves_transaction(self):
        from exordos_core.user_api.network.dm import models as catalog

        _, subnet = self._network()
        port = models.Port(
            uuid=sys_uuid.uuid4(), subnet=subnet.uuid, project_id=c.ZERO_UUID
        )
        with contexts.Context().session_manager() as session:
            with mock.patch.object(catalog.PortSecurityGroup, "objects") as failed:
                failed.get_all.side_effect = lambda **kwargs: session.execute(
                    "SELECT 1 / 0"
                )
                with pytest.raises(DivisionByZero):
                    with sql_utils.savepoint():
                        models.Port.project_observations([port], session=session)
            assert session.execute("SELECT 42 AS answer").fetchone()["answer"] == 42

    @pytest.mark.parametrize("read", ["nodes", "addresses"])
    def test_comparison_prefetch_sql_error_preserves_transaction(self, read):
        network, subnet = self._network()
        driver = network.load_driver()
        port = mock.Mock(uuid=sys_uuid.uuid4(), node=sys_uuid.uuid4())
        port.name = ""
        owner = models.Node if read == "nodes" else driver
        method = "objects" if read == "nodes" else "_comparison_addresses"
        with contexts.Context().session_manager() as session:
            with mock.patch.object(owner, method) as failed:
                query = failed.get_all if read == "nodes" else failed
                query.side_effect = lambda *a, **kw: session.execute("SELECT 1 / 0")
                with driver.compare_ports(subnet, [port]):
                    assert (
                        session.execute("SELECT 42 AS answer").fetchone()["answer"]
                        == 42
                    )

    @pytest.mark.parametrize("resource", ["host", "nf"])
    def test_deferred_sql_error_preserves_other_writes(self, resource):
        network, _ = self._network()
        driver = network.load_driver()
        created = []
        with contexts.Context().session_manager() as session:
            before = self._node("before-flush")

            def write(*args, **kwargs):
                if not created:
                    created.append(None)
                    session.execute("SELECT 1 / 0")
                else:
                    created.append(self._node("after-flush"))

            with mock.patch.object(
                driver,
                "_write_host" if resource == "host" else "_write_resource",
                side_effect=write,
            ):
                with driver.reconcile():
                    if resource == "host":
                        driver._ensure_host(sys_uuid.uuid4())
                        driver._ensure_host(sys_uuid.uuid4())
                    else:
                        cp_evpn._RECONCILE_READS.get()["nf-targets"] = {
                            str(i): (mock.Mock(uuid=sys_uuid.uuid4()), network.uuid)
                            for i in range(2)
                        }
        assert len(created) == 2
        for node in (before, created[1]):
            assert models.Node.objects.get_one(
                filters={"uuid": dm_filters.EQ(node.uuid)}
            )

    def test_cp_computes_targets_then_dp_renders_them(self, tmp_path):
        node = self._node()
        # The guest's hypervisor is what wires it: its bridge is on the
        # host, and an agent inside the guest has nothing to put a patch on.
        hyp = self._node(name="hyp-e2e")
        self._machine(node, self._pool(hypervisor_node=hyp.uuid))
        network, subnet = self._network()
        port = self._port(subnet, node)

        # --- CP: real ovs_evpn driver through the reconcile loop -------
        # The functional DB carries a seeded flat_bridge network; keep the
        # real driver for our ovs_evpn network only and no-op the rest so
        # the test does not touch the host's real dhcpd.
        orig_load = models.Network.load_driver

        def selective_load(net):
            if net.driver_spec.get("driver") == "ovs_evpn":
                return orig_load(net)
            noop = mock.MagicMock()
            noop.list_subnets.return_value = []
            noop.list_ports.return_value = []
            return noop

        with mock.patch.object(models.Network, "load_driver", selective_load):
            # First loop creates the evpn_subnet marker; the reconcile
            # design only actualizes ports once the subnet is "actual",
            # so ports land on the second loop.
            self._service._iteration()
            self._service._iteration()

        # Allocated driver state remains separate from the desired spec.
        network = models.Network.objects.get_one(
            filters={"uuid": dm_filters.EQ(str(network.uuid))}
        )
        spec = network.driver_state
        assert spec["vni"] >= CONF.evpn.vni_range_start
        assert spec["rt"] == "%d:%d" % (CONF.evpn.as_number, spec["vni"])
        assert spec["mtu"] == CONF.evpn.underlay_mtu - 50

        # evpn_subnet actual-state marker
        assert len(_resources("evpn_subnet", master=network.uuid)) == 1

        # evpn_port scheduled to the port's node, with the computed value
        port_res = _resources("evpn_port", master=subnet.uuid)
        assert len(port_res) == 1
        port_res = port_res[0]
        assert port_res.agent == hyp.uuid
        assert port_res.value["vni"] == spec["vni"]
        assert port_res.value["imp_rt"] == [spec["rt"]]
        assert port_res.value["ipv4"] == "10.42.0.1"
        assert port_res.value["dhcp"]["mtu"] == spec["mtu"]
        assert port_res.value["mac"]

        # evpn_host for the hypervisor, which is what runs the fabric —
        # not for the guest, which has none of it.
        host_res = ua_models.TargetResource.objects.get_all(
            filters={
                "kind": dm_filters.EQ("evpn_host"),
                "uuid": dm_filters.EQ(str(hyp.uuid)),
            }
        )
        assert len(host_res) == 1
        host_res = host_res[0]
        assert host_res.agent == hyp.uuid
        assert host_res.value["as_number"] == CONF.evpn.as_number

        # Port model got its IPAM allocation persisted back
        port = models.Port.objects.get_one(
            filters={"uuid": dm_filters.EQ(str(port.uuid))}
        )
        assert port.ipv4 == netaddr.IPAddress("10.42.0.1")

        # --- CP idempotency: another loop must not churn the port ------
        hash_before = port_res.hash
        with mock.patch.object(models.Network, "load_driver", selective_load):
            self._service._iteration()
        port_res2 = _resources("evpn_port", master=subnet.uuid)
        assert len(port_res2) == 1
        assert port_res2[0].hash == hash_before

        # --- DP: real host driver renders the very same resources ------
        dp_port = dp_evpn.EvpnPort(
            uuid=port_res.uuid,
            mac=port_res.value["mac"],
            ipv4=port_res.value["ipv4"],
            vni=port_res.value["vni"],
            imp_rt=port_res.value["imp_rt"],
            exp_rt=port_res.value["exp_rt"],
            dhcp=port_res.value["dhcp"],
            # The compiled network functions travel with the port: the host
            # installs these and nothing else.
            nfs=port_res.value["nfs"],
        )
        for stale in ("/tmp/e2e_vlan_map.json", "/tmp/e2e_sg_rules.json"):
            if os.path.exists(stale):
                os.remove(stale)
        with (
            # Where an installation puts things is `base`, and running a
            # command is `sh`: patching them on the package would rebind a
            # name the driver does not read.
            mock.patch.object(dp_base, "CLIENT_CONF_DIR", "/tmp/e2e_vmconf"),
            mock.patch.object(dp_base, "EGRESS_RECORDS_DIR", str(tmp_path / "egress")),
            mock.patch.object(dp_base, "NF_RECORDS_DIR", "/tmp/e2e_nf"),
            mock.patch.object(dp_base, "VLAN_MAP_PATH", "/tmp/e2e_vlan_map.json"),
            mock.patch.object(dp_base, "FLOW_BUNDLE_FILE", "/tmp/e2e_flow_bundle.tmp"),
            # Both live beside a real installation's agent state; a test
            # that wrote them would change what that installation allows.
            mock.patch.object(dp_base, "SG_RULES_PATH", "/tmp/e2e_sg_rules.json"),
            mock.patch.object(dp_base, "PUBLISHED_FILE", "/tmp/e2e_published.json"),
            # The port starts the responder it needs, so it owns this too.
            mock.patch.object(dp_base, "NF_UNIT_PATH", "/tmp/e2e_nf.service"),
            mock.patch.object(dp_sh, "run", return_value="1042\n"),
        ):
            dp_port.dump_to_dp()
            with open("/tmp/e2e_vmconf/%s.json" % dp_port.uuid) as fl:
                client = json.load(fl)
            # the record lands in its VNI's directory: that is how the
            # host's responder learns which namespaces to serve
            with open("/tmp/e2e_nf/%s/%s.json" % (dp_port.vni, dp_port.uuid)) as fl:
                dhcp_record = json.load(fl)

        assert client["vni"] == spec["vni"]
        assert client["ofport"] == 1042
        assert client["routes"] == ["10.42.0.1/32"]
        assert client["imp_rt"] == [spec["rt"]]
        assert dp_port.status == "ACTIVE"
        # The record exists because a `dhcp` function serves this port, and
        # says which functions do — the responder is data-driven, not
        # hardcoded.
        assert dhcp_record["dhcp_enabled"] is True
        assert dhcp_record["dns_enabled"] is True
        # ... while what the functions answer with arrives as their own
        # slices, rendered beside the guest's record in the VNI's directory
        assert "filename" not in dhcp_record

        # The record is what the host-local responder serves this guest
        # from, so the values it must carry are asserted here rather than
        # through a config generator the product does not run.
        assert dhcp_record["mac"] == port_res.value["mac"]
        assert dhcp_record["ipv4"] == "10.42.0.1"
        assert dhcp_record["mtu"] == spec["mtu"]
        assert dhcp_record["routers"] == [{"to": "0.0.0.0/0", "via": "10.42.0.254"}]

        dp_host = dp_evpn.EvpnHost(
            uuid=host_res.uuid,
            as_number=host_res.value["as_number"],
            rr_addresses=host_res.value["rr_addresses"],
        )
        gobgp_conf = _rendered(dp_host, "10.77.0.11")
        assert "as = %d" % CONF.evpn.as_number in gobgp_conf
        assert 'neighbor-address = "%s"' % RR in gobgp_conf
        assert "long-lived-graceful-restart" in gobgp_conf

        # --- bgp_rr: driver maintains the reflector resource ------------
        rr_agent = ua_models.UniversalAgent(
            uuid=sys_uuid.uuid4(),
            name="rr-agent",
            node=node.uuid,
            capabilities={"evpn_node": ["bgp_rr"]},
        )
        rr_agent.insert()
        self._created.append(rr_agent)
        CONF.set_override("rr_agent", str(rr_agent.uuid), group="evpn")
        CONF.set_override("rr_peer_prefixes", ["10.77.0.0/24"], group="evpn")
        try:
            with mock.patch.object(models.Network, "load_driver", selective_load):
                self._service._iteration()
            rr_res = ua_models.TargetResource.objects.get_all(
                filters={"kind": dm_filters.EQ("bgp_rr")}
            )
            assert len(rr_res) == 1
            rr_res = rr_res[0]
            assert rr_res.agent == rr_agent.uuid
            assert rr_res.value["peer_prefixes"] == ["10.77.0.0/24"]

            dp_rr = dp_evpn.BgpRr(
                uuid=rr_res.uuid,
                as_number=rr_res.value["as_number"],
                peer_prefixes=rr_res.value["peer_prefixes"],
            )
            rr_conf = _rendered(dp_rr, RR)
            assert "route-reflector-client = true" in rr_conf
            assert 'prefix = "10.77.0.0/24"' in rr_conf
        finally:
            CONF.clear_override("rr_agent", group="evpn")
            CONF.clear_override("rr_peer_prefixes", group="evpn")
            for res in ua_models.TargetResource.objects.get_all(
                filters={"kind": dm_filters.EQ("bgp_rr")}
            ):
                res.delete()

        # --- GC: node gone -> port rows gone -> evpn_host collected -----
        # (deleting only the port is not enough: NetworkService re-creates
        # a port for an uninitialized legacy VM node that has none)
        node_row = models.Node.objects.get_one(
            filters={"uuid": dm_filters.EQ(str(node.uuid))}
        )
        node_row.delete()
        self._created.remove(node)
        for row in models.Port.objects.get_all(
            filters={"subnet": dm_filters.EQ(str(subnet.uuid))}
        ):
            row.delete()
            if row.uuid == port.uuid:
                self._created.remove(port)
        with mock.patch.object(models.Network, "load_driver", selective_load):
            self._service._iteration()
        assert _resources("evpn_port", master=subnet.uuid) == []
        assert (
            ua_models.TargetResource.objects.get_all(
                filters={
                    "kind": dm_filters.EQ("evpn_host"),
                    "uuid": dm_filters.EQ(str(node.uuid)),
                }
            )
            == []
        )

    def test_self_referencing_sg_compiles_memberships_on_both_ports(self):
        from exordos_core.user_api.network.dm import models as catalog

        network, subnet = self._network()
        flat, _ = self._flat_network()
        hyp = self._pinned_node(flat.uuid, "hyp-sg")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guests = [
            self._pinned_node(network.uuid, name) for name in ("sender", "receiver")
        ]
        for guest in guests:
            self._machine(guest, pool)
        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
        group = self._security_group("peers", [])
        rule = catalog.SecurityGroupRule(
            project_id=group.project_id,
            security_group=group.uuid,
            direction="ingress",
            protocol="tcp",
            port=443,
            remote_group_id=group.uuid,
        )
        rule.insert()
        self._created.append(rule)
        ports = [
            models.Port.objects.get_one(filters={"node": dm_filters.EQ(guest.uuid)})
            for guest in guests
        ]
        for port in ports:
            self._bind_sg(port, group)
        with self._selective_load():
            self._service._iteration()
        targets = _resources("evpn_port", master=subnet.uuid)
        assert len(targets) == 2
        mark = targets[0].value["identity"]
        assert mark
        for target in targets:
            assert target.value["identity"] == mark
            assert target.value["security_rules"] == [
                {
                    "direction": "ingress",
                    "proto": "tcp",
                    "port": 443,
                    "remote_identity": mark,
                }
            ]
        with self._selective_load():
            self._service._iteration()
        assert {
            target.hash for target in _resources("evpn_port", master=subnet.uuid)
        } == {target.hash for target in targets}

    def test_an_upgrade_reaches_a_quiet_subnet_too(self):
        # What the installation generates for a subnet is derived from its
        # own settings, and was refreshed only when a port happened to be
        # compiled — so an upgrade that changed a default reached a busy
        # installation and never a quiet one. The sweep is per iteration.
        from exordos_core.user_api.network.dm import models as net_api_models

        # The grants are derived from where the boot API tells a machine to
        # fetch, so the test has to say where that is.
        CONF.set_override("gc_boot_api", "http://10.30.0.2:11013", "boot_api")
        CONF.set_override("kernel", "http://10.30.0.2:8080/bios/vmlinuz", "boot_api")

        evpn_net, evpn_subnet = self._network()
        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        proxy = [
            nf
            for nf in net_api_models.NetworkFunction.objects.get_all()
            if nf.kind == "proxy" and str(nf.owner_network or "") == str(evpn_net.uuid)
        ][0]
        self._created.append(proxy)
        # Defaults are derived when compiled, without a stored snapshot.
        assert proxy.config == {}

        # No port changes; only the loop runs.
        with self._selective_load():
            self._service._iteration()

        proxy = net_api_models.NetworkFunction.objects.get_one(
            filters={"uuid": dm_filters.EQ(proxy.uuid)}
        )
        driver = evpn_net.load_driver()
        assert proxy.render_config(driver._nf_defaults(evpn_subnet)["proxy"])["ports"]
        assert proxy.config == {}
        CONF.clear_override("gc_boot_api", "boot_api")
        CONF.clear_override("kernel", "boot_api")

    def test_a_deleted_subnet_takes_its_generated_rows_with_it(self):
        # Subnet-owned functions are collected without allocating group identities.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, first = self._network()
        first.delete()
        cycles = 17
        for index in range(cycles):
            subnet = models.Subnet(
                network=evpn_net.uuid,
                cidr=netaddr.IPNetwork("10.43.%d.0/24" % index),
                project_id=c.ZERO_UUID,
            )
            subnet.insert()
            self._created.append(subnet)
            # First pass creates the subnet, the second seeds its objects.
            with self._selective_load():
                self._service._iteration()
                self._service._iteration()
            owned = net_api_models.NetworkFunction.objects.get_all(
                filters={"owner_subnet": dm_filters.EQ(subnet.uuid)}
            )
            assert {nf.kind for nf in owned} == {"dhcp"}
            subnet.delete()

            assert not net_api_models.NetworkFunction.objects.get_all(
                filters={"owner_subnet": dm_filters.EQ(subnet.uuid)}
            )
            assert not net_api_models.Address.objects.get_all(
                filters={"subnet": dm_filters.EQ(subnet.uuid)}
            )
        assert not net_api_models.SecurityGroupIdentity.objects.get_all(
            filters={"network": dm_filters.EQ(evpn_net.uuid)}
        )
        # The network's own functions are not the subnet's to take.
        assert net_api_models.NetworkFunction.objects.get_all(
            filters={"owner_network": dm_filters.EQ(evpn_net.uuid)}
        )

    def test_a_reserved_address_keeps_its_subnet(self):
        from exordos_core.user_api.network.dm import models as net_api_models

        _, subnet = self._network()
        addr = net_api_models.Address(
            uuid=sys_uuid.uuid4(),
            project_id=c.ZERO_UUID,
            subnet=subnet.uuid,
            address="10.42.0.20",
        )
        addr.insert()
        self._created.append(addr)

        with pytest.raises(ra_storage_exc.ConflictRecords, match="reserved"):
            subnet.delete()
        assert models.Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet.uuid)}
        )

    def test_flat_only_installation_needs_no_reflector(self):
        assert (
            bootstrap_defaults.render_evpn_rr_config(
                {
                    "stand": {
                        "network": {"cidr": "10.20.0.0/22"},
                        "bootstraps": [
                            {
                                "uuid": str(sys_uuid.uuid4()),
                                "ports": [{"ip": "10.20.0.2"}],
                            }
                        ],
                    }
                }
            )
            is None
        )
