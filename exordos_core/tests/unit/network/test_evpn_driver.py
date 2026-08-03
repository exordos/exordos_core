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


@pytest.mark.parametrize(
    "reported,matching,owner,expected",
    [
        ("ACTIVE", True, True, "ACTIVE"),
        ("ACTIVE", False, True, "IN_PROGRESS"),
        ("ERROR", True, True, "ERROR"),
        ("IN_PROGRESS", True, True, "IN_PROGRESS"),
        (None, False, True, "IN_PROGRESS"),
        ("ACTIVE", True, False, "IN_PROGRESS"),
    ],
)
def test_hypervisor_identity_follows_current_owned_host_fact(
    reported, matching, owner, expected
):
    driver = _make_driver()
    uuid = sys_uuid.uuid4()
    target = mock.MagicMock(
        kind="evpn_host", uuid=uuid, agent=uuid, hash="current", status="IN_PROGRESS"
    )
    actual = mock.MagicMock(
        kind="evpn_host",
        uuid=uuid,
        node=uuid if owner else sys_uuid.uuid4(),
        hash="current" if matching else "old",
        status=reported,
    )
    node = mock.MagicMock(
        uuid=uuid, status="ERROR", description="No suitable HW machines found"
    )
    with (
        mock.patch.object(
            evpn.ua_models.Resource,
            "objects",
            **{"get_all.return_value": [actual] if reported else []},
        ),
        mock.patch.object(
            evpn.ua_models.TargetResource,
            "objects",
            **{"get_all.return_value": [target]},
        ),
        mock.patch.object(
            evpn.models.MachinePool,
            "objects",
            **{"get_all.return_value": [mock.MagicMock(hypervisor_node=uuid)]},
        ),
        mock.patch.object(
            evpn.models.Node, "objects", **{"get_all.return_value": [node]}
        ),
        mock.patch.object(
            evpn.models.Machine, "objects", **{"get_all.return_value": []}
        ),
    ):
        driver._actualize_statuses()
    assert node.status == expected
    if expected != "ERROR":
        assert node.description == ""
        node.save.assert_called_once()
    else:
        node.save.assert_not_called()


def test_host_fact_does_not_promote_an_unregistered_hw_workload():
    driver = _make_driver()
    with (
        mock.patch.object(
            evpn.models.MachinePool, "objects", **{"get_all.return_value": []}
        ),
        mock.patch.object(evpn.models.Node, "objects") as nodes,
    ):
        driver._actualize_hypervisor_nodes({sys_uuid.uuid4(): "ACTIVE"})
    nodes.get_all.assert_not_called()


def test_provisioned_hypervisor_keeps_its_machine_lifecycle():
    driver = _make_driver()
    uuid = sys_uuid.uuid4()
    with (
        mock.patch.object(
            evpn.models.MachinePool,
            "objects",
            **{"get_all.return_value": [mock.MagicMock(hypervisor_node=uuid)]},
        ),
        mock.patch.object(
            evpn.models.Machine,
            "objects",
            **{"get_all.return_value": [mock.MagicMock(node=uuid)]},
        ),
        mock.patch.object(evpn.models.Node, "objects") as nodes,
    ):
        driver._actualize_hypervisor_nodes({uuid: "ACTIVE"})
    nodes.get_all.assert_not_called()


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
    """An allocated network keeps its VNI, RT and MTU.

    The first bind adds a confirmation marker, so the whole-table collision
    re-check does not rerun on every bind.
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

    # A pool that names no hypervisor has nobody to wire the guest: the guest's
    # own agent cannot, its bridge is on the hypervisor.
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
    port.port_security = True
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
        mock.patch.object(evpn.ipam, "ledger_reservations", return_value={}),
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
    assert set(port_dns) == {"kind"}

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
    port.port_security = True
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


@pytest.mark.parametrize(
    "name", ["db.internal", "web_1", "-web", "x" * 64, "web 1", "wéb"]
)
def test_a_name_that_is_not_one_dns_label_is_not_published(name):
    """A dotted name claims names below the zone suffix; the rest is not a
    name the responder can put on the wire."""
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.mac = "52:54:00:aa:bb:cc"
    port.ipv4 = netaddr.IPAddress("10.42.0.5")
    port.name = name
    port.port_security = True
    port.node = None

    with _seeded_services(driver):
        evpn_port = driver._build_evpn_port(port, subnet, publish=False)
    assert "name" not in evpn_port.dhcp


def test_port_without_name_omits_dns_zone():
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.node = sys_uuid.uuid4()
    port.mac = "52:54:00:aa:bb:cc"
    port.ipv4 = netaddr.IPAddress("10.42.0.5")
    port.name = None
    port.port_security = True
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
        "proxy": {"forwards": []},
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
    assert all(set(s) == {"kind"} for s in services if s["kind"] != "proxy")
    # The one exception, and it is not configuration: a door is a port the
    # bridge steals from this guest, so which ports those are has to be part
    # of what the port's own target hashes over.
    proxy = next(s for s in services if s["kind"] == "proxy")
    assert set(proxy) == {"kind", "config"}
    assert set(proxy["config"]) == {"ports"}


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
    """A guest arrives filtered: everything out, in only from its own subnet."""
    driver = _make_driver()
    subnet = _subnet_stub()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", security_groups=[])
    default = _nf(
        "splitter",
        {
            "rules": [
                {"direction": "egress", "protocol": "any"},
                {
                    "direction": "ingress",
                    "protocol": "any",
                    "remote_ip": str(subnet.cidr),
                },
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
    sg.rules = [{"direction": "ingress", "protocol": "tcp", "port": 22}]
    default = _nf("splitter", {"rules": [{"direction": "egress", "protocol": "any"}]})
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
    address_objects = mock.MagicMock()
    address_objects.objects.get_all.return_value = []
    with (
        _api_models(Address=address_objects),
        mock.patch.object(driver, "list_ports", return_value=[]),
    ):
        first = driver._allocate_ip(subnet, None)

    reserved = mock.MagicMock(address=str(first), subnet=subnet.uuid)
    address_objects.objects.get_all.return_value = [reserved]
    with (
        _api_models(Address=address_objects),
        mock.patch.object(driver, "list_ports", return_value=[]),
    ):
        allocated = driver._allocate_ip(subnet, None)

    assert allocated != first


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
    pool = mock.MagicMock(uuid=held.subnet)
    captured = {}

    def _get_all(filters):
        captured.update(filters)
        return [held]

    with (
        mock.patch(
            "exordos_core.user_api.network.dm.models.Address.objects",
            **{"get_all.side_effect": _get_all},
        ),
        mock.patch.object(
            evpn.models.Subnet, "objects", **{"get_all.return_value": [pool]}
        ),
        mock.patch(
            "exordos_core.user_api.network.dm.models.is_floating_pool",
            return_value=True,
        ),
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
        mock.patch.object(driver, "_collect_host") as collect,
    ):
        driver.delete_subnet(subnet)

    collect.assert_called_once_with(node)


def test_the_proxy_is_granted_what_a_netbooting_guest_must_fetch():
    """The proxy is granted what a netbooting guest must fetch.

    An overlay guest reaches the boot network only through its hypervisor's
    proxy; grants derive from the boot API's fetch URLs so they cannot drift.
    """
    from oslo_config import cfg

    from exordos_core.network.driver import evpn as evpn_driver

    conf = cfg.CONF
    conf.set_override("kernel", "http://10.30.0.2:8080/bios/vmlinuz", "boot_api")
    conf.set_override("gc_boot_api", "http://10.30.0.2:11013", "boot_api")
    try:
        assert evpn_driver._default_proxy_forwards() == [
            "/bios=http://10.30.0.2:8080/bios",
            "/=http://10.30.0.2:11013",
        ]
    finally:
        for opt, group in (
            ("kernel", "boot_api"),
            ("gc_boot_api", "boot_api"),
        ):
            conf.clear_override(opt, group)


def test_a_subnet_without_a_default_group_compiles_to_deny_not_to_open():
    """A subnet without a default group compiles to deny, not to open.

    `_default_group_nf` returns nothing when the default cannot be produced (no
    identity bits, lost seed race, row removed); an empty allow-list would mean
    no filtering at all.
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
    """The default group is looked up on its own network, not by name alone.

    Any project may name a group anything; adopting theirs would tie the
    subnet's default rules to a row they can delete.
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

    Seeding asks once per (kind, owner); one row for every ask would have the
    `proxy` pass rewrite the `dns` row.
    """
    by_kind = {nf.kind: [nf] for nf in existing}
    model = mock.MagicMock()
    model.objects.get_all.side_effect = lambda filters: by_kind.get(
        filters["kind"].value, []
    )
    return model


def test_every_key_the_seeder_writes_is_one_the_model_accepts():
    """Every config the seeder writes validates against the real schema.

    The seeding tests mock NetworkFunction, so nothing else checks this seam; a
    key missing from the schema makes seeding raise on every pass and leaves the
    proxy without grants.
    """
    from exordos_core.user_api.network.dm import models as net_api_models

    driver = _make_driver()
    subnet = _subnet_stub()
    written = {}

    model = mock.MagicMock()
    model.objects.get_one_or_none.return_value = None
    model.objects.create = None  # unused; creation goes through the class

    def _capture(**kwargs):
        written[kwargs["kind"]] = kwargs.get("config") or {}
        return mock.MagicMock()

    model.side_effect = _capture
    with _api_models(
        NetworkFunction=model,
        NFProvenance=mock.MagicMock(GENERATED=mock.MagicMock(value="generated")),
    ):
        driver._seed_service_nfs(subnet)

    assert written, "the seeder wrote nothing, so this asserts nothing"
    schema = net_api_models.NetworkFunction._CONFIG_SCHEMA
    for kind, config in written.items():
        unknown = sorted(set(config) - set(schema.get(kind, {})))
        assert not unknown, (
            "%s: the seeder writes %s, which the model refuses — the "
            "function is never created and everything it serves is a 404"
            % (kind, unknown)
        )


def test_a_service_the_caller_owns_is_still_left_alone():
    """An operator's resolver is theirs — that is what makes these objects
    rather than options."""
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
    """Checking a port for staleness is a read and seeds nothing.

    It runs for every port on every loop; anything unseeded just compiles to a
    difference, and the next update seeds it.
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


def test_a_pool_asked_for_after_the_port_was_built_makes_it_stale():
    """The comparing compile allocates nothing, so a `floating_from` added
    to a built port compiled to no address, matched what was deployed, and
    the address was never taken."""
    driver = _make_driver()
    port = mock.MagicMock()
    port.config = mock.MagicMock(
        KIND="simple", public={"floating_from": sys_uuid.uuid4()}
    )
    deployed = mock.MagicMock(hash="same")
    compiled = mock.MagicMock()
    compiled.to_ua_resource.return_value = mock.MagicMock(hash="same")
    with (
        mock.patch.object(driver, "_get_resource", return_value=deployed),
        mock.patch.object(driver, "_find_subnet", return_value=_subnet_stub()),
        mock.patch.object(driver, "_build_evpn_port", return_value=compiled),
        mock.patch.object(driver, "_public_address", return_value=None) as public,
    ):
        assert driver.port_is_stale(port)
        public.assert_called_once_with(port, allocate=False)

        # Once the address is held, only a difference makes it stale.
        public.return_value = mock.MagicMock()
        assert not driver.port_is_stale(port)


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


def test_a_node_may_only_name_an_origin_the_installation_publishes():
    """What a node boots from is written by whoever owns the node. Read as a
    grant, it is a tenant naming an address on the management network and
    having the hypervisor fetch it for them — the thing a door needs a
    permission for, asked by somebody with none."""
    from oslo_config import cfg

    from exordos_core.compute.dm import models as compute_models
    from exordos_core.network.driver import evpn as evpn_driver

    conf = cfg.CONF
    conf.set_override("kernel", "http://10.30.0.2:8080/bios/vmlinuz", "boot_api")
    conf.set_override("gc_boot_api", "http://10.30.0.2:11013", "boot_api")
    conf.set_override("image_origins", [], "evpn")

    subnet = mock.MagicMock(uuid=sys_uuid.uuid4())

    def _origins(image):
        evpn_driver._refused_image_origins.clear()
        port = mock.MagicMock(node=sys_uuid.uuid4())
        node = mock.MagicMock(uuid=port.node)
        node.disk_spec.image = image
        with (
            mock.patch.object(
                compute_models.Port, "objects", **{"get_all.return_value": [port]}
            ),
            mock.patch.object(
                compute_models.Node, "objects", **{"get_one_or_none.return_value": node}
            ),
        ):
            return evpn_driver._image_origins(subnet)

    try:
        # The artifact host the netboot kernel already comes from.
        assert _origins("http://10.30.0.2:8080/images/base.raw.zst") == [
            "http://10.30.0.2:8080"
        ]
        # Anything else on the management network is not a grant to make.
        assert _origins("http://10.30.0.9:8080/images/base.raw.zst") == []
        assert _origins("http://10.30.0.2:22/images/base.raw.zst") == []
        # The operator may add one, which is what the escape hatch is for.
        conf.set_override("image_origins", ["http://10.30.0.9:8080"], "evpn")
        assert _origins("http://10.30.0.9:8080/images/base.raw.zst") == [
            "http://10.30.0.9:8080"
        ]
    finally:
        evpn_driver._refused_image_origins.clear()
        for opt, group in (
            ("kernel", "boot_api"),
            ("gc_boot_api", "boot_api"),
            ("image_origins", "evpn"),
        ):
            conf.clear_override(opt, group)


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


def test_a_rule_naming_a_group_compiles_to_its_bit():
    """The agent matches a number, not a name."""
    driver = _make_driver()
    group = mock.MagicMock(uuid=sys_uuid.uuid4(), identity=4)

    with mock.patch.object(
        evpn.OvsEvpnNetworkDriver,
        "_identity_group",
        staticmethod(lambda _uuid: group),
    ):
        compiled = driver._compile_rule(
            {"direction": "ingress", "protocol": "tcp", "remote_group": group.uuid}
        )

    assert compiled == {"direction": "ingress", "proto": "tcp", "remote_identity": 4}


def test_a_subnet_on_a_network_with_no_bits_left_gets_no_default():
    """Past its network's sixteen bits a group is refused, not carried some
    other way. The subnet's create is refused up front; a seed that loses
    the last bit to a race anyway is left without a default (fail closed)
    rather than handed something that is not a group."""
    from exordos_core.common import exceptions as ex_exceptions

    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = sys_uuid.uuid4()
    subnet.project_id = sys_uuid.uuid4()

    group_model = mock.MagicMock()
    group_model.objects.get_all.return_value = []
    group_model.allocate_identity.side_effect = ex_exceptions.ValidateException(
        err="no identity bit left"
    )

    with _api_models(IdentityGroup=group_model):
        assert driver._default_identity_group(subnet) is None

    group_model.return_value.insert.assert_not_called()


def test_the_comparing_compile_allocates_no_floating_address():
    """It must write nothing: an address it took would be found already
    there by the compile that publishes, allocated by a check."""
    driver = _make_driver()
    port = mock.MagicMock()
    port.config = mock.MagicMock(KIND="simple", public={"floating_from": "x"})
    address_objects = mock.MagicMock()
    address_objects.objects.get_one_or_none.return_value = None
    with _api_models(Address=address_objects):
        assert driver._public_address(port, allocate=False) is None
    address_objects.assert_not_called()


@pytest.mark.parametrize("default_group", [False, True])
def test_an_empty_allowlist_denies_all_traffic(default_group):
    driver = _make_driver()
    port = mock.MagicMock()
    port.config.KIND = "simple"
    port.config.security_groups = [] if default_group else [sys_uuid.uuid4()]
    group = mock.MagicMock(rules=[])
    group.config = {"rules": []}
    with (
        mock.patch.object(driver, "_default_group_nf", return_value=group),
        _api_models(
            SecurityGroup=mock.MagicMock(
                **{"objects.get_one_or_none.return_value": group}
            )
        ),
    ):
        rules = driver._compile_security_rules(port, _subnet_stub())
    assert rules == list(driver.DENY_ALL)
