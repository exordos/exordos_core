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

import contextlib
from unittest import mock
import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.network.driver import evpn


def _network(spec=None):
    network = mock.MagicMock()
    network.uuid = sys_uuid.uuid4()
    network.driver_spec = spec if spec is not None else {"driver": "ovs_evpn"}
    return network


def _subnet_stub(dhcp=True):
    subnet = mock.MagicMock()
    subnet.uuid = sys_uuid.uuid4()
    subnet.dhcp = dhcp
    return subnet


def _make_driver(spec=None, used_vnis=()):
    nets = []
    for vni in used_vnis:
        used = mock.MagicMock()
        used.uuid = sys_uuid.uuid4()
        used.driver_spec = {"driver": "ovs_evpn", "vni": vni}
        nets.append(used)
    with mock.patch.object(
        evpn.models.Network, "objects", **{"get_all.return_value": nets}
    ):
        return evpn.OvsEvpnNetworkDriver(_network(spec))


def test_rejects_foreign_spec():
    with pytest.raises(evpn.InvalidEvpnDriverSpec):
        evpn.OvsEvpnNetworkDriver(_network({"driver": "flat_bridge"}))


def test_allocates_vni_rt_mtu_and_persists():
    driver = _make_driver(used_vnis=[10000, 10001])

    spec = driver._network.driver_spec
    assert spec["vni"] == 10002
    assert spec["rt"] == "65001:10002"
    assert spec["mtu"] == 1450
    driver._network.update.assert_called_once()


def test_existing_allocation_untouched():
    """An allocated network keeps its VNI, its RT and its MTU.

    It does gain a confirmation marker on the first bind: the collision
    re-check scans the whole network table, and running it on every bind for
    the life of the installation is quadratic in the number of networks and
    proves nothing after the race window has closed.
    """
    spec = {"driver": "ovs_evpn", "vni": 12345, "rt": "65001:12345", "mtu": 1400}
    driver = _make_driver(spec=dict(spec))

    stored = driver._network.driver_spec
    assert {k: stored[k] for k in spec} == spec
    assert stored[evpn.OvsEvpnNetworkDriver.VNI_CONFIRMED] is True


def test_a_confirmed_vni_is_not_rescanned():
    """The scan is the cost this marker exists to stop paying."""
    spec = {
        "driver": "ovs_evpn",
        "vni": 12345,
        "rt": "65001:12345",
        "mtu": 1400,
        evpn.OvsEvpnNetworkDriver.VNI_CONFIRMED: True,
    }
    with mock.patch.object(evpn.models.Network, "objects") as objects:
        driver = evpn.OvsEvpnNetworkDriver(_network(dict(spec)))
    objects.get_all.assert_not_called()
    driver._network.update.assert_not_called()


def test_vni_exhaustion():
    with mock.patch.object(evpn.CONF.evpn, "vni_range_end", 10001):
        with pytest.raises(evpn.EvpnVniExhausted):
            _make_driver(used_vnis=[10000, 10001])


def test_create_port_without_node_is_skipped():
    driver = _make_driver()
    port = mock.MagicMock()
    port.node = None

    with mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs:
        assert driver.create_port(port) is port
        objs.get_one.assert_not_called()


def _subnet_view():
    subnet_uuid = sys_uuid.uuid4()
    return {
        "uuid": str(subnet_uuid),
        "name": "s1",
        "network": str(sys_uuid.uuid4()),
        "project_id": str(sys_uuid.uuid4()),
        "cidr": "10.42.0.0/24",
        "ip_range": None,
        "ip_discovery_range": None,
        "dhcp": True,
        "dns_servers": ["10.42.0.254"],
        "routers": [{"to": "0.0.0.0/0", "via": "10.42.0.254"}],
        "next_server": None,
    }


def test_wiring_agent_defers_until_machine_placed():
    driver = _make_driver()
    node = sys_uuid.uuid4()

    with mock.patch.object(
        evpn.models.Machine, "objects", **{"get_one_or_none.return_value": None}
    ):
        assert driver._wiring_agent(node) is None

    unplaced = mock.MagicMock()
    unplaced.pool = None
    with mock.patch.object(
        evpn.models.Machine, "objects", **{"get_one_or_none.return_value": unplaced}
    ):
        assert driver._wiring_agent(node) is None


def test_wiring_agent_uses_pool_hypervisor_node():
    driver = _make_driver()
    node = sys_uuid.uuid4()
    hyp = sys_uuid.uuid4()

    machine = mock.MagicMock()
    machine.pool = sys_uuid.uuid4()
    pool = mock.MagicMock()
    pool.hypervisor_node = hyp

    with (
        mock.patch.object(
            evpn.models.Machine,
            "objects",
            **{"get_one_or_none.return_value": machine},
        ),
        mock.patch.object(
            evpn.models.MachinePool,
            "objects",
            **{"get_one_or_none.return_value": pool},
        ),
    ):
        assert driver._wiring_agent(node) == hyp

    # A pool that names no hypervisor has nobody to wire the guest. It
    # used to be handed to the guest's own agent, on the reading that a
    # node can wire itself — true of a machine that *is* a host, false of
    # a guest of one, whose bridge is on the hypervisor. The resource was
    # created, scheduled to an agent that could never act on it, and the
    # port stayed new for ever with nothing saying why.
    pool.hypervisor_node = None
    with (
        mock.patch.object(
            evpn.models.Machine,
            "objects",
            **{"get_one_or_none.return_value": machine},
        ),
        mock.patch.object(
            evpn.models.MachinePool,
            "objects",
            **{"get_one_or_none.return_value": pool},
        ),
    ):
        assert driver._wiring_agent(node) is None


def test_create_port_defers_when_machine_not_placed():
    driver = _make_driver()
    port = mock.MagicMock()
    port.node = sys_uuid.uuid4()

    with (
        mock.patch.object(driver, "_wiring_agent", return_value=None),
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs,
    ):
        assert driver.create_port(port) is port
        objs.get_one.assert_not_called()


def test_create_port_produces_scheduled_resource():
    driver = _make_driver()
    node = sys_uuid.uuid4()
    subnet_res = mock.MagicMock()
    subnet_res.value = _subnet_view()

    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.node = node
    port.subnet = sys_uuid.UUID(subnet_res.value["uuid"])
    port.ipv4 = None
    port.target_ipv4 = None
    port.mac = None
    port.name = "web1"

    def get_resource(kind, uuid):
        if kind == evpn.SUBNET_KIND:
            return subnet_res
        raise evpn.ra_storage_exc.RecordNotFound(model=None, filters=None)

    stored_subnet = evpn.models.Subnet.restore_from_simple_view(**subnet_res.value)
    inserted = []
    with (
        mock.patch.object(driver, "_get_resource", side_effect=get_resource),
        mock.patch.object(
            evpn.models.Subnet,
            "objects",
            **{"get_one_or_none.return_value": stored_subnet},
        ),
        mock.patch.object(driver, "_list_resources", return_value=[]),
        mock.patch.object(driver, "_wiring_agent", side_effect=lambda n: n),
        # The subnet's default group is a catalog row; this test has no
        # storage, and what it is about is the resource the compile emits.
        mock.patch.object(driver, "_compile_identity", return_value=0),
        mock.patch.object(
            evpn.ua_models.TargetResource, "insert", lambda self: inserted.append(self)
        ),
        mock.patch.object(
            evpn.ua_models.TargetResource,
            "objects",
            **{"get_all.return_value": []},
        ),
        _seeded_services(driver),
    ):
        driver.create_port(port)

    # The port, its host, and a slice per function serving it
    kinds = sorted(r.kind for r in inserted)
    assert kinds == ["evpn_host", "evpn_nf", "evpn_nf", "evpn_nf", "evpn_port"]
    slices = [r for r in inserted if r.kind == "evpn_nf"]
    assert sorted(r.value["kind"] for r in slices) == ["dhcp", "dns", "proxy"]
    # every slice carries what its function answers with, and lands on the
    # host that serves the guest
    assert all(r.agent == node for r in slices)
    dns_slice = next(r for r in slices if r.value["kind"] == "dns")
    assert dns_slice.value["config"]["zone_suffix"] == evpn.CONF.evpn.dns_zone_suffix

    port_res = next(r for r in inserted if r.kind == "evpn_port")
    assert port_res.agent == node
    assert port_res.value["vni"] == 10000
    assert port_res.value["imp_rt"] == ["65001:10000"]
    assert port_res.value["ipv4"] == "10.42.0.1"
    assert port_res.value["dhcp"]["mtu"] == 1450
    assert port_res.value["dhcp"]["dns_servers"] == ["10.42.0.254"]
    # The guest contributes its name to the internal zone; the zone itself
    # (its suffix) belongs to the network's `dns` function.
    assert port_res.value["dhcp"]["name"] == "web1"
    assert "zone_suffix" not in port_res.value["dhcp"]
    assert port_res.value["mac"] is not None

    host_res = next(r for r in inserted if r.kind == "evpn_host")
    assert host_res.uuid == node
    assert host_res.agent == node
    assert host_res.value["as_number"] == 65001
    # The host resource is the fabric and nothing else — it is not told about
    # any network's functions, because the guests that need them start and
    # configure the responder themselves.
    assert "nfs" not in host_res.value
    # The port carries the settings its guest is served with; nothing on the
    # host is implicit any more.
    assert [nf["kind"] for nf in port_res.value["nfs"]] == ["dhcp", "dns", "proxy"]
    port_dns = next(nf for nf in port_res.value["nfs"] if nf["kind"] == "dns")
    # the port names the function; its settings are in that function's slice
    assert set(port_dns) == {"kind", "nf"}
    assert port_dns["nf"] == dns_slice.value["uuid"] or port_dns["nf"]

    # The port itself got its allocation back for persistence
    assert str(port.ipv4) == "10.42.0.1"
    assert port.mask == netaddr.IPAddress("255.255.255.0")


def test_a_guest_is_known_by_its_node_when_the_port_is_nameless():
    """The zone answers for guests, and the platform names neither the port
    it creates for a node nor anything else — so every guest of every
    overlay was missing from a zone the responder builds and the suffix is
    configured for. The node's name is the one a person would type."""
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    node_uuid = sys_uuid.uuid4()
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.node = node_uuid
    port.mac = "52:54:00:aa:bb:cc"
    port.ipv4 = netaddr.IPAddress("10.42.0.5")
    port.name = None

    node = mock.MagicMock()
    node.name = "sdn-a"
    with (
        _seeded_services(driver),
        mock.patch.object(
            evpn.models.Node,
            "objects",
            **{"get_one_or_none.return_value": node},
        ),
    ):
        evpn_port = driver._build_evpn_port(port, subnet, publish=False)
    assert evpn_port.dhcp["name"] == "sdn-a"

    # A port that names itself keeps its own name: that is someone saying
    # what this interface is called.
    port.name = "web1"
    with (
        _seeded_services(driver),
        mock.patch.object(
            evpn.models.Node,
            "objects",
            **{"get_one_or_none.return_value": node},
        ),
    ):
        named = driver._build_evpn_port(port, subnet, publish=False)
    assert named.dhcp["name"] == "web1"


def test_port_without_name_omits_dns_zone():
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.node = sys_uuid.uuid4()
    port.mac = "52:54:00:aa:bb:cc"
    port.ipv4 = netaddr.IPAddress("10.42.0.5")
    port.name = None
    port.node = None  # nothing to fall back to, so the zone stays empty

    with _seeded_services(driver):
        evpn_port = driver._build_evpn_port(port, subnet, publish=False)
    assert "name" not in evpn_port.dhcp
    assert "zone_suffix" not in evpn_port.dhcp


def test_ensure_rr_disabled_by_default():
    driver = _make_driver()
    with mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs:
        driver._ensure_rr()
        objs.get_one.assert_not_called()


def test_ensure_rr_creates_resource():
    rr_agent = sys_uuid.uuid4()
    inserted = []
    driver = _make_driver()  # rr_agent unset during construction
    with (
        mock.patch.object(evpn.CONF.evpn, "rr_agent", str(rr_agent)),
        mock.patch.object(evpn.CONF.evpn, "rr_peer_prefixes", ["10.77.0.0/24"]),
        mock.patch.object(
            evpn.ua_models.TargetResource,
            "insert",
            lambda self: inserted.append(self),
        ),
        mock.patch.object(
            driver,
            "_get_resource",
            side_effect=evpn.ra_storage_exc.RecordNotFound(model=None, filters=None),
        ),
    ):
        driver._ensure_rr()

    assert len(inserted) == 1
    res = inserted[0]
    assert res.kind == "bgp_rr"
    assert res.uuid == rr_agent
    assert res.agent == rr_agent
    assert res.value["peer_prefixes"] == ["10.77.0.0/24"]


def test_delete_port_collects_idle_host():
    driver = _make_driver()
    node = sys_uuid.uuid4()
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()

    port_res = mock.MagicMock()
    port_res.agent = node
    host_res = mock.MagicMock()

    def get_resource(kind, uuid):
        return port_res if kind == "evpn_port" else host_res

    with (
        mock.patch.object(driver, "_get_resource", side_effect=get_resource),
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs,
    ):
        objs.get_all.return_value = []  # no evpn_port left on the node
        driver.delete_port(port)

    port_res.delete.assert_called_once()
    host_res.delete.assert_called_once()


def test_delete_port_keeps_busy_host():
    driver = _make_driver()
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()

    port_res = mock.MagicMock()
    port_res.agent = sys_uuid.uuid4()
    host_res = mock.MagicMock()

    def get_resource(kind, uuid):
        return port_res if kind == "evpn_port" else host_res

    with (
        mock.patch.object(driver, "_get_resource", side_effect=get_resource),
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs,
    ):
        objs.get_all.return_value = [mock.MagicMock()]  # another port remains
        driver.delete_port(port)

    port_res.delete.assert_called_once()
    host_res.delete.assert_not_called()


# --- network functions ------------------------------------------------------


def _nf(kind, config=None, uuid=None, outputs=None):
    nf = mock.MagicMock()
    nf.uuid = uuid or sys_uuid.uuid4()
    nf.kind = kind
    nf.config = config or {}
    nf.outputs = outputs or {}
    return nf


@contextlib.contextmanager
def _seeded_services(driver, **configs):
    """Stand in for the subnet's and network's service functions.

    The seeding itself needs a database and is covered by the functional
    tier; what a unit test can pin down is that the compiler takes the
    functions from the objects that own them, and nothing else.
    """
    defaults = {
        "dhcp": {"filename": evpn.DEFAULT_NETBOOT_URL},
        "dns": {
            "forwarders": list(evpn.CONF.evpn.dns_forwarders),
            "zone_suffix": evpn.CONF.evpn.dns_zone_suffix,
        },
        "proxy": {"forwards": list(evpn.CONF.evpn.proxy_forwards)},
    }
    defaults.update(configs)
    nfs = [_nf(kind, config) for kind, config in defaults.items()]
    # The default security group is seeded the same way and needs a database
    # too; its own tests drive it directly.
    with (
        mock.patch.object(
            type(driver), "_service_nfs", lambda self, subnet, seed=True: nfs
        ),
        mock.patch.object(
            type(driver), "_default_group_nf", lambda self, subnet, seed=True: None
        ),
        # The subnet's default identity is a catalog row, seeded the same
        # way and needing the same database.
        mock.patch.object(
            type(driver), "_compile_identity", lambda self, port, subnet, seed=True: 0
        ),
    ):
        yield nfs


@contextlib.contextmanager
def _api_models(**attrs):
    """Patch the lazily-imported user_api models the compiler reaches for."""
    from exordos_core.user_api.network.dm import models as net_api_models

    with contextlib.ExitStack() as stack:
        for name, value in attrs.items():
            stack.enter_context(mock.patch.object(net_api_models, name, value))
        yield


def test_host_services_come_from_the_subnet_and_the_installation():
    """DHCP, the resolver and the proxy take no composed configuration:
    what to hand out is the subnet, who to ask upstream is `[evpn]`."""
    driver = _make_driver()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", dhcp=None, dns=None)
    with _seeded_services(driver):
        services = driver._host_services(port, _subnet_stub())
    # the port names its functions; what each answers with travels as its
    # own slice, so a port's own resource carries no function configuration
    assert [s["kind"] for s in services] == ["dhcp", "dns", "proxy"]
    assert all(set(s) == {"kind", "nf"} for s in services)


def test_the_services_are_read_from_their_owners_not_invented():
    """The compiler asks the subnet and the network what serves their
    guests. An operator who edits a function sees the edit in the port's
    target resource — which is the whole reason they are objects."""
    driver = _make_driver()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", dhcp=None, dns=None)
    with _seeded_services(
        driver,
        dns={"forwarders": ["9.9.9.9"], "zone_suffix": "corp"},
        proxy={"forwards": ["/boot=http://mirror/"]},
    ):
        services = driver._host_services(port, _subnet_stub())
    by_kind = {s["kind"]: s["nf"] for s in services}
    assert set(by_kind) == {"dhcp", "dns", "proxy"}
    # each names the object it came from, which is what the host's slice of
    # that function is keyed by
    assert all(by_kind.values())


def test_a_missing_service_is_simply_not_served():
    """Nothing is invented to fill a gap: a network whose proxy function was
    deleted compiles to a port without one, not to a default."""
    driver = _make_driver()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", dhcp=None, dns=None)
    nfs = [_nf("dhcp", {"filename": evpn.DEFAULT_NETBOOT_URL})]
    with mock.patch.object(
        type(driver), "_service_nfs", lambda self, subnet, seed=True: nfs
    ):
        kinds = [s["kind"] for s in driver._host_services(port, _subnet_stub())]
    assert kinds == ["dhcp"]


def test_a_port_with_no_group_of_its_own_gets_the_subnets_default():
    """A guest arrives filtered: everything out, and in only from its own
    subnet. Anything else needs a rule — which is what a default-deny
    posture means, and what an empty rule list used to fail to say."""
    driver = _make_driver()
    subnet = _subnet_stub()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", security_groups=[])
    default = _nf(
        "splitter",
        {
            "rules": [
                {"direction": "egress", "proto": "any"},
                {"direction": "ingress", "proto": "any", "remote": str(subnet.cidr)},
            ]
        },
    )
    with mock.patch.object(
        type(driver), "_default_group_nf", lambda self, s, seed=True: default
    ):
        rules = driver._compile_security_rules(port, subnet)
    assert {r["direction"] for r in rules} == {"egress", "ingress"}
    ingress = next(r for r in rules if r["direction"] == "ingress")
    assert ingress["remote"] == str(subnet.cidr)


def test_attaching_a_group_replaces_the_default():
    """Attaching a group is how a caller says what may reach the guest, so
    it must not be widened by the default sitting underneath it."""
    driver = _make_driver()
    subnet = _subnet_stub()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", security_groups=[sys_uuid.uuid4()])
    sg = mock.MagicMock()
    sg.rules = [{"direction": "ingress", "proto": "tcp", "port": 22}]
    default = _nf("splitter", {"rules": [{"direction": "egress", "proto": "any"}]})
    with (
        mock.patch.object(
            type(driver), "_default_group_nf", lambda self, s, seed=True: default
        ),
        _api_models(
            SecurityGroup=mock.MagicMock(**{"objects.get_one_or_none.return_value": sg})
        ),
    ):
        rules = driver._compile_security_rules(port, subnet)
    assert rules == [{"direction": "ingress", "proto": "tcp", "port": 22}]


def test_a_subnet_without_dhcp_serves_none():
    """`Subnet.dhcp` is the switch that already existed — the overlay used
    to ignore it and lease anyway."""
    driver = _make_driver()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", dhcp=None, dns=None)
    with _seeded_services(driver):
        kinds = [
            s["kind"] for s in driver._host_services(port, _subnet_stub(dhcp=False))
        ]
    assert "dhcp" not in kinds and "dns" in kinds


def test_port_toggles_switch_a_service_off():
    driver = _make_driver()
    subnet = _subnet_stub()
    port = mock.MagicMock()

    with _seeded_services(driver):
        port.config = mock.MagicMock(KIND="simple", dhcp=False, dns=None)
        assert [s["kind"] for s in driver._host_services(port, subnet)] == [
            "dns",
            "proxy",
        ]

        port.config = mock.MagicMock(KIND="simple", dhcp=None, dns=False)
        assert [s["kind"] for s in driver._host_services(port, subnet)] == [
            "dhcp",
            "proxy",
        ]


def test_security_rules_keep_their_direction():
    driver = _make_driver()
    sg = mock.MagicMock()
    sg.rules = [
        {
            "direction": "ingress",
            "protocol": "tcp",
            "port": 443,
            "remote_ip": "10.0.0.0/8",
        },
        {"direction": "egress", "protocol": "any"},
    ]
    sg_objects = mock.MagicMock()
    sg_objects.objects.get_one_or_none.return_value = sg
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", security_groups=[sys_uuid.uuid4()])

    with _api_models(SecurityGroup=sg_objects):
        rules = driver._compile_security_rules(port)

    assert rules == [
        {"direction": "ingress", "proto": "tcp", "port": 443, "remote": "10.0.0.0/8"},
        {"direction": "egress", "proto": "any"},
    ]


def test_compiling_an_already_compiled_rule_does_not_widen_it():
    """A generated `splitter` NF publishes what the compiler produced, so a
    graph pointing back at one feeds those rules in again. Reading only the
    source spelling would find neither protocol nor peer and turn a narrow
    rule into "any protocol, anywhere"."""
    driver = _make_driver()
    once = driver._compile_rule(
        {
            "direction": "ingress",
            "protocol": "tcp",
            "port": 443,
            "remote_ip": "10.0.0.0/8",
        }
    )
    assert driver._compile_rule(once) == once


def test_missing_security_group_keeps_the_port_filtered():
    """A group that vanished must not silently widen the port to allow-all."""
    driver = _make_driver()
    sg_objects = mock.MagicMock()
    sg_objects.objects.get_one_or_none.return_value = None
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", security_groups=[sys_uuid.uuid4()])

    with _api_models(SecurityGroup=sg_objects):
        rules = driver._compile_security_rules(port)

    # non-empty (the agent installs the pipeline) but nothing is allowed
    assert rules and all(r["proto"] == "none" for r in rules)


def test_catalog_reservations_are_not_handed_to_a_port():
    """The ledger and the port pool allocate over one subnet."""
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    reserved = mock.MagicMock()
    reserved.address = "10.42.0.1"
    address_objects = mock.MagicMock()
    address_objects.objects.get_all.return_value = [reserved]

    with (
        _api_models(Address=address_objects),
        mock.patch.object(driver, "list_ports", return_value=[]),
    ):
        allocated = driver._allocate_ip(subnet, None)

    assert str(allocated) != "10.42.0.1"


def _with_published(addresses):
    """What the installation's ledger says it publishes, or None for
    "could not be read"."""
    return mock.patch.object(evpn, "_published_addresses", return_value=addresses)


def test_evpn_host_is_updated_not_just_created():
    """Fabric parameters change over a node's life; a create-once resource
    would pin the host to whatever its first port implied."""
    driver = _make_driver()
    node = sys_uuid.uuid4()
    stale = mock.MagicMock()
    stale.hash = "stale-hash"

    with (
        _with_published([]),
        mock.patch.object(driver, "_get_resource", return_value=stale),
    ):
        driver._ensure_host(node)

    stale.update_value.assert_called_once()
    stale.update.assert_called_once()


def test_the_host_is_told_which_addresses_are_published():
    """The fabric guard blocks by address class, and a floating address is
    private by circumstance and a front door by role. Unless the host is
    told which is which, a realm cannot reach a door published to be
    reached."""
    driver = _make_driver()
    node = sys_uuid.uuid4()
    stale = mock.MagicMock()
    stale.hash = "stale-hash"

    with (
        _with_published(["10.20.3.1", "10.20.3.2"]),
        mock.patch.object(driver, "_get_resource", return_value=stale),
    ):
        driver._ensure_host(node)

    value = stale.update_value.call_args.args[0].value
    assert value["published_addresses"] == ["10.20.3.1", "10.20.3.2"]


def test_an_unreadable_ledger_shuts_no_doors():
    """One failed query must not close every published address in the
    installation until some later pass reopens them."""
    driver = _make_driver()
    node = sys_uuid.uuid4()
    stale = mock.MagicMock()
    stale.hash = "stale-hash"

    with (
        _with_published(None),
        mock.patch.object(driver, "_get_resource", return_value=stale),
    ):
        driver._ensure_host(node)

    stale.update_value.assert_not_called()
    stale.update.assert_not_called()


def test_only_the_addresses_still_held_are_published():
    """A freed row is a receipt, not a reservation — the door it names is
    gone, and leaving it open would outlive the realm that had it."""
    held, freed = mock.MagicMock(), mock.MagicMock()
    held.address, freed.address = "10.20.3.1", "10.20.3.9"
    captured = {}

    def _get_all(filters):
        captured.update(filters)
        return [held]

    with mock.patch(
        "exordos_core.user_api.network.dm.models.Address.objects",
        **{"get_all.side_effect": _get_all},
    ):
        assert evpn._published_addresses() == ["10.20.3.1"]

    assert "origin" in captured and "allocation" in captured


def test_a_configured_service_address_is_published_too():
    """A repository or a mirror has no row in the ledger — the platform does
    not hand out its address — and the guard still has to be told that
    reaching it is allowed."""
    with (
        mock.patch(
            "exordos_core.user_api.network.dm.models.Address.objects",
            **{"get_all.return_value": []},
        ),
        mock.patch.object(
            evpn.CONF,
            "evpn",
            **{"service_addresses": ["10.20.4.1:8081", " 10.20.4.2:443 "]},
        ),
    ):
        assert evpn._published_addresses() == ["10.20.4.1:8081", "10.20.4.2:443"]


def test_an_unparseable_service_address_is_dropped_here_not_on_every_host():
    """Checked where it is configured: a typo travelling to every
    hypervisor would be refused by each of them without a word."""
    with (
        mock.patch(
            "exordos_core.user_api.network.dm.models.Address.objects",
            **{"get_all.return_value": []},
        ),
        mock.patch.object(
            evpn.CONF,
            "evpn",
            **{
                "service_addresses": [
                    "10.20.4.1:8081",
                    "not-an-address:80",
                    "10.20.4.3:not-a-port",
                    "10.20.4.4:99999",
                ]
            },
        ),
    ):
        assert evpn._published_addresses() == ["10.20.4.1:8081"]


def test_delete_subnet_collects_the_hosts_of_its_ports():
    driver = _make_driver()
    node = sys_uuid.uuid4()
    port_res = mock.MagicMock()
    port_res.agent = node
    port_res.uuid = sys_uuid.uuid4()
    subnet = mock.MagicMock()
    subnet.uuid = sys_uuid.uuid4()

    with (
        mock.patch.object(driver, "_list_resources", return_value=[port_res]),
        mock.patch.object(driver, "_get_resource", return_value=mock.MagicMock()),
        mock.patch.object(driver, "_collect_generated_nfs"),
        mock.patch.object(driver, "_collect_host") as collect,
    ):
        driver.delete_subnet(subnet)

    collect.assert_called_once_with(node)


def test_the_proxy_is_granted_what_a_netbooting_guest_must_fetch():
    """A guest of an overlay has no route to the boot network, so everything
    it needs comes through its hypervisor's proxy — which until now was
    granted nothing at all: `[evpn] proxy_forwards` was read and set by no
    bootstrap, manifest or installer, making netboot over an overlay a 404 by
    construction. The grants are derived from where the boot API tells a
    machine to fetch, so they cannot drift from it."""
    from oslo_config import cfg

    from exordos_core.network.driver import evpn as evpn_driver

    conf = cfg.CONF
    conf.set_override("kernel", "http://10.30.0.2:8080/bios/vmlinuz", "boot_api")
    conf.set_override("gc_boot_api", "http://10.30.0.2:11013", "boot_api")
    conf.set_override("proxy_forwards", [], "evpn")
    try:
        assert evpn_driver._default_proxy_forwards() == [
            "/bios=http://10.30.0.2:8080/bios",
            "/=http://10.30.0.2:11013",
        ]

        # An operator who says it explicitly keeps it, as with every other
        # seeded function.
        conf.set_override("proxy_forwards", ["/x=http://example/x"], "evpn")
        assert evpn_driver._default_proxy_forwards() == ["/x=http://example/x"]
    finally:
        for opt, group in (
            ("kernel", "boot_api"),
            ("gc_boot_api", "boot_api"),
            ("proxy_forwards", "evpn"),
        ):
            conf.clear_override(opt, group)


def test_a_subnet_without_a_default_group_compiles_to_deny_not_to_open():
    """The failure direction of a security default is the whole point of it.

    `_default_group_nf` returns nothing when the default cannot be produced —
    the network is out of identity bits, the seed raced and lost, the row was
    removed out of band. That used to compile to an empty allow-list, which
    the agent applies as no filtering at all: a subnet that merely failed to
    get a group handed every one of its guests an unfiltered port, and said
    so only in a log line.
    """
    driver = _make_driver()
    subnet = _subnet_stub()
    with mock.patch.object(driver, "_default_group_nf", return_value=None):
        rules = driver._default_security_rules(subnet)
    assert rules, "a port with no producible default is not unfiltered"
    assert all(rule["proto"] == "none" for rule in rules), rules
    # No subnet at all is a different question — there is nothing to default
    # to, and the caller (a port off any subnet) is not being denied silently.
    assert driver._default_security_rules(None) == []


def test_the_default_group_is_looked_up_on_its_own_network():
    """A name is not an identity.

    Any project may create a group called anything, so finding this subnet's
    default by name alone could return — and adopt — a row belonging to
    somebody else, whose deletion then takes the subnet's default rules with
    it.
    """
    driver = _make_driver()
    subnet = _subnet_stub()
    with mock.patch(
        "exordos_core.user_api.network.dm.models.IdentityGroup"
    ) as group_model:
        group_model.objects.get_all.return_value = []
        group_model.allocate_identity.return_value = 1
        driver._default_identity_group(subnet)
    filters = group_model.objects.get_all.call_args.kwargs["filters"]
    assert "network" in filters, "scoped by the network, not by the name alone"
    assert "name" in filters


def _seed_probe(*existing):
    """A NetworkFunction stand-in that answers the seeding path per kind.

    Seeding asks once per (kind, owner), so the stand-in has to answer per
    kind too: a probe that returned the same row to every ask would have the
    `proxy` pass rewrite the `dns` row, which is the opposite of what these
    tests are about.
    """
    by_kind = {nf.kind: [nf] for nf in existing}
    model = mock.MagicMock()
    model.objects.get_all.side_effect = lambda filters: by_kind.get(
        filters["kind"].value, []
    )
    model.PLATFORM_KINDS = frozenset({"proxy"})
    return model


def test_the_proxy_function_is_brought_back_under_the_installation():
    """Its `forwards` and `ports` say which upstreams the *hypervisor* will
    fetch on a guest's behalf, from the network the overlay exists to keep
    guests out of. A row that ended up marked as somebody's own — an older
    stand, a manifest writing it directly — is rewritten from the
    installation's configuration rather than left pointing the host wherever
    it was pointed."""
    driver = _make_driver()
    subnet = _subnet_stub()
    taken_over = _nf("proxy", {"forwards": ["/=http://10.20.0.2:11010/"]})
    taken_over.provenance = "user"
    with _api_models(
        NetworkFunction=_seed_probe(taken_over),
        NFProvenance=mock.MagicMock(GENERATED=mock.MagicMock(value="generated")),
    ):
        driver._seed_service_nfs(subnet)
    taken_over.sync_generated.assert_called_once()
    written = taken_over.sync_generated.call_args.args[0]
    assert "http://10.20.0.2:11010/" not in str(written)
    assert set(written) == {"forwards", "ports", "apis"}


def test_a_service_the_caller_owns_is_still_left_alone():
    """Only the platform's own kinds are taken back. An operator's resolver
    is theirs — that is what makes these objects rather than options."""
    driver = _make_driver()
    subnet = _subnet_stub()
    edited = _nf("dns", {"forwarders": ["9.9.9.9"]})
    edited.provenance = "user"
    with _api_models(
        NetworkFunction=_seed_probe(edited),
        NFProvenance=mock.MagicMock(GENERATED=mock.MagicMock(value="generated")),
    ):
        driver._seed_service_nfs(subnet)
    edited.sync_generated.assert_not_called()


def test_a_group_of_another_network_is_not_stamped():
    """A bit means whatever the port's own network says it means, so a group
    from another one is a membership claim in a group the port was never
    admitted to. The API refuses it; a row that got in another way is refused
    here, because this is where it would take effect."""
    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = sys_uuid.uuid4()
    port = mock.MagicMock()
    port.config = mock.MagicMock(
        KIND="simple", identity_groups=[sys_uuid.uuid4()], subnet_group=False
    )
    elsewhere = mock.MagicMock()
    elsewhere.identity = 2
    elsewhere.network = sys_uuid.uuid4()
    with mock.patch.object(
        type(driver), "_identity_group", classmethod(lambda cls, uuid: elsewhere)
    ):
        assert driver._compile_identity(port, subnet) == 0

    # The same group on this network is stamped.
    elsewhere.network = subnet.network
    with mock.patch.object(
        type(driver), "_identity_group", classmethod(lambda cls, uuid: elsewhere)
    ):
        assert driver._compile_identity(port, subnet) == 2


def test_asking_whether_a_port_is_stale_writes_nothing():
    """The check runs for every port of every subnet on every loop, and it
    is a read. A read that seeds is one that can fail, race and rewrite —
    and the seeding it was doing has its own pass on the same loop.

    Anything not seeded yet simply compiles to a resource that differs from
    the deployed one, which is what the check is for: the next update seeds
    it and the difference goes away.
    """
    driver = _make_driver()
    subnet = _subnet_stub()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", dhcp=None, dns=None, public=None)
    port.uuid = sys_uuid.uuid4()
    port.node = None
    port.name = "probe"
    port.mac = "52:54:00:aa:bb:cc"
    port.ipv4 = netaddr.IPAddress("10.42.0.7")
    port.port_security = True

    seeded = []
    with (
        mock.patch.object(
            type(driver), "_seed_service_nfs", lambda self, s: seeded.append(s)
        ),
        _api_models(
            NetworkFunction=mock.MagicMock(
                **{"objects.get_all.return_value": []},
            ),
            IdentityGroup=mock.MagicMock(
                **{"objects.get_all.return_value": []},
            ),
        ),
    ):
        driver._build_evpn_port(port, subnet, publish=False)
    assert seeded == [], "the staleness path seeded a service function"


def test_only_an_origin_a_guest_cannot_reach_is_granted():
    """The proxy exists to bridge what the fabric guard drops — the space
    that is not globally routable. A public origin is fetched directly, and
    funnelling a multi-gigabyte image through the proxy for nothing is a
    cost; a host that is a name is nobody's to resolve on the guest's
    behalf."""
    from exordos_core.network.driver import evpn as evpn_driver

    assert (
        evpn_driver.unreachable_origin("http://10.20.0.1:8081/exordos-elements/x.zst")
        == "http://10.20.0.1:8081"
    )
    assert (
        evpn_driver.unreachable_origin("http://192.168.5.4/images/y.raw.zst")
        == "http://192.168.5.4"
    )
    assert evpn_driver.unreachable_origin("https://repo.exordos.com/x.zst") is None
    assert evpn_driver.unreachable_origin("https://8.8.8.8/x.zst") is None
    assert evpn_driver.unreachable_origin("") is None
    assert evpn_driver.unreachable_origin("not a url") is None


def test_an_origin_is_reached_at_the_same_path_every_time():
    """The guest is handed URLs under the prefix, so a prefix that moved
    between hosts or restarts would strand whatever was told the old one."""
    from exordos_core.network.driver import evpn as evpn_driver

    first = evpn_driver._origin_prefix("http://10.20.0.1:8081")
    assert first == evpn_driver._origin_prefix("http://10.20.0.1:8081")
    assert first != evpn_driver._origin_prefix("http://10.20.0.2:8081")
    assert first.startswith("/images-")


def test_the_root_grant_stays_last_so_the_others_are_reachable():
    """A grant of `/` matches every request, so the routing table offers it
    last: an image origin listed after it would never be routed to."""
    from exordos_core.network.driver import evpn as evpn_driver

    forwards = evpn_driver._default_proxy_forwards()
    roots = [i for i, f in enumerate(forwards) if f.startswith("/=")]
    assert not roots or roots[0] == len(forwards) - 1


def _group(identity=None, conj_id=None, uuid=None):
    """An identity group carried one way or the other, never both."""
    group = mock.MagicMock()
    group.uuid = uuid or sys_uuid.uuid4()
    group.identity = identity
    group.conj_id = conj_id
    return group


def test_a_group_past_the_bits_compiles_to_a_set_and_not_to_a_mark():
    """The same rule, the same syntax, a different mechanism — which is the
    compiler's decision and never the author's."""
    driver = _make_driver()
    with_bit = _group(identity=4)
    with_set = _group(conj_id=7001)

    with mock.patch.object(
        evpn.OvsEvpnNetworkDriver,
        "_identity_group",
        staticmethod(lambda _uuid: with_bit),
    ):
        by_mark = driver._compile_rule(
            {"direction": "ingress", "protocol": "tcp", "remote_group": with_bit.uuid}
        )
    with mock.patch.object(
        evpn.OvsEvpnNetworkDriver,
        "_identity_group",
        staticmethod(lambda _uuid: with_set),
    ):
        by_members = driver._compile_rule(
            {"direction": "ingress", "protocol": "tcp", "remote_group": with_set.uuid}
        )

    assert by_mark == {"direction": "ingress", "proto": "tcp", "remote_identity": 4}
    assert by_members == {"direction": "ingress", "proto": "tcp", "remote_set": 7001}
    # ... and a compiled rule read back (a generated splitter publishes what
    # the compiler produced) still says the same thing.
    assert driver._compile_rule(by_members) == by_members


def test_a_port_stamps_nothing_for_a_group_that_has_no_bit():
    """A member of an address-set group is recognised by its address, not by
    anything it carries — so contributing a bit here would be inventing one
    that means something else on the wire."""
    driver = _make_driver()
    subnet = _subnet_stub()
    with_set = _group(conj_id=7001)
    port = mock.MagicMock()
    port.config = mock.MagicMock(
        KIND="simple", identity_groups=[with_set.uuid], subnet_group=False
    )
    with mock.patch.object(
        evpn.OvsEvpnNetworkDriver,
        "_identity_group",
        staticmethod(lambda _uuid: with_set),
    ):
        assert driver._compile_identity(port, subnet, seed=False) == 0


def test_the_members_of_a_set_are_read_the_way_the_mark_is_stamped():
    """Two readings of "who is in this group" that could drift apart are
    one: the port's own list, plus its subnet's default unless it opted
    out."""
    driver = _make_driver()
    subnet = _subnet_stub()
    default = _group(conj_id=7001)

    joined = mock.MagicMock(ipv4="10.42.0.7")
    joined.config = mock.MagicMock(KIND="simple", identity_groups=[], subnet_group=True)
    opted_out = mock.MagicMock(ipv4="10.42.0.8")
    opted_out.config = mock.MagicMock(
        KIND="simple", identity_groups=[], subnet_group=False
    )
    by_name = mock.MagicMock(ipv4="10.42.0.9")
    by_name.config = mock.MagicMock(
        KIND="simple", identity_groups=[default.uuid], subnet_group=False
    )
    unaddressed = mock.MagicMock(ipv4=None)
    unaddressed.config = mock.MagicMock(
        KIND="simple", identity_groups=[], subnet_group=True
    )

    from exordos_core.compute.dm import models as compute_models

    with (
        mock.patch.object(driver, "list_subnets", return_value=[subnet]),
        mock.patch.object(driver, "_default_identity_group", return_value=default),
        mock.patch.object(
            compute_models.Port,
            "objects",
            **{"get_all.return_value": [joined, opted_out, by_name, unaddressed]},
        ),
    ):
        members = driver._set_members(default)

    # the one that opted out is not a member; the one with no address yet is
    # a member that cannot be recognised, which is the fail-closed reading
    assert members == ["10.42.0.7", "10.42.0.9"]


def test_a_set_is_shipped_to_every_host_of_the_network_and_reaped():
    """Its own resource per (group, host): a member joining moves these and
    re-hashes no port. And a host that lost its last guest keeps no
    allow-list somebody's rule could still join."""
    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = sys_uuid.uuid4()
    with_set = _group(conj_id=7001)
    hosts = {sys_uuid.uuid4(), sys_uuid.uuid4()}
    group_model = mock.MagicMock()
    group_model.objects.get_all.return_value = [_group(identity=4), with_set]

    stale = mock.MagicMock(uuid=sys_uuid.uuid4())
    with (
        _api_models(IdentityGroup=group_model),
        mock.patch.object(driver, "_serving_hosts", return_value=hosts),
        mock.patch.object(driver, "_set_members", return_value=["10.42.0.7"]),
        mock.patch.object(
            driver, "_ensure_address_set", side_effect=lambda *a, **k: a[2]
        ) as shipped,
        mock.patch.object(driver, "_list_resources", return_value=[stale]),
    ):
        driver._refresh_address_sets(subnet)

    # one per host, and only for the group that has no bit
    assert {call.args[0] for call in shipped.call_args_list} == {with_set}
    assert {call.args[2] for call in shipped.call_args_list} == hosts
    stale.delete.assert_called_once()


def test_a_subnet_on_a_network_with_no_bits_left_still_gets_a_default():
    """The seed takes whichever mechanism allocation hands it.

    Asking for a bit outright made a subnet on a full network compile to
    deny-all: the refusal landed in the seed's `except`, the subnet was left
    with no default group at all, and every one of its guests was isolated
    with one line in a log to say so. That is the state the create-time gate
    used to prevent, and an address set is what replaced the gate.
    """
    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = sys_uuid.uuid4()
    subnet.project_id = sys_uuid.uuid4()

    group_model = mock.MagicMock()
    group_model.objects.get_all.return_value = []
    # what a full network answers: no bit, a join number instead
    group_model.allocate.return_value = {"conj_id": 7001}
    group_model.allocate_identity.side_effect = AssertionError(
        "the seed must not ask for a bit it may not get"
    )

    with _api_models(IdentityGroup=group_model):
        group = driver._default_identity_group(subnet)

    assert group is not None, "the subnet has a default group"
    assert group_model.call_args.kwargs["conj_id"] == 7001
    assert "identity" not in group_model.call_args.kwargs
    group.insert.assert_called_once()
