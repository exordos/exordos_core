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


@pytest.fixture(autouse=True)
def no_database_savepoints(monkeypatch):
    monkeypatch.setattr(evpn.sql_utils, "savepoint", contextlib.nullcontext)


@pytest.fixture(autouse=True)
def empty_policy_catalog():
    from exordos_core.user_api.network.dm import models as catalog

    with (
        mock.patch.object(catalog.PortSecurityGroup, "objects") as memberships,
        mock.patch.object(catalog.SecurityGroupRule, "objects") as rules,
    ):
        memberships.get_all.return_value = []
        rules.get_all.return_value = []
        yield


def test_shared_host_and_nf_targets_are_written_once_after_port_batch():
    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = driver._network.uuid
    host = sys_uuid.uuid4()
    nf = _nf("dns", {"zone_suffix": "old"})
    with (
        mock.patch.object(
            evpn, "_published_addresses", side_effect=[["192.0.2.1"], ["192.0.2.2"]]
        ) as published,
        mock.patch.object(driver, "_write_host") as write_host,
        mock.patch.object(driver, "_dns_records", return_value=[]),
        mock.patch.object(driver, "_service_nfs", return_value=[nf]),
        mock.patch.object(driver, "_write_resource") as write_nf,
    ):
        with driver.reconcile():
            for _ in range(4):
                driver._ensure_host(host)
                driver._ensure_host_nfs(host, subnet)
            nf.config = {"zone_suffix": "new"}
            driver._ensure_host_nfs(host, subnet)
            write_host.assert_not_called()
            write_nf.assert_not_called()
        published.assert_called_once()
        write_host.assert_called_once_with(host, ["192.0.2.1"])
        write_nf.assert_called_once()
        assert write_nf.call_args.args[0].config["zone_suffix"] == "new"
        driver._ensure_host(host)
        assert published.call_count == 2


def test_live_nf_rows_are_shared_only_after_successful_seed():
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = driver._network.uuid
    with (
        mock.patch.object(catalog.NetworkFunction, "objects") as rows,
        mock.patch.object(driver, "_configured_or_core", return_value=[]),
        mock.patch.object(evpn, "_default_proxy_forwards", return_value=[]),
        mock.patch.object(evpn, "_proxy_services", return_value=[]),
        mock.patch.object(evpn, "_proxy_service_domains", return_value=[]),
    ):
        rows.get_one_or_none.return_value = mock.Mock(config={})
        rows.get_all.return_value = []
        for _ in range(2):
            with driver.reconcile():
                for _ in range(4):
                    assert driver._service_nfs(subnet) == []
        assert rows.get_all.call_count == 4
        driver._service_nfs(subnet)
        assert rows.get_all.call_count == 6


@pytest.mark.parametrize("with_guests", [False, True])
def test_subnet_maintenance_seeds_services_once_per_pass(with_guests):
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    targets = [
        mock.Mock(value={**_subnet_view(), "uuid": str(sys_uuid.uuid4())})
        for _ in range(3)
    ]
    host = sys_uuid.uuid4()

    def resources(kind, master):
        if kind == evpn.SUBNET_KIND:
            return targets
        return [mock.Mock(agent=host)] if with_guests else []

    with (
        mock.patch.object(driver, "_list_resources", side_effect=resources),
        mock.patch.object(driver, "_ensure_host"),
        mock.patch.object(driver, "_ensure_host_nfs") as refresh,
        mock.patch.object(catalog.NetworkFunction, "objects") as rows,
    ):
        rows.get_one_or_none.return_value = mock.Mock()
        rows.get_all.return_value = []
        with driver.reconcile():
            subnets = driver.list_subnets()
            for subnet in subnets:
                driver.refresh_generated(subnet)
        assert rows.get_one_or_none.call_count == 9
        assert refresh.call_count == (3 if with_guests else 0)


def test_pending_host_targets_are_flushed_after_failure_and_retried_next_pass():
    driver = _make_driver()
    host = sys_uuid.uuid4()
    with (
        mock.patch.object(evpn, "_published_addresses", return_value=[]),
        mock.patch.object(driver, "_write_host") as write_host,
    ):
        write_host.side_effect = RuntimeError("host storage unavailable")
        with pytest.raises(ValueError, match="interrupted reconciliation"):
            with driver.reconcile():
                driver._ensure_host(host)
                raise ValueError("interrupted reconciliation")
        write_host.assert_called_once()
        assert evpn._RECONCILE_READS.get() is None
        write_host.side_effect = None
        with driver.reconcile():
            driver._ensure_host(host)
        assert write_host.call_count == 2


def test_image_origins_load_distinct_nodes_in_one_query():
    subnet = _subnet_stub()
    node = mock.Mock(uuid=sys_uuid.uuid4())
    node.disk_spec.image = "http://10.20.0.1/image.raw"
    ports = [mock.Mock(node=node.uuid) for _ in range(4)]
    with (
        mock.patch.object(evpn.models.Port, "objects") as port_rows,
        mock.patch.object(evpn.models.Node, "objects") as nodes,
        mock.patch.object(
            evpn, "_granted_image_origins", return_value={"http://10.20.0.1"}
        ),
    ):
        port_rows.get_all.return_value = ports
        nodes.get_all.return_value = [node]
        nodes.get_one_or_none.return_value = node
        assert evpn._image_origins(subnet) == ["http://10.20.0.1"]
        nodes.get_all.assert_called_once()
        nodes.get_one_or_none.assert_not_called()


def test_comparison_reuses_targets_and_batches_node_names():
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    node = mock.Mock(uuid=sys_uuid.uuid4())
    node.name = "guest"
    targets = [
        mock.Mock(
            uuid=sys_uuid.uuid4(),
            agent=node.uuid,
            value={"ipv4": "10.42.0.7", "mac": "52:54:00:aa:bb:cc"},
        )
        for _ in range(3)
    ]
    with (
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as resources,
        mock.patch.object(evpn.models.Node, "objects") as nodes,
    ):
        resources.get_all.return_value = targets
        resources.get_one.return_value = targets[0]
        nodes.get_all.return_value = [node]
        nodes.get_one_or_none.return_value = node
        with driver.reconcile():
            ports = list(driver.list_ports(subnet))
            with driver.compare_ports(subnet, ports):
                for port, target in zip(ports, targets):
                    assert driver._get_resource("evpn_port", port.uuid) is target
                    assert driver._guest_name(port) == "guest"
        resources.get_all.assert_called_once()
        resources.get_one.assert_not_called()
        nodes.get_all.assert_called_once()
        nodes.get_one_or_none.assert_not_called()
        node.name = "renamed"
        assert driver._guest_name(ports[0]) == "renamed"
        driver._get_resource("evpn_port", ports[0].uuid)
        resources.get_one.assert_called_once()


def test_comparison_batches_fip_ledger_and_reads_live_when_publishing():
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    ports = [mock.Mock(uuid=sys_uuid.uuid4(), node=None, config=None) for _ in range(3)]
    address = mock.Mock(
        uuid=sys_uuid.uuid4(),
        port=ports[0].uuid,
        association=ports[0].uuid,
        owner_port=None,
        kind="floating",
        address="192.0.2.10",
    )
    with mock.patch.object(catalog.Address, "objects") as ledger:
        ledger.get_all.return_value = [address]
        ledger.get_one_or_none.side_effect = lambda filters: (
            address if filters["port"].value == ports[0].uuid else None
        )
        with driver.compare_ports(_subnet_stub(), ports):
            assert driver._compile_fips(ports[0], claim=False) == [
                {"public": "192.0.2.10"}
            ]
            for port in ports[1:]:
                assert driver._compile_fips(port, claim=False) == []
            ledger.get_all.assert_called_once()
            ledger.get_one_or_none.assert_not_called()
            assert driver._public_address(ports[0], cached=False) is address
            ledger.get_one_or_none.assert_called_once()
        driver._public_address(ports[0])
        assert ledger.get_one_or_none.call_count == 2


def test_port_network_uses_its_prefetched_subnet_when_casting():
    from exordos_core.network.dm import models as related
    from exordos_core.user_api.network.dm import models as catalog

    network = evpn.models.Network(project_id=sys_uuid.uuid4(), name="network")
    view = _subnet_view()
    base_subnet = evpn.models.Subnet.restore_from_simple_view(**view)
    fields = {name: getattr(base_subnet, name) for name in base_subnet.properties}
    fields["network"] = network
    subnet = related.Subnet(**fields)
    port = related.Port(
        subnet=subnet, project_id=subnet.project_id, mac=evpn.models.Port.generate_mac()
    )
    with (
        mock.patch.object(evpn.models.Subnet, "objects") as subnets,
        mock.patch.object(
            catalog.PortSecurityGroup, "objects", **{"get_all.return_value": []}
        ),
    ):
        subnets.get_all.return_value = [subnet]
        evpn.models.Port.project_observations([port])
        assert port.network == network.uuid
        base_port = port.cast_to_base()
        assert base_port.network == network.uuid
        subnets.get_all.assert_called_once()
        subnets.get_one_or_none.assert_not_called()


@pytest.mark.parametrize("failed", ["nodes", "ledger"])
def test_failed_comparison_prefetch_uses_live_reads_and_clears_scope(failed):
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    port = mock.Mock(uuid=sys_uuid.uuid4(), node=sys_uuid.uuid4(), config=None)
    port.name = ""
    node = mock.Mock()
    node.name = "fresh"
    with (
        mock.patch.object(evpn.models.Node, "objects") as nodes,
        mock.patch.object(catalog.Address, "objects") as ledger,
    ):
        nodes.get_all.return_value = []
        nodes.get_one_or_none.return_value = node
        ledger.get_all.return_value = []
        ledger.get_one_or_none.return_value = None
        (nodes if failed == "nodes" else ledger).get_all.side_effect = RuntimeError(
            "batch failed"
        )
        with pytest.raises(ValueError, match="comparison interrupted"):
            with driver.compare_ports(_subnet_stub(), [port]):
                if failed == "nodes":
                    assert driver._guest_name(port) == "fresh"
                    nodes.get_one_or_none.assert_called_once()
                else:
                    assert driver._public_address(port) is None
                    ledger.get_one_or_none.assert_called_once()
                raise ValueError("comparison interrupted")
        assert evpn._COMPARISON_READS.get() is None


@pytest.mark.parametrize("mode", ["bound", "ambiguous"])
def test_comparison_fip_lookup_preserves_selection_and_ambiguity(mode):
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    port = mock.Mock(uuid=sys_uuid.uuid4(), node=None)
    address = mock.Mock(
        uuid=sys_uuid.uuid4(),
        port=port.uuid,
        association=None,
        kind="floating",
    )
    with mock.patch.object(catalog.Address, "objects") as ledger:
        ledger.get_all.return_value = (
            [address, address] if mode == "ambiguous" else [address]
        )
        ledger.get_one_or_none.side_effect = evpn.ra_storage_exc.HasManyRecords(
            model=catalog.Address, filters={}
        )
        with driver.compare_ports(_subnet_stub(), [port]):
            if mode == "ambiguous":
                with pytest.raises(evpn.ra_storage_exc.HasManyRecords):
                    driver._public_address(port)
            else:
                assert driver._public_address(port) is address
                ledger.get_one_or_none.assert_not_called()


def _network(spec=None):
    network = mock.MagicMock()
    network.uuid = sys_uuid.uuid4()
    given = spec if spec is not None else {"driver": "ovs_evpn"}
    network.driver_spec = {k: v for k, v in given.items() if k in ("driver", "mtu")}
    vni = given.get("vni", 10000)
    network.driver_state = {
        "vni": vni,
        "rt": given.get("rt", "65001:%s" % vni),
        "mtu": given.get("mtu", 1450),
    }
    return network


def _subnet_stub(dhcp=True):
    subnet = mock.MagicMock()
    subnet.uuid = sys_uuid.uuid4()
    subnet.dhcp = dhcp
    return subnet


def _make_driver(spec=None):
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
        mock.patch(
            "exordos_core.network.contracts.applied_by_owner",
            side_effect=lambda target, actual, **kw: actual.node == target.agent,
        ),
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


def test_existing_allocation_untouched():
    """An allocated network keeps its VNI, RT and MTU.

    Allocation is reconciled by the Network model.
    """
    spec = {"driver": "ovs_evpn", "vni": 12345, "rt": "65001:12345", "mtu": 1400}
    driver = _make_driver(spec=dict(spec))

    stored = {"driver": "ovs_evpn", **driver._network.driver_state}
    assert {k: stored[k] for k in spec} == spec
    assert "vni_confirmed" not in stored


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
        # SG identities require storage; this test checks the compiled resource.
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
    assert set(port_dns) == {"kind", "config"}
    assert "169.254.169.254" in port_dns["config"]["destinations"]

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
        mock.patch.object(driver, "_find_subnet"),
        mock.patch.object(driver, "_collect_host_nfs"),
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs,
    ):
        objs.get_all.return_value = []  # no evpn_port left on the node
        driver.delete_port(port)

    port_res.delete.assert_called_once()
    port_res.update.assert_not_called()
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
        mock.patch.object(driver, "_find_subnet"),
        mock.patch.object(driver, "_collect_host_nfs"),
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as objs,
    ):
        objs.get_all.return_value = [mock.MagicMock()]  # another port remains
        driver.delete_port(port)

    port_res.delete.assert_called_once()
    port_res.update.assert_not_called()
    host_res.delete.assert_not_called()


# --- network functions ------------------------------------------------------


def _nf(kind, config=None, uuid=None, outputs=None):
    nf = mock.MagicMock()
    nf.uuid = uuid or sys_uuid.uuid4()
    nf.kind = kind
    nf.config = config or {}
    from exordos_core.user_api.network.dm import models as catalog

    nf.render_config.side_effect = lambda defaults: (
        catalog.NetworkFunction.render_config(nf, defaults)
    )
    nf.config = config or {}
    nf.render_config.side_effect = lambda defaults: (
        catalog.NetworkFunction.render_config(nf, defaults)
    )
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
    with (
        mock.patch.object(driver, "_compile_fips", return_value=[]),
        mock.patch.object(driver, "_dns_records", return_value=[]),
        mock.patch.object(
            type(driver), "_service_nfs", lambda self, subnet, seed=True: nfs
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
        if "SecurityGroup" in attrs:
            groups = attrs["SecurityGroup"].objects
            rules = stack.enter_context(
                mock.patch.object(net_api_models.SecurityGroupRule, "objects")
            )

            def rule_rows(filters, **kwargs):
                group = groups.get_one_or_none.return_value
                return [
                    mock.Mock(
                        security_group=sys_uuid.UUID(
                            filters["security_group"].value[0]
                        ),
                        rule_view=mock.Mock(return_value=rule),
                    )
                    for rule in group.rules
                ]

            rules.get_all.side_effect = rule_rows
        yield


def test_host_services_come_from_the_subnet_and_the_installation():
    """DHCP, the resolver and the proxy take no composed configuration:
    what to hand out is the subnet, who to ask upstream is `[evpn]`."""
    driver = _make_driver()
    port = mock.MagicMock()
    with _seeded_services(driver):
        services = driver._host_services(port, _subnet_stub())
    # Function settings live in their slices; per-guest intercept addresses
    # and door ports must participate in the port's own target hash.
    assert [s["kind"] for s in services] == ["dhcp", "dns", "proxy"]
    assert set(services[0]) == {"kind"}
    proxy = next(s for s in services if s["kind"] == "proxy")
    assert set(proxy) == {"kind", "config"}
    assert set(proxy["config"]) == {"ports", "destinations"}


@pytest.mark.parametrize("gateway", ["10.120.0.1", None])
def test_nf_intercept_addresses_are_metadata_and_the_actual_gateway(gateway):
    driver = _make_driver()
    subnet = _subnet_stub()
    routes = [{"to": "10.90.0.0/16", "via": "10.120.0.9"}]
    if gateway:
        routes.append({"to": "0.0.0.0/0", "via": gateway})
    subnet.routers = routes
    port = mock.MagicMock()
    with _seeded_services(driver):
        services = driver._host_services(port, subnet)
    expected = ["169.254.169.254"] + ([gateway] if gateway else [])
    for service in services:
        if service["kind"] in {"dns", "proxy"}:
            assert service["config"]["destinations"] == expected


def test_a_missing_service_is_simply_not_served():
    """Nothing is invented to fill a gap: a network whose proxy function was
    deleted compiles to a port without one, not to a default."""
    driver = _make_driver()
    port = mock.MagicMock()
    nfs = [_nf("dhcp", {"filename": evpn.DEFAULT_NETBOOT_URL})]
    with mock.patch.object(
        type(driver), "_service_nfs", lambda self, subnet, seed=True: nfs
    ):
        kinds = [s["kind"] for s in driver._host_services(port, _subnet_stub())]
    assert kinds == ["dhcp"]


def test_a_port_with_no_group_binding_has_no_security_filtering():
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
    port = mock.MagicMock()
    port.security_groups = []
    port.enforce_security_group = False
    assert driver._compile_security_rules(port, _subnet_stub()) == []


def test_attaching_a_group_compiles_only_its_explicit_rules():
    """Explicit SG binding permits only the group's declared traffic."""
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
    subnet = _subnet_stub()
    port = mock.MagicMock()
    port.security_groups = [sys_uuid.uuid4()]
    sg = mock.MagicMock()
    sg.rules = [{"direction": "ingress", "protocol": "tcp", "port": 22}]
    with (
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
        port.dhcp, port.dns = False, None
        assert [s["kind"] for s in driver._host_services(port, subnet)] == [
            "dns",
            "proxy",
        ]

        port.dhcp, port.dns = None, False
        assert [s["kind"] for s in driver._host_services(port, subnet)] == [
            "dhcp",
            "proxy",
        ]


def test_security_rules_keep_their_direction():
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
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
    port.security_groups = [sys_uuid.uuid4()]

    with _api_models(SecurityGroup=sg_objects):
        rules = driver._compile_security_rules(port)

    assert rules == [
        {"direction": "ingress", "proto": "tcp", "port": 443, "remote": "10.0.0.0/8"},
        {"direction": "egress", "proto": "any"},
    ]


def test_missing_security_group_keeps_the_port_filtered():
    """A group that vanished must not silently widen the port to allow-all."""
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
    sg_objects = mock.MagicMock()
    sg_objects.objects.get_one_or_none.return_value = None
    port = mock.MagicMock()
    port.security_groups = [sys_uuid.uuid4()]

    with _api_models(SecurityGroup=sg_objects):
        rules = driver._compile_security_rules(port)

    # non-empty (the agent installs the pipeline) but nothing is allowed
    assert rules and all(r["proto"] == "none" for r in rules)


def test_shared_policy_reads_are_scoped_to_port_comparison():
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
    subnet = _subnet_stub()
    subnet.network = driver._network.uuid
    sg_uuid, identity_uuid = sys_uuid.uuid4(), sys_uuid.uuid4()
    ports = [mock.Mock(security_groups=[sg_uuid]) for _ in range(3)]
    sg = mock.Mock()
    declared = [{"remote_group_id": identity_uuid, "port": port} for port in range(4)]
    group = mock.Mock(network=subnet.network, identity=4)
    sg_model, identity_model = mock.Mock(), mock.Mock()
    sg_model.objects.get_one_or_none.return_value = sg
    identity_model.objects.get_one_or_none.return_value = group
    with (
        _api_models(SecurityGroup=sg_model, SecurityGroupIdentity=identity_model),
        mock.patch.object(
            type(sg), "rules", new_callable=mock.PropertyMock, create=True
        ) as rules,
    ):
        rules.return_value = declared
        with driver.compare_ports(subnet):
            for port in ports:
                compiled = driver._compile_security_rules(port, subnet, seed=False)
                assert len(compiled) == 4
                assert all(rule["remote_identity"] == 4 for rule in compiled)
        sg_model.objects.get_one_or_none.assert_called_once()
        identity_model.objects.get_one_or_none.assert_called_once()
        rules.assert_called_once()

        rules.return_value = []
        with driver.compare_ports(subnet):
            for port in ports:
                assert driver._compile_security_rules(port, subnet, seed=False) == list(
                    driver.DENY_ALL
                )
        assert sg_model.objects.get_one_or_none.call_count == 2
        assert rules.call_count == 2

        rules.return_value = declared
        identity_model.objects.get_one_or_none.return_value = None
        with driver.compare_ports(subnet):
            for port in ports:
                assert all(
                    rule["proto"] == "none"
                    for rule in driver._compile_security_rules(port, subnet, seed=False)
                )
        assert identity_model.objects.get_one_or_none.call_count == 2


def test_comparison_shares_network_inputs_and_clears_them_after_failure():
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = driver._network.uuid
    with (
        mock.patch.object(catalog.NetworkFunction, "objects") as nfs,
        mock.patch.object(catalog.SecurityGroupIdentity, "objects") as identities,
        mock.patch.object(catalog.ProxyService, "objects") as proxies,
        mock.patch.object(evpn.models.Subnet, "objects") as subnets,
    ):
        nfs.get_all.return_value = []
        identities.get_one_or_none.return_value = None
        proxies.get_all.return_value = []
        subnets.get_one_or_none.return_value = subnet
        with pytest.raises(ValueError, match="interrupted comparison"):
            with driver.compare_ports(subnet):
                for _ in range(3):
                    assert driver._find_subnet(subnet.uuid) is subnet
                    assert driver._service_nfs(subnet, seed=False) == []
                    assert (
                        driver._security_group_identity(
                            subnet.network, sys_uuid.UUID(int=1), seed=False
                        )
                        is None
                    )
                    assert evpn._proxy_service_ports(subnet.network) == []
                raise ValueError("interrupted comparison")
        assert nfs.get_all.call_count == 2
        identities.get_one_or_none.assert_called_once()
        proxies.get_all.assert_called_once()

        driver._service_nfs(subnet, seed=False)
        assert nfs.get_all.call_count == 4


def test_shared_dhcp_inputs_keep_each_guests_name_separate():
    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.cidr = "10.42.0.0/24"
    subnet.dump_to_simple_view.return_value = {}
    with driver.compare_ports(subnet):
        first = driver._dhcp_record(subnet)
        first["name"] = "first"
        second = driver._dhcp_record(subnet)
        assert "name" not in second
        second["name"] = "second"
        assert first["name"] == "first"
    subnet.dump_to_simple_view.assert_not_called()


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


def test_published_hosts_share_one_ledger_read():
    driver = _make_driver()
    hosts = [mock.Mock(uuid=sys_uuid.uuid4()) for _ in range(3)]
    with (
        _with_published(["10.20.3.1"]) as published,
        mock.patch.object(evpn.ua_models.TargetResource, "objects") as targets,
        mock.patch.object(driver, "_configured_or_core", return_value=[]),
        mock.patch.object(driver, "_write_resource", return_value=None) as write,
    ):
        targets.get_all.return_value = hosts
        driver._refresh_published_hosts()

    published.assert_called_once()
    assert write.call_count == len(hosts)
    assert all(
        call.args[0].published_addresses == ["10.20.3.1"]
        for call in write.call_args_list
    )


@pytest.mark.parametrize("failed", [False, True])
def test_network_iteration_runs_global_work_once_for_multiple_networks(failed):
    from exordos_core.network import service

    drivers = [_make_driver(), _make_driver()]
    network_map = {driver._network: {} for driver in drivers}
    for driver in drivers:
        driver._network.load_driver.return_value = driver
    reconciler = object.__new__(service.NetworkService)
    with (
        mock.patch.object(service.contexts, "Context"),
        mock.patch.object(reconciler, "_get_subnet_map", return_value={}),
        mock.patch.object(reconciler, "_collect_orphan_nf_targets"),
        mock.patch.object(reconciler, "_get_new_hw_ports", return_value=[]),
        mock.patch.object(reconciler, "_build_network_map", return_value=network_map),
        mock.patch.object(evpn.OvsEvpnNetworkDriver, "_ensure_agent_offers_overlay"),
        mock.patch.object(evpn.OvsEvpnNetworkDriver, "_ensure_rr"),
        mock.patch.object(
            evpn.OvsEvpnNetworkDriver,
            "_actualize_statuses",
            side_effect=ValueError("unreadable facts") if failed else None,
        ) as statuses,
        mock.patch.object(
            evpn.OvsEvpnNetworkDriver, "_refresh_published_hosts"
        ) as hosts,
        mock.patch.object(
            evpn.OvsEvpnNetworkDriver, "_list_resources", return_value=[]
        ),
    ):
        reconciler._iteration()
        reconciler._iteration()

    assert statuses.call_count == 2
    assert hosts.call_count == (0 if failed else 2)


def test_floating_addresses_are_published():
    held = mock.MagicMock(address="10.20.3.1")
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

    assert captured["kind"].value == "floating"


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


def test_withdrawn_nf_is_not_republished_at_reconcile_exit():
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = driver._network.uuid
    host = sys_uuid.uuid4()
    nf = _nf("dns", {})
    target = mock.MagicMock()
    with (
        mock.patch.object(driver, "_dns_records", return_value=[]),
        mock.patch.object(driver, "_service_nfs", return_value=[nf]),
        mock.patch.object(driver, "_list_resources", return_value=[]),
        mock.patch.object(
            evpn.ua_models.TargetResource,
            "objects",
            mock.Mock(get_all=mock.Mock(return_value=[])),
        ),
        mock.patch.object(
            catalog.NetworkFunction,
            "objects",
            mock.Mock(get_all=mock.Mock(return_value=[nf])),
        ),
        mock.patch.object(driver, "_get_resource", return_value=target),
        mock.patch.object(driver, "_write_resource") as write,
    ):
        with driver.reconcile():
            driver._ensure_host_nfs(host, subnet)
            driver._collect_host_nfs(host, subnet)
        write.assert_not_called()
        assert target.delete.called


def test_delete_subnet_collects_nf_targets_without_remaining_ports():
    driver = _make_driver()
    subnet = _subnet_stub()
    host = sys_uuid.uuid4()
    target = mock.MagicMock(agent=host)
    with (
        mock.patch.object(
            driver,
            "_list_resources",
            side_effect=lambda kind, owner: [target] if kind == "evpn_nf" else [],
        ),
        mock.patch.object(driver, "_get_resource"),
        mock.patch.object(driver, "_collect_host_nfs") as collect_nfs,
        mock.patch.object(driver, "_collect_host") as collect_host,
    ):
        driver.delete_subnet(subnet)
    collect_nfs.assert_called_once_with(host, subnet)
    collect_host.assert_called_once_with(host)


def test_delete_subnet_collects_targets_without_host_ack():
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
        mock.patch.object(driver, "_collect_host_nfs"),
    ):
        driver.delete_subnet(subnet)

    collect.assert_called_once_with(node)
    port_res.delete.assert_called_once_with()


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


def test_an_attached_empty_group_compiles_to_deny_all():
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
    port = mock.MagicMock()
    port.security_groups = [sys_uuid.uuid4()]
    with _api_models(
        SecurityGroup=mock.MagicMock(
            **{"objects.get_one_or_none.return_value": mock.MagicMock(rules=[])}
        )
    ):
        assert driver._compile_security_rules(port, _subnet_stub()) == list(
            driver.DENY_ALL
        )


def _seed_probe(*existing):
    """A NetworkFunction stand-in that answers the seeding path per kind.

    Seeding asks once per (kind, owner); one row for every ask would have the
    `proxy` pass rewrite the `dns` row.
    """
    by_kind = {nf.kind: nf for nf in existing}
    model = mock.MagicMock()
    model.objects.get_one_or_none.side_effect = lambda filters: by_kind.get(
        filters["kind"].value
    )
    return model


def test_every_default_key_is_one_the_model_accepts():
    from exordos_core.user_api.network.dm import models as catalog

    driver = _make_driver()
    subnet = _subnet_stub()
    with (
        mock.patch.object(evpn, "_proxy_services", return_value={}),
        mock.patch.object(evpn, "_proxy_service_domains", return_value=[]),
    ):
        defaults = driver._nf_defaults(subnet)
    for kind, config in defaults.items():
        assert not set(config) - set(catalog.NetworkFunction._CONFIG_SCHEMA[kind])


def test_asking_whether_a_port_is_stale_writes_nothing():
    """Checking a port for staleness is a read and seeds nothing.

    It runs for every port on every loop; anything unseeded just compiles to a
    difference, and the next update seeds it.
    """
    driver = _make_driver()
    subnet = _subnet_stub()
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.node = None
    port.name = "probe"
    port.security_groups = []
    port.mac = "52:54:00:aa:bb:cc"
    port.ipv4 = netaddr.IPAddress("10.42.0.7")
    port.port_security = True

    seeded = []
    with (
        mock.patch.object(
            type(driver), "_seed_service_nfs", lambda self, s: seeded.append(s)
        ),
        _api_models(
            Address=mock.MagicMock(**{"objects.get_one_or_none.return_value": None}),
            NetworkFunction=mock.MagicMock(
                **{"objects.get_all.return_value": []},
            ),
            SecurityGroupIdentity=mock.MagicMock(
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
                compute_models.Node, "objects", **{"get_all.return_value": [node]}
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
        "_security_group_identity",
        staticmethod(lambda network, _uuid, seed=False: group),
    ):
        compiled = driver._compile_rule(
            {"direction": "ingress", "protocol": "tcp", "remote_group_id": group.uuid},
            network=sys_uuid.uuid4(),
        )

    assert compiled == {"direction": "ingress", "proto": "tcp", "remote_identity": 4}


def test_the_comparing_compile_allocates_no_floating_address():
    """The compiler only reads a bound Address; allocation belongs to creation."""
    driver = _make_driver()
    port = mock.MagicMock()
    address_objects = mock.MagicMock()
    address_objects.objects.get_one_or_none.return_value = None
    with _api_models(Address=address_objects):
        assert driver._public_address(port) is None
    address_objects.assert_not_called()


@pytest.mark.parametrize("bound", [False, True])
def test_empty_bound_group_denies_but_no_binding_allows(bound):
    driver = _make_driver()
    driver._port_security_groups = lambda port: port.security_groups
    port = mock.MagicMock()
    port.enforce_security_group = False
    port.security_groups = [sys_uuid.uuid4()] if bound else []
    group = mock.MagicMock(rules=[])
    group.config = {"rules": []}
    with (
        _api_models(
            SecurityGroup=mock.MagicMock(
                **{"objects.get_one_or_none.return_value": group}
            )
        ),
    ):
        rules = driver._compile_security_rules(port, _subnet_stub())
    assert rules == (list(driver.DENY_ALL) if bound else [])


def test_service_defaults_are_transient_and_cached_only_per_reconcile():
    driver = _make_driver()
    first, second = _subnet_stub(), _subnet_stub()
    first.network = second.network = driver._network.uuid
    with (
        mock.patch.object(driver, "_configured_or_core", return_value=["10.0.0.2"]),
        mock.patch.object(
            evpn,
            "_default_proxy_forwards",
            side_effect=lambda subnet: [str(subnet.uuid)],
        ) as forwards,
        mock.patch.object(evpn, "_proxy_services", return_value={}),
        mock.patch.object(evpn, "_proxy_service_domains", return_value=[]),
    ):
        for _ in range(2):
            with driver.reconcile():
                for subnet in (first, second, first):
                    defaults = driver._nf_defaults(subnet)
                    assert defaults["dns"]["forwarders"] == ["10.0.0.2"]
                    assert defaults["proxy"]["forwards"] == [str(subnet.uuid)]
        assert forwards.call_count == 4
        driver._nf_defaults(first)
        assert forwards.call_count == 5


@pytest.mark.parametrize("index", [0, 1, None])
def test_only_primary_port_advertises_dhcp_default_without_changing_host_routes(index):
    driver = _make_driver()
    subnet = evpn.models.Subnet.restore_from_simple_view(**_subnet_view())
    port = mock.Mock(
        uuid=sys_uuid.uuid4(),
        node=None,
        index=index,
        security_groups=[],
        mac="52:54:00:aa:bb:cc",
        ipv4=netaddr.IPAddress("10.42.0.7"),
        port_security=True,
    )
    port.name = "multi-nic"
    with (
        _seeded_services(driver),
        mock.patch.object(
            driver, "_compile_fips", return_value=[{"public": "203.0.113.7"}]
        ),
    ):
        compiled = driver._build_evpn_port(port, subnet, publish=False)
    assert compiled.dhcp["default_route"] is (index == 0)
    assert compiled.dhcp["routers"] == [{"to": "0.0.0.0/0", "via": "10.42.0.254"}]
    assert compiled.fips == [{"public": "203.0.113.7"}]
    assert "default_route" not in driver._dhcp_record(subnet)


def test_dns_slices_publish_network_names_once_without_caller_metadata():
    driver = _make_driver()
    subnet = _subnet_stub()
    subnet.network = driver._network.uuid
    other_subnet = sys_uuid.uuid4()
    alpha, beta = sys_uuid.uuid4(), sys_uuid.uuid4()
    ports = [
        mock.Mock(
            uuid=sys_uuid.uuid4(),
            subnet=subnet.uuid,
            node=alpha,
            ipv4="10.42.0.7",
            dns=True,
        ),
        mock.Mock(
            uuid=sys_uuid.uuid4(),
            subnet=other_subnet,
            node=beta,
            ipv4="10.43.0.7",
            dns=None,
        ),
        mock.Mock(
            uuid=sys_uuid.uuid4(),
            subnet=subnet.uuid,
            node=beta,
            ipv4="10.42.0.9",
            dns=False,
        ),
    ]
    ports[0].name = "alpha"
    ports[1].name = None
    ports[2].name = "disabled"
    node = mock.Mock(uuid=beta)
    node.name = "beta"
    dns = _nf("dns", {"zone_suffix": "corp"})
    with (
        mock.patch.object(evpn.models.Subnet, "objects") as subnets,
        mock.patch.object(evpn.models.Port, "objects") as port_rows,
        mock.patch.object(evpn.models.Node, "objects") as nodes,
        mock.patch.object(driver, "_nf_defaults", return_value={}),
        mock.patch.object(driver, "_service_nfs", return_value=[dns]),
        mock.patch.object(driver, "_write_resource") as write,
    ):
        subnets.get_all.return_value = [subnet, mock.Mock(uuid=other_subnet)]
        port_rows.get_all.return_value = ports
        nodes.get_all.return_value = [node]
        with driver.reconcile():
            driver._ensure_host_nfs(alpha, subnet)
            driver._ensure_host_nfs(beta, subnet)
        expected = [
            {"name": "alpha", "ipv4": "10.42.0.7"},
            {"name": "beta", "ipv4": "10.43.0.7"},
        ]
        assert len(write.call_args_list) == 2
        for call in write.call_args_list:
            assert call.args[0].config["records"] == expected
            assert call.args[0].config["zone_suffix"] == "corp"
        subnets.get_all.assert_called_once()
        assert (
            subnets.get_all.call_args.kwargs["filters"]["network"].value
            == driver._network.uuid
        )
        port_rows.get_all.assert_called_once()
        assert set(port_rows.get_all.call_args.kwargs["filters"]["subnet"].value) == {
            subnet.uuid,
            other_subnet,
        }
        nodes.get_all.assert_called_once()
        nodes.get_one_or_none.assert_not_called()


def test_orphan_nf_gc_uses_subnet_and_host_not_reused_vni():
    from exordos_core.network import service

    reconciler = object.__new__(service.NetworkService)
    subnet = _subnet_stub()
    neighbor = _subnet_stub()
    neighbor.network = subnet.network
    host, idle_host = sys_uuid.uuid4(), sys_uuid.uuid4()
    port = mock.Mock(agent=host, master=neighbor.uuid)
    orphan = mock.Mock(
        agent=host, master=sys_uuid.uuid4(), value={"kind": "dns", "vni": 10005}
    )
    idle = mock.Mock(
        agent=idle_host, master=subnet.uuid, value={"kind": "dns", "vni": 10005}
    )
    dhcp = mock.Mock(
        agent=host, master=subnet.uuid, value={"kind": "dhcp", "vni": 10005}
    )
    shared = mock.Mock(
        agent=host, master=subnet.uuid, value={"kind": "dns", "vni": 10005}
    )
    current = mock.Mock(
        agent=host, master=neighbor.uuid, value={"kind": "dhcp", "vni": 10005}
    )
    with mock.patch.object(
        service.ua_models.TargetResource,
        "objects",
        mock.Mock(
            get_all=mock.Mock(
                side_effect=[[port], [orphan, idle, dhcp, shared, current]]
            )
        ),
    ):
        reconciler._collect_orphan_nf_targets({subnet: [], neighbor: []})
    for target in (orphan, idle, dhcp):
        target.delete.assert_called_once_with()
    shared.delete.assert_not_called()
    current.delete.assert_not_called()


def test_shared_host_dhcp_slices_keep_their_subnet_selector_and_settings():
    driver = _make_driver()
    host = sys_uuid.uuid4()
    compiled = []
    expected = []
    with mock.patch.object(
        driver, "_write_resource", side_effect=lambda nf, **kw: compiled.append(nf)
    ):
        for lease in (600, 900):
            subnet = _subnet_stub()
            subnet.cidr = netaddr.IPNetwork("10.42.0.0/24")
            driver._ensure_host_nfs(
                host, subnet, [_nf("dhcp", {"lease_seconds": lease})]
            )
            expected.append((str(subnet.uuid), lease))
            assert driver._dhcp_record(subnet)["_subnet_uuid"] == str(subnet.uuid)
    assert [
        (nf.config["_subnet_uuid"], nf.config["lease_seconds"]) for nf in compiled
    ] == expected
    assert compiled[0].uuid != compiled[1].uuid
