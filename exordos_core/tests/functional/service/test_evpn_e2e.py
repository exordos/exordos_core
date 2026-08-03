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

"""End-to-end test of the ovs_evpn stack across both repos.

The real ``ovs_evpn`` CP driver is driven through the real
``NetworkService`` reconcile loop against the migrated test database,
producing real ``ua_target_resources``. Those exact rows are then handed
to the real ``gcl_sdk`` host-side ``EvpnAgentCapabilityDriver``, which
renders the evpn_connector client config and the gobgpd config (only the
leaf ``ovs-vsctl``/``systemctl``/``ip`` calls are mocked). This exercises
the whole chain the transport (orch API + agent) carries in production.
"""

import json
import os
from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as sdk_models
import netaddr
from oslo_config import cfg
import pytest

from restalchemy.storage import exceptions as ra_storage_exc
from restalchemy.dm import filters as dm_filters

from exordos_core.bootstrap import defaults as bootstrap_defaults
from exordos_core.common import constants as c
from exordos_core.common import exceptions as ex_exceptions
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
            project_id=c.SERVICE_PROJECT_ID,
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
            project_id=c.SERVICE_PROJECT_ID,
        )
        network.insert()
        self._created.append(network)
        subnet = models.Subnet(
            network=network.uuid,
            cidr=netaddr.IPNetwork("10.42.0.0/24"),
            project_id=c.SERVICE_PROJECT_ID,
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
            project_id=c.SERVICE_PROJECT_ID,
        )
        network.insert()
        self._created.append(network)
        subnet = models.Subnet(
            network=network.uuid,
            cidr=netaddr.IPNetwork("10.99.0.0/24"),
            project_id=c.SERVICE_PROJECT_ID,
        )
        subnet.insert()
        self._created.append(subnet)
        return network, subnet

    def _pinned_node(self, network_uuid, name):
        node = models.Node(
            uuid=sys_uuid.uuid4(),
            name=name,
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=c.SERVICE_PROJECT_ID,
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
            driver_spec=models.DummyPoolDriverSpec(),
        )
        pool.insert()
        self._created.append(pool)
        return pool

    def _machine(self, node, pool):
        machine = models.Machine(
            name=node.name + "-machine",
            project_id=c.SERVICE_PROJECT_ID,
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
        # The `simple` kind's dhcp/dns toggles are the friendly surface of
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
            config=models.PortSimpleKind(dhcp=False),
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
        # A port whose `simple` kind references catalog security-group objects
        # must have their rules compiled into the evpn_port target's
        # `security_rules` (which the on-host agent turns into a conntrack
        # pipeline). Ties the catalog SG + simple kind to the data plane.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-sg")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-sg")
        self._machine(guest, pool)

        sg = net_api_models.SecurityGroup(
            uuid=sys_uuid.uuid4(),
            name="web-sg",
            project_id=c.SERVICE_PROJECT_ID,
            rules=[
                {"direction": "ingress", "protocol": "tcp", "port": 443},
                {"direction": "ingress", "protocol": "tcp", "port": 22},
            ],
        )
        sg.insert()
        self._created.append(sg)

        # A port carrying the simple kind referencing the SG — exactly what the
        # user-facing API creates.
        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(security_groups=[sg.uuid]),
        )
        port.insert()
        self._created.append(port)

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

        sg = net_api_models.SecurityGroup(
            uuid=sys_uuid.uuid4(),
            name="edited-sg",
            project_id=c.SERVICE_PROJECT_ID,
            rules=[{"direction": "ingress", "protocol": "tcp", "port": 443}],
        )
        sg.insert()
        self._created.append(sg)

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(security_groups=[sg.uuid]),
        )
        port.insert()
        self._created.append(port)

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
        sg.rules = [{"direction": "ingress", "protocol": "tcp", "port": 22}]
        sg.update()
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

    def test_floating_ip_compiles_into_evpn_port(self):
        # A port whose `simple` kind references a public address-object via
        # `public.address` must have it compiled into the evpn_port target's
        # `fips` (a 1:1 NAT mapping the on-host driver programs) — SDN CP API
        # §7 `fip`.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-fip")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-fip")
        self._machine(guest, pool)

        addr = net_api_models.Address(
            uuid=sys_uuid.uuid4(),
            project_id=c.SERVICE_PROJECT_ID,
            subnet=evpn_subnet.uuid,
            address="203.0.113.7",
            origin="floating",
        )
        addr.insert()
        self._created.append(addr)

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(public={"address": str(addr.uuid)}),
        )
        port.insert()
        self._created.append(port)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        assert ep.value.get("fips") == [{"public": "203.0.113.7"}]

    def test_floating_from_allocates_and_compiles_fip(self):
        # simple.public.floating_from names the subnet to take one from;
        # the compiler
        # auto-allocates a floating address there (once, owned by the port)
        # and compiles it into evpn_port.fips — SDN CP API §7 `fip`.
        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-ff")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-ff")
        self._machine(guest, pool)

        pub_net = models.Network(
            name="public-net",
            driver_spec={"driver": "flat_bridge"},
            project_id=c.SERVICE_PROJECT_ID,
        )
        pub_net.insert()
        self._created.append(pub_net)
        pub_subnet = models.Subnet(
            network=pub_net.uuid,
            cidr=netaddr.IPNetwork("203.0.113.0/24"),
            project_id=c.SERVICE_PROJECT_ID,
        )
        pub_subnet.insert()
        self._created.append(pub_subnet)

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(
                public={"floating_from": str(pub_subnet.uuid)}
            ),
        )
        port.insert()
        self._created.append(port)

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

    def test_deleting_a_floating_from_port_frees_its_owned_address(self):
        # A floating_from port owns the public address the compiler allocated
        # for it (owner_port); deleting the port must free it. Otherwise the
        # address leaks forever — Address.delete refuses an owned address
        # ("delete the port instead"), so the port-delete path is the only one
        # that can release it.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-ffdel")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-ffdel")
        self._machine(guest, pool)

        pub_net = models.Network(
            name="public-net-del",
            driver_spec={"driver": "flat_bridge"},
            project_id=c.SERVICE_PROJECT_ID,
        )
        pub_net.insert()
        self._created.append(pub_net)
        pub_subnet = models.Subnet(
            network=pub_net.uuid,
            cidr=netaddr.IPNetwork("203.0.114.0/24"),
            project_id=c.SERVICE_PROJECT_ID,
        )
        pub_subnet.insert()
        self._created.append(pub_subnet)

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(
                public={"floating_from": str(pub_subnet.uuid)}
            ),
        )
        port.insert()
        self._created.append(port)

        def _owned():
            return net_api_models.Address.objects.get_all(
                filters={"owner_port": dm_filters.EQ(port.uuid)}
            )

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            assert len(_owned()) == 1  # the floating address was allocated

            evpn_net.load_driver().delete_port(port)
            assert _owned() == []  # …and released with the port

    def test_a_cited_address_is_marked_used_and_given_back(self):
        # The ledger is what says a public address is spoken for: the
        # compiler records which port is using one, a second port citing it
        # is refused, and a port that goes away hands it back — without
        # deleting it, because a standalone address is the caller's and
        # outlives the machine that answered on it.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-assoc")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-assoc")
        self._machine(guest, pool)

        addr = net_api_models.Address(
            uuid=sys_uuid.uuid4(),
            project_id=c.SERVICE_PROJECT_ID,
            subnet=evpn_subnet.uuid,
            address="203.0.115.7",
            origin="floating",
        )
        addr.insert()
        self._created.append(addr)

        port = models.Port(
            subnet=evpn_subnet.uuid,
            node=guest.uuid,
            project_id=evpn_subnet.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(public={"address": str(addr.uuid)}),
        )
        port.insert()
        self._created.append(port)

        def _stored():
            return net_api_models.Address.objects.get_one(
                filters={"uuid": dm_filters.EQ(addr.uuid)}
            )

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()
            assert str(_stored().association) == str(port.uuid)

            # Nobody else may take it while this guest is answering on it.
            other = self._pinned_node(evpn_net.uuid, "guest-assoc-2")
            self._machine(other, pool)
            second = models.Port(
                subnet=evpn_subnet.uuid,
                node=other.uuid,
                project_id=evpn_subnet.project_id,
                mac=models.Port.generate_mac(),
                config=models.PortSimpleKind(public={"address": str(addr.uuid)}),
            )
            with pytest.raises(ex_exceptions.ValidateException):
                second.insert()

            # The port goes; the address stays reserved and free to re-cite.
            evpn_net.load_driver().delete_port(port)
            released = _stored()
            assert released.association is None
            assert released.allocation == "reserved"

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
        # DHCP, the resolver and the netboot/metadata proxy used to be
        # invented per port from `[evpn]` options — invisible and
        # unoverridable. They are seeded as generated NFs owned by the
        # subnet (dhcp) and the network (dns, proxy), and what the compiler
        # sends the host is read from them.
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
        assert all(
            nf.provenance == net_api_models.NFProvenance.GENERATED.value
            for nf in seeded.values()
        )
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
        dns_ref = [nf for nf in eports[0].value["nfs"] if nf["kind"] == "dns"][0]
        assert dns_ref["nf"] == str(seeded["dns"].uuid)
        dns_slices = [
            r
            for r in _resources("evpn_nf", master=evpn_subnet.uuid)
            if r.value["kind"] == "dns"
        ]
        assert dns_slices
        assert dns_slices[0].value["config"] == {
            "forwarders": ["9.9.9.9"],
            "zone_suffix": "corp",
        }
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

    def test_a_guest_arrives_behind_its_subnets_default_group(self):
        # Filtering used to be opt-in: a port with no group compiled to an
        # empty rule list, which the host reads as "no filtering at all".
        # Every port now gets its subnet's default group — everything out,
        # and in only from its own subnet — and that default is an object,
        # so an installation can edit or remove it.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-def")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-def")
        self._machine(guest, pool)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        default = [
            nf
            for nf in net_api_models.NetworkFunction.objects.get_all()
            if nf.kind == "splitter"
            and str(nf.owner_subnet or "") == str(evpn_subnet.uuid)
        ]
        assert default, "the subnet must have been given a default group"
        self._created.append(default[0])

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        rules = eports[0].value["security_rules"]
        assert rules, "a port with no group of its own must not be unfiltered"
        directions = {r["direction"] for r in rules}
        assert directions == {"egress", "ingress"}
        ingress = [r for r in rules if r["direction"] == "ingress"][0]
        # "From my own subnet" by membership, not by address. The one rule
        # that applies to every guest is the last place that should decide
        # who is "us" by an address a neighbour can simply choose, so the
        # subnet gets an identity group, every port on it joins, and the
        # rule names the group. What travels is the group's number.
        from exordos_core.user_api.network.dm import models as net_api_models

        group = net_api_models.IdentityGroup.objects.get_one(
            filters={"name": dm_filters.EQ("subnet-%s" % evpn_subnet.uuid)}
        )
        self._created.append(group)
        assert "remote" not in ingress
        assert ingress["remote_identity"] == int(group.identity)
        assert eports[0].value["identity"] == int(group.identity), (
            "and the port stamps the same group on everything it sends"
        )

    def test_unpinned_node_avoids_overlay_network(self):
        # Overlay placement is opt-in: an unpinned node must land on the
        # flat network even when an ovs_evpn one coexists (subnet iteration
        # order is not deterministic, and a management-plane node placed on
        # an overlay would have no connectivity).
        self._network()
        _, flat_subnet = self._flat_network()
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

    def test_apply_private_network_seeds_ovs_evpn_and_is_idempotent(self):
        # The bootstrap seed adds an ovs_evpn private network next to the flat
        # one so a fresh install supports both types. Calling it twice must be
        # a no-op (idempotent), and a node pinned to it must land on its
        # subnet through the standard reconcile.
        bootstrap_defaults.apply_private_network({"private_network": {}})
        bootstrap_defaults.apply_private_network({"private_network": {}})

        net = models.Network.objects.get_one(
            filters={"uuid": dm_filters.EQ(c.PRIVATE_NETWORK_UUID)}
        )
        self._created.append(net)
        assert net.driver_spec["driver"] == "ovs_evpn"

        subnets = models.Subnet.objects.get_all(
            filters={"network": dm_filters.EQ(str(net.uuid))}
        )
        assert len(subnets) == 1, "idempotent seed must not duplicate the subnet"
        self._created.append(subnets[0])
        assert subnets[0].next_server is None
        assert subnets[0].cidr == netaddr.IPNetwork(c.PRIVATE_NETWORK_CIDR)

        # A node pinned to the seeded private network lands on its subnet.
        node = self._pinned_node(net.uuid, "priv-hyp")
        with self._selective_load():
            self._service._iteration()

        ports = models.Port.objects.get_all(
            filters={"subnet": dm_filters.EQ(str(subnets[0].uuid))}
        )
        for p in ports:
            self._created.append(p)
        assert {str(p.node) for p in ports} == {str(node.uuid)}


class TestOvsEvpnE2E(_EvpnStand):
    def test_cp_computes_targets_then_dp_renders_them(self):
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

        # VNI/RT/MTU allocated onto the network spec
        network = models.Network.objects.get_one(
            filters={"uuid": dm_filters.EQ(str(network.uuid))}
        )
        spec = network.driver_spec
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
        for stale in ("/tmp/e2e_vlan_map.json",):
            if os.path.exists(stale):
                os.remove(stale)
        with (
            # Where an installation puts things is `base`, and running a
            # command is `sh`: patching them on the package would rebind a
            # name the driver does not read.
            mock.patch.object(dp_base, "CLIENT_CONF_DIR", "/tmp/e2e_vmconf"),
            mock.patch.object(dp_base, "NF_RECORDS_DIR", "/tmp/e2e_nf"),
            mock.patch.object(dp_base, "VLAN_MAP_PATH", "/tmp/e2e_vlan_map.json"),
            mock.patch.object(dp_base, "FLOW_BUNDLE_FILE", "/tmp/e2e_flow_bundle.tmp"),
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
        # a port for any VM node that has none — that is by design)
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

    def test_a_port_can_join_a_group_of_its_own(self):
        # What a port *is* is a reference, like the groups that say what it
        # may do: it names an identity group and stamps that group's number
        # instead of its subnet's. Nothing about addresses changes hands.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-ident")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-ident")
        self._machine(guest, pool)

        web = net_api_models.IdentityGroup(
            uuid=sys_uuid.uuid4(),
            name="web",
            project_id=evpn_subnet.project_id,
            network=evpn_net.uuid,
            identity=net_api_models.IdentityGroup.allocate_identity(evpn_net.uuid),
        )
        web.insert()
        self._created.append(web)

        with self._selective_load():
            self._service._iteration()
            self._service._iteration()

        port = models.Port.objects.get_all(filters={"node": dm_filters.EQ(guest.uuid)})[
            0
        ]
        port.config = models.PortSimpleKind(identity_groups=[web.uuid])
        port.update()

        with self._selective_load():
            self._service._iteration()

        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        subnet_group = net_api_models.IdentityGroup.objects.get_one(
            filters={"name": dm_filters.EQ("subnet-%s" % evpn_subnet.uuid)}
        )
        # Both bits: joining a group adds access rather than trading the
        # subnet's away, which is what carrying a *set* buys.
        assert ep.value["identity"] == int(web.identity) | int(subnet_group.identity)
        assert int(web.identity) != int(subnet_group.identity)

        # ... and a workload that is not one of the neighbours says so: it
        # then carries only what it declared, so the subnet's default rule
        # no longer admits it anywhere.
        port.config = models.PortSimpleKind(
            identity_groups=[web.uuid], subnet_group=False
        )
        port.update()
        with self._selective_load():
            self._service._iteration()
        eports = _resources("evpn_port", master=evpn_subnet.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        assert ep.value["identity"] == int(web.identity)

        # A group that is named by a rule cannot be deleted out from under
        # it: the rule would compile to a match on a number nobody stamps,
        # and the port it protects would quietly stop admitting the
        # workloads it was meant to.
        sg = net_api_models.SecurityGroup(
            uuid=sys_uuid.uuid4(),
            name="admits-web",
            project_id=evpn_subnet.project_id,
            rules=[
                {
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port": 443,
                    "remote_group": str(web.uuid),
                }
            ],
        )
        sg.insert()
        self._created.append(sg)
        with pytest.raises(ra_storage_exc.ConflictRecords):
            web.delete()

        # ... and neither can it be deleted out from under its members. That
        # would take their membership away silently and free the bit for the
        # next group, after which the rules that admitted them admit
        # somebody else.
        sg.rules = []
        sg.update()
        with pytest.raises(ra_storage_exc.ConflictRecords):
            web.delete()

        # Two callers allocating at once pick different bits: the scan that
        # decides is taken under a lock, so the second one sees the first.
        second = net_api_models.IdentityGroup(
            uuid=sys_uuid.uuid4(),
            name="api",
            project_id=evpn_subnet.project_id,
            network=evpn_net.uuid,
            identity=net_api_models.IdentityGroup.allocate_identity(evpn_net.uuid),
        )
        second.insert()
        self._created.append(second)
        assert int(second.identity) != int(web.identity)

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
        # Someone's installation was seeded before the grants were derived.
        # `sync_generated` is how the installation writes its own defaults —
        # an ordinary update would mark the function the operator's, which is
        # exactly what must *not* be refreshed.
        proxy.sync_generated({"forwards": [], "ports": {}})

        default = [
            nf
            for nf in net_api_models.NetworkFunction.objects.get_all()
            if nf.kind == "splitter"
            and str(nf.owner_subnet or "") == str(evpn_subnet.uuid)
        ][0]
        self._created.append(default)
        stale = [
            {"direction": "egress", "proto": "any"},
            {"direction": "ingress", "proto": "any", "remote": "10.0.0.0/8"},
        ]
        default.sync_generated({"rules": stale})

        # No port changes; only the loop runs.
        with self._selective_load():
            self._service._iteration()

        proxy = net_api_models.NetworkFunction.objects.get_one(
            filters={"uuid": dm_filters.EQ(proxy.uuid)}
        )
        default = net_api_models.NetworkFunction.objects.get_one(
            filters={"uuid": dm_filters.EQ(default.uuid)}
        )
        assert proxy.config["ports"], "the grants a guest needs came back"
        rules = default.config["rules"]
        ingress = [r for r in rules if r["direction"] == "ingress"][0]
        assert "remote" not in ingress, "and the default names a group again"
        assert ingress["remote_group"]
        # ... and both are still the installation's to keep current.
        assert proxy.provenance == net_api_models.NFProvenance.GENERATED.value
        assert default.provenance == net_api_models.NFProvenance.GENERATED.value
        CONF.clear_override("gc_boot_api", "boot_api")
        CONF.clear_override("kernel", "boot_api")

    def test_a_subnet_on_a_network_out_of_bits_is_filtered_not_isolated(self):
        # Sixteen bits is what a packet carries, and it used to be what a
        # network could hold: the seventeenth group was refused, and so was
        # a subnet, because a subnet whose guests cannot be given a default
        # group is not a state worth reaching. The refusal is gone, and this
        # is what has to be true instead — the subnet gets its default the
        # other way, as an address set, and its guests compile to that rule
        # rather than to the deny-all a failed seed produces.
        from exordos_core.user_api.network.dm import models as net_api_models

        evpn_net, evpn_subnet = self._network()
        flat_net, _ = self._flat_network()
        hyp = self._pinned_node(flat_net.uuid, "hyp-nobits")
        pool = self._pool(hypervisor_node=hyp.uuid)
        guest = self._pinned_node(evpn_net.uuid, "guest-nobits")
        self._machine(guest, pool)

        # Spend every bit the network has, the way an operator would.
        for index in range(net_api_models.IdentityGroup.IDENTITY_BITS):
            group = net_api_models.IdentityGroup(
                uuid=sys_uuid.uuid4(),
                name="filler-%d" % index,
                project_id=c.SERVICE_PROJECT_ID,
                network=evpn_net.uuid,
                **net_api_models.IdentityGroup.allocate(evpn_net.uuid),
            )
            group.insert()
            self._created.append(group)

        second = models.Subnet(
            network=evpn_net.uuid,
            cidr=netaddr.IPNetwork("10.43.0.0/24"),
            project_id=c.SERVICE_PROJECT_ID,
        )
        second.insert()
        self._created.append(second)

        port = models.Port(
            subnet=second.uuid,
            node=guest.uuid,
            project_id=second.project_id,
            mac=models.Port.generate_mac(),
            config=models.PortSimpleKind(),
        )
        port.insert()
        self._created.append(port)

        with self._selective_load():
            # Three, and the third is not padding: the generated objects of a
            # subnet are refreshed *before* its ports are compiled, so the
            # membership of a set is shipped one iteration after the guest
            # that joined it got its port. Until then the rule naming the set
            # is half a match and admits nobody, which is the fail-closed
            # direction and repairs itself — but it is not what this asserts.
            self._service._iteration()
            self._service._iteration()
            self._service._iteration()

        default = net_api_models.IdentityGroup.objects.get_one_or_none(
            filters={"name": dm_filters.EQ("subnet-%s" % second.uuid)}
        )
        assert default is not None, "the subnet has a default group"
        assert default.identity is None and default.conj_id, "carried as a set"

        eports = _resources("evpn_port", master=second.uuid)
        ep = [r for r in eports if str(r.value.get("uuid")) == str(port.uuid)][0]
        rules = ep.value.get("security_rules")
        # Filtered, not isolated: the default's ingress names the set, and a
        # deny-all would be the single `proto: none` sentinel instead.
        assert {r["proto"] for r in rules} != {"none"}
        assert [r for r in rules if r.get("remote_set") == int(default.conj_id)]

        # ... and the membership that answers for it reaches the host. Not
        # asked for under this subnet's master: a set belongs to the
        # *network*, so whichever of its subnets refreshes first creates the
        # slice, and that is the master it hangs under.
        sets = _resources("evpn_address_set")
        assert [r for r in sets if str(r.value.get("group")) == str(default.uuid)], (
            "the membership that answers for the group is shipped to the host"
        )

    def test_an_upgrade_that_asked_for_no_overlay_is_given_none(self):
        # The old scheme has to keep working, and "keep working" is not a
        # promise about the data path: it is that upgrading an installation
        # which never mentioned a private network creates no `ovs_evpn`
        # network, writes no `[evpn]` drop-in and restarts nothing. A spec
        # written before any of this existed carries no such block, so this
        # is what every existing installation looks like on upgrade.
        before = {str(n.uuid) for n in models.Network.objects.get_all()}

        bootstrap_defaults.apply_private_network({})

        after = {str(n.uuid) for n in models.Network.objects.get_all()}
        assert after == before, "an unasked-for overlay was seeded"
        assert str(c.PRIVATE_NETWORK_UUID) not in after
        # ... and the reflector config, which is written on every bootstrap
        # and restarts the network service when it changes, is not rendered
        # either -- there is no fabric for it to configure.
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
