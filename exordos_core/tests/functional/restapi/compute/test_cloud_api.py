# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import uuid

from bazooka import exceptions
import pytest
from restalchemy.dm import filters

from exordos_core.cloud.links import CloudNetworkLink
from exordos_core.compute.dm import models as compute
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import create
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import resource


@pytest.fixture
def client(user_api_client, auth_user_admin, user_api, monkeypatch):
    from exordos_core.compute.dm.models import MachinePool as CloudConnection

    monkeypatch.setattr(
        CloudConnection,
        "_authenticate",
        lambda self: {"project_id": "provider-project"},
    )
    yield user_api_client(auth_user_admin)
    user_api._restore_seed_snapshot()


def connection(client, **kwargs):
    spec = kwargs.pop(
        "driver_spec",
        {
            "kind": "openstack",
            "auth_url": "https://identity.example/v3",
            "auth": {
                "application_credential_id": "test",
                "application_credential_secret": "private-sentinel",
            },
        },
    )
    for field in ("auth_url", "auth_type", "auth", "region", "interface"):
        if field in kwargs:
            spec[field] = kwargs.pop(field)
    return create(client, ("compute", "hypervisors"), driver_spec=spec, **kwargs)


def network(client, config):
    return create(
        client,
        ("network", "networks"),
        project_id=config["project_id"],
        driver_spec={"driver": "cloud", "connection": config["uuid"]},
    )


def test_public_driver_settings_keep_credentials_private(client):
    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    result = client.post(
        path.rstrip("/") + "/actions/update_driver_settings/invoke",
        json={"settings": {"volume_type": "fast", "key_name": "operator"}},
    ).json()
    assert result["driver_spec"]["volume_type"] == "fast"
    assert result["driver_spec"]["key_name"] == "operator"
    assert "auth" not in result["driver_spec"]
    from exordos_core.compute.dm.models import MachinePool

    saved = MachinePool.objects.get_one(filters={"uuid": filters.EQ(uuid.UUID(config["uuid"]))})
    assert saved.driver_spec.auth["application_credential_secret"] == "private-sentinel"


@pytest.mark.parametrize("settings", [
    {"auth": {}}, {"auth_url": "https://other/v3"}, {"region": "other"},
    {"kind": "libvirt"}, {"volume_type": 42},
])
def test_public_driver_settings_reject_credential_identity_and_invalid_fields(client, settings):
    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    with pytest.raises(exceptions.BadRequestError):
        client.post(path.rstrip("/") + "/actions/update_driver_settings/invoke", json={"settings": settings})
    assert client.get(path).json()["driver_spec"] == config["driver_spec"]


def test_driver_setting_update_does_not_restore_stale_credentials(client):
    from exordos_core.compute.dm.models import MachinePool

    config = connection(client)
    query = {"uuid": filters.EQ(uuid.UUID(config["uuid"]))}
    stale = MachinePool.objects.get_one(filters=query)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    replacement = dict(config["driver_spec"], auth={
        "application_credential_id": "replacement", "application_credential_secret": "rotated-sentinel",
    })
    client.put(path, json={"driver_spec": replacement})
    stale.update_driver_settings(settings={"volume_type": "fast"})
    saved = MachinePool.objects.get_one(filters=query)
    assert saved.driver_spec.volume_type == "fast"
    assert saved.driver_spec.auth == replacement["auth"]


def test_credential_action_keeps_settings_changed_after_the_clients_read(client):
    from exordos_core.compute.dm.models import MachinePool

    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    client.post(path.rstrip("/") + "/actions/update_driver_settings/invoke", json={"settings": {"volume_type": "fast", "key_name": "operator"}})
    auth = {"application_credential_id": "replacement", "application_credential_secret": "rotated-sentinel"}
    result = client.post(path.rstrip("/") + "/actions/update_credentials/invoke", json={"auth_type": "v3applicationcredential", "auth": auth}).json()
    saved = MachinePool.objects.get_one(filters={"uuid": filters.EQ(uuid.UUID(config["uuid"]))})
    assert saved.driver_spec.volume_type == "fast"
    assert saved.driver_spec.key_name == "operator"
    assert saved.driver_spec.auth == auth
    assert "auth" not in result["driver_spec"]


def test_credential_action_requires_update_permission(client, auth_test1_user, user_api_client):
    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    with pytest.raises(exceptions.ForbiddenError):
        user_api_client(auth_test1_user).post(path.rstrip("/") + "/actions/update_credentials/invoke", json={"auth_type": "v3applicationcredential", "auth": {}})


def test_public_driver_settings_require_hypervisor_update_permission(client, auth_test1_user, user_api_client):
    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    unprivileged = user_api_client(auth_test1_user)
    with pytest.raises(exceptions.ForbiddenError):
        unprivileged.post(path.rstrip("/") + "/actions/update_driver_settings/invoke", json={"settings": {"volume_type": "fast"}})
    assert client.get(path).json()["driver_spec"] == config["driver_spec"]


def test_public_driver_settings_do_not_apply_to_native_hypervisors(client):
    config = connection(client, driver_spec={"kind": "libvirt", "connection_uri": "qemu:///system"})
    path = resource(client, "compute", "hypervisors", config["uuid"])
    with pytest.raises(exceptions.BadRequestError):
        client.post(path.rstrip("/") + "/actions/update_driver_settings/invoke", json={"settings": {"volume_type": "fast"}})


def test_cloud_connection_persists_and_pool_resolves_only_its_configuration(client):
    config = connection(client)
    net = network(client, config)
    pool = config
    assert pool["uuid"] == config["uuid"]
    assert "auth" not in config["driver_spec"]
    assert config["driver_state"] == {"project_id": "provider-project"}
    assert net["driver_state"] == {}
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "compute", "hypervisors", config["uuid"]))


def test_network_cannot_reference_another_projects_cloud_account(client):
    config = connection(client, project_id=str(uuid.uuid4()))
    with pytest.raises(exceptions.NotFoundError):
        create(
            client,
            ("network", "networks"),
            driver_spec={"driver": "cloud", "connection": config["uuid"]},
        )


def link_endpoints(client, count=2, cidrs=None):
    result = []
    for index in range(count):
        config = connection(client)
        net = network(client, config)
        subnet = create(
            client,
            ("network", "subnets"),
            network=net["uuid"],
            cidr=cidrs[index] if cidrs else f"10.244.{index}.0/24",
        )
        result.append(
            {
                "subnet": subnet["uuid"],
                "external_network": str(uuid.uuid4()),
                "gateway_image": "ubuntu-24.04",
            }
        )
    return result


@pytest.mark.parametrize("server_first", [False, True])
def test_link_accepts_explicit_server_and_client_roles(client, server_first):
    endpoints = link_endpoints(client)
    endpoints[0]["role"], endpoints[1]["role"] = "client", "server"
    if server_first:
        endpoints.reverse()
    result = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    assert [e["role"] for e in result["endpoints"]] == [e["role"] for e in endpoints]
    with pytest.raises(exceptions.ConflictError):
        client.put(
            resource(client, "compute", "cloud_network_links", result["uuid"]),
            json={"endpoints": [dict(e, role="auto") for e in endpoints]},
        )


@pytest.mark.parametrize("role", ["server", "client", "invalid", None, []])
def test_link_rejects_invalid_or_incompatible_mesh_roles(client, role):
    endpoints = [dict(e, role=role) for e in link_endpoints(client)]
    with pytest.raises(exceptions.BadRequestError):
        create(client, ("compute", "cloud_network_links"), endpoints=endpoints)


def test_outbound_only_local_endpoint_needs_no_public_address(client):
    from gcl_sdk.agents.universal.dm import models as ua

    from exordos_core.cloud.links import CloudLinkService
    from exordos_core.cloud.links import endpoints_for_subnet

    cloud = dict(link_endpoints(client)[0], role="server")
    network = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    subnet = create(
        client, ("network", "subnets"), network=network["uuid"], cidr="10.246.0.0/24"
    )
    agent = ua.UniversalAgent(
        uuid=uuid.uuid4(),
        node=uuid.uuid4(),
        name="outbound-router",
        capabilities={"capabilities": ["cloud_net_link"]},
        facts={"facts": []},
    )
    compute.Node(
        uuid=agent.node, project_id=uuid.UUID(subnet["project_id"]),
        name="local-router", node_type="HW", cores=1, ram=512, disk_spec=compute.infra_models.DisksSpec(disks=[]),
    ).insert()
    agent.insert()
    result = create(
        client,
        ("compute", "cloud_network_links"),
        endpoints=[
            cloud,
            {"subnet": subnet["uuid"], "agent": str(agent.uuid), "role": "client"},
        ],
    )
    endpoint = endpoints_for_subnet(uuid.UUID(subnet["uuid"]))[0]
    link = CloudNetworkLink.objects.get_one(
        filters={"uuid": filters.EQ(result["uuid"])}
    )
    assert CloudLinkService()._reserve(link, endpoint, []) == {
        "agent": str(agent.uuid),
        "endpoint": None,
    }


def test_link_is_symmetric_hides_secrets_and_retains_cleanup_record(client):
    endpoints = link_endpoints(client)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    assert "secrets" not in link
    assert "local_endpoint" not in link and "connection" not in link
    assert [e["subnet"] for e in link["endpoints"]] == [e["subnet"] for e in endpoints]
    assert link["transport"] == {"driver": "openvpn", "protocol": "tcp", "port": 443}
    client.delete(resource(client, "compute", "cloud_network_links", link["uuid"]))
    retained = client.get(
        resource(client, "compute", "cloud_network_links", link["uuid"])
    ).json()
    assert retained["status"] == "DELETING"
    assert "secrets" not in retained


def test_stale_reconciler_cannot_cancel_an_accepted_delete(client):
    link = create(
        client, ("compute", "cloud_network_links"), endpoints=link_endpoints(client)
    )
    stale = CloudNetworkLink.objects.get_one(filters={"uuid": filters.EQ(link["uuid"])})
    client.delete(resource(client, "compute", "cloud_network_links", link["uuid"]))
    stale.observed(status="IN_PROGRESS", state={"gateway_ip": "10.244.0.5"})
    retained = client.get(
        resource(client, "compute", "cloud_network_links", link["uuid"])
    ).json()
    assert retained["status"] == "DELETING"
    assert retained["state"]["gateway_ip"] == "10.244.0.5"


@pytest.mark.parametrize(
    "cidrs", [["10.244.0.0/24", "10.244.0.0/25"], ["10.244.0.0/24", "0.0.0.0/0"]]
)
def test_link_rejects_default_and_overlapping_subnets(client, cidrs):
    endpoints = link_endpoints(client, cidrs=cidrs)
    with pytest.raises(exceptions.BadRequestError):
        create(client, ("compute", "cloud_network_links"), endpoints=endpoints)


@pytest.mark.parametrize(
    "change",
    [
        {"subnet": "invalid"},
        {"external_network": "bad"},
        {"gateway_cores": 0},
        {"gateway_ram": 1},
        {"unknown": "field"},
    ],
)
def test_link_reports_invalid_topology_as_bad_request(client, change):
    endpoints = link_endpoints(client)
    endpoints[0].update(change)
    with pytest.raises(exceptions.BadRequestError):
        create(client, ("compute", "cloud_network_links"), endpoints=endpoints)


def test_link_subnets_are_owned_until_cleanup_finishes(client):
    from exordos_core.cloud.links import endpoints_for_subnet

    endpoints = link_endpoints(client, count=3)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    path = resource(client, "compute", "cloud_network_links", link["uuid"])
    updated = client.put(path, json={"endpoints": link["endpoints"][:2]}).json()
    assert updated["status"] == "IN_PROGRESS"
    assert len(endpoints_for_subnet(uuid.UUID(endpoints[2]["subnet"]))) == 1
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "network", "subnets", endpoints[2]["subnet"]))
    with pytest.raises(exceptions.ConflictError):
        create(client, ("compute", "cloud_network_links"), endpoints=endpoints)


def test_link_cannot_attach_another_projects_subnet(client):
    endpoints = link_endpoints(client)
    with pytest.raises(exceptions.NotFoundError):
        create(
            client,
            ("compute", "cloud_network_links"),
            endpoints=endpoints,
            project_id=str(uuid.uuid4()),
        )


def test_observed_state_does_not_revert_new_membership(client):
    endpoints = link_endpoints(client, count=3)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints[:2])
    stale = CloudNetworkLink.objects.get_one(filters={"uuid": filters.EQ(link["uuid"])})
    path = resource(client, "compute", "cloud_network_links", link["uuid"])
    client.put(path, json={"endpoints": endpoints})
    stale.observed(status="ACTIVE", state={})
    current = client.get(path).json()
    assert len(current["endpoints"]) == 3
    assert current["status"] == "IN_PROGRESS"


def test_connection_delete_serializes_with_dependent_network_insert(client):
    import concurrent.futures
    import time

    from restalchemy.storage import exceptions as storage_exc
    from restalchemy.storage.sql import engines

    from exordos_core.compute.dm.models import Network

    config = connection(client)
    engine = engines.engine_factory.get_engine()
    handle = engine.get_connection()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        handle.execute(
            "SELECT pg_advisory_xact_lock(1129074517, hashtext(%s))", (config["uuid"],)
        )
        candidate = Network(
            project_id=uuid.UUID(config["project_id"]),
            driver_spec={"driver": "cloud", "connection": config["uuid"]},
        )
        future = executor.submit(candidate.insert)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            blocked = handle.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=1129074517 AND NOT granted"
            ).fetchone()[0]
            if blocked:
                break
            if future.done():
                future.result()
                pytest.fail(
                    "Dependent insertion did not wait for connection lifecycle lock"
                )
            time.sleep(0.01)
        else:
            pytest.fail("Dependent insertion never reached the lifecycle lock")
        handle.execute("DELETE FROM machine_pools WHERE uuid=%s", (config["uuid"],))
        handle.commit()
        with pytest.raises(storage_exc.RecordNotFound):
            future.result(timeout=10)
        assert (
            Network.objects.get_one_or_none(
                filters={"uuid": filters.EQ(candidate.uuid)}
            )
            is None
        )
    finally:
        handle.rollback()
        engine.close_connection(handle)
        executor.shutdown(wait=True)


def test_cloud_reconciliation_refreshes_subnets_after_waiting_for_provider_lock(
    client, monkeypatch
):
    import concurrent.futures
    import contextlib
    import threading
    from unittest import mock

    from exordos_core.compute.dm.models import Network
    from exordos_core.network.service import NetworkService

    config = connection(client)
    net = network(client, config)
    subnet = create(
        client, ("network", "subnets"), network=net["uuid"], cidr="10.244.0.0/24"
    )
    waiting, proceed = threading.Event(), threading.Event()
    driver = mock.Mock()

    @contextlib.contextmanager
    def reconcile():
        waiting.set()
        assert proceed.wait(10)
        yield

    driver.reconcile.side_effect = reconcile
    monkeypatch.setattr(Network, "load_driver", lambda self: driver)
    service = NetworkService()
    monkeypatch.setattr(service, "_get_new_hw_ports", lambda subnets: [])
    actualize = mock.Mock()
    monkeypatch.setattr(service, "_actualize_network", actualize)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service._iteration)
        try:
            assert waiting.wait(10)
            client.delete(resource(client, "network", "subnets", subnet["uuid"]))
        finally:
            proceed.set()
        future.result(timeout=10)
    actualize.assert_called_once()
    assert actualize.call_args.args[1] == {}


def test_cloud_pool_cannot_disappear_before_its_volume_is_cleaned_up(client):
    from gcl_sdk.agents.universal.dm import models as ua

    from exordos_core.compute.dm.models import Volume

    config = connection(client)
    pool = config
    volume = Volume(
        size=1, pool=uuid.UUID(pool["uuid"]), project_id=uuid.UUID(config["project_id"])
    )
    volume.insert()
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "compute", "hypervisors", pool["uuid"]))
    volume.delete()
    pending = ua.TargetResource(
        uuid=volume.uuid,
        kind="pool_volume",
        value={"uuid": str(volume.uuid), "pool": pool["uuid"]},
    )
    pending.calculate_hash()
    pending.insert()
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "compute", "hypervisors", pool["uuid"]))
    pending.delete()
    client.delete(resource(client, "compute", "hypervisors", pool["uuid"]))


def test_cloud_pool_delete_holds_foreign_key_barrier_until_commit(client, monkeypatch):
    import concurrent.futures
    import threading
    import time

    from restalchemy.common import contexts
    from restalchemy.storage.sql import engines
    from restalchemy.storage.sql import orm

    from exordos_core.compute.dm.models import MachinePool
    from exordos_core.compute.dm.models import Volume

    config = connection(client)
    pool = config
    model = MachinePool.objects.get_one(filters={"uuid": filters.EQ(pool["uuid"])})
    candidate = Volume(
        size=1, pool=model.uuid, project_id=uuid.UUID(config["project_id"])
    )
    before_delete, proceed, inserting = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    original_delete = orm.SQLStorableMixin.delete
    backend = []

    def pause_delete(self, *args, **kwargs):
        if isinstance(self, MachinePool):
            before_delete.set()
            assert proceed.wait(10)
        return original_delete(self, *args, **kwargs)

    monkeypatch.setattr(orm.SQLStorableMixin, "delete", pause_delete)

    def insert_volume():
        with contexts.Context().session_manager() as session:
            backend.append(
                session.execute("SELECT pg_backend_pid()").fetchone()["pg_backend_pid"]
            )
            inserting.set()
            candidate.insert(session=session)

    engine = engines.engine_factory.get_engine()
    handle = engine.get_connection()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        deletion = executor.submit(model.delete)
        insertion = None
        try:
            assert before_delete.wait(10)
            insertion = executor.submit(insert_volume)
            assert inserting.wait(10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if insertion.done():
                    insertion.result()
                    pytest.fail(
                        "Concurrent workload bypassed the pool deletion barrier"
                    )
                if (
                    handle.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                        (backend[0],),
                    ).fetchone()[0]
                    == "Lock"
                ):
                    break
                handle.commit()
                time.sleep(0.01)
            else:
                pytest.fail("Workload insertion never blocked on pool deletion")
        finally:
            handle.rollback()
            engine.close_connection(handle)
            proceed.set()
        deletion.result(timeout=10)
        with pytest.raises(Exception, match="foreign key"):
            insertion.result(timeout=10)
    assert (
        Volume.objects.get_one_or_none(filters={"uuid": filters.EQ(candidate.uuid)})
        is None
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"auth": {}},
        {
            "auth": {
                "application_credential_id": "test",
                "application_credential_secret": "",
            }
        },
        {"auth_url": "file:///etc/passwd"},
        {
            "auth": {
                "application_credential_id": "test",
                "application_credential_secret": "secret",
                "endpoint_override": "http://other",
            }
        },
    ],
)
def test_connection_rejects_invalid_authentication(client, updates):
    with pytest.raises(exceptions.BadRequestError):
        connection(client, **updates)


def test_gateway_recovery_is_queued_without_needing_the_remote_agent(client):
    from exordos_core.cloud.links import endpoints_for_subnet

    endpoints = link_endpoints(client)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    model = CloudNetworkLink.objects.get_one(filters={"uuid": filters.EQ(link["uuid"])})
    endpoint = endpoints_for_subnet(uuid.UUID(endpoints[0]["subnet"]))[0]
    old_server = str(uuid.uuid4())
    model.observed(state={str(endpoint.uuid): {"server_id": old_server}})
    path = resource(client, "compute", "cloud_network_links", link["uuid"])
    result = client.post(
        path.rstrip("/") + "/actions/recover_gateway/invoke",
        json={"subnet": endpoints[0]["subnet"]},
    ).json()
    assert result["status"] == "IN_PROGRESS"
    assert result["recovery"] == {str(endpoint.uuid): old_server}
    model.complete_recovery(endpoint.uuid, str(uuid.uuid4()))
    assert client.get(path).json()["recovery"] == result["recovery"]
    model.complete_recovery(endpoint.uuid, old_server)
    assert client.get(path).json()["recovery"] == {}


def test_local_gateway_requires_capability_and_cannot_share_a_routing_domain(client):
    from gcl_sdk.agents.universal.dm import models as ua

    cloud_endpoint = link_endpoints(client)[0]
    agent = ua.UniversalAgent(
        uuid=uuid.uuid4(),
        node=uuid.uuid4(),
        name="local-router",
        capabilities={"capabilities": []},
        facts={"facts": []},
    )
    agent.insert()
    net = create(client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"})
    subnet = create(
        client, ("network", "subnets"), network=net["uuid"], cidr="10.246.0.0/24"
    )
    compute.Node(uuid=agent.node, project_id=uuid.UUID(subnet["project_id"]), name="router", node_type="HW", cores=1, ram=512, disk_spec=compute.infra_models.DisksSpec(disks=[])).insert()
    local = {
        "subnet": subnet["uuid"],
        "agent": str(agent.uuid),
        "endpoint": "192.0.2.20",
    }
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "cloud_network_links"),
            endpoints=[local, cloud_endpoint],
        )
    agent.capabilities = {"capabilities": ["cloud_net_link"]}
    agent.update()
    create(
        client, ("compute", "cloud_network_links"), endpoints=[local, cloud_endpoint]
    )
    second_subnet = create(
        client, ("network", "subnets"), network=net["uuid"], cidr="10.246.1.0/24"
    )
    other = link_endpoints(client, cidrs=["10.247.0.0/24", "10.247.1.0/24"])[0]
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "cloud_network_links"),
            endpoints=[dict(local, subnet=second_subnet["uuid"]), other],
        )


def test_reconciliation_renews_certificates_without_changing_endpoint_identity(
    client, monkeypatch
):
    from gcl_sdk.cloud import openvpn

    from exordos_core.cloud.links import CloudLinkEndpoint
    from exordos_core.cloud.links import CloudLinkService

    link = create(
        client, ("compute", "cloud_network_links"), endpoints=link_endpoints(client)
    )
    model = CloudNetworkLink.objects.get_one(filters={"uuid": filters.EQ(link["uuid"])})
    endpoints = CloudLinkEndpoint.objects.get_all(
        filters={"link": filters.EQ(model.uuid)}
    )
    service = CloudLinkService()
    before = service._material(model, endpoints)
    ca_key = before["ca"]["key"]
    leaves = {key: e["client"]["key"] for key, e in before["endpoints"].items()}
    monkeypatch.setattr(openvpn, "expiring", lambda pem: True)
    current = service._material(model, endpoints)
    assert current["ca"]["key"] == ca_key
    assert set(current["endpoints"]) == set(leaves)
    assert all(
        e["client"]["key"] != leaves[key] for key, e in current["endpoints"].items()
    )


def test_gateway_recovery_requires_update_permission(
    client, user_api_client, auth_test1_user
):
    endpoints = link_endpoints(client)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    reader = user_api_client(auth_test1_user, permissions=["compute.hypervisor.read"])
    path = resource(reader, "compute", "cloud_network_links", link["uuid"])
    assert reader.get(path).status_code == 200
    with pytest.raises(exceptions.ForbiddenError):
        reader.post(
            path.rstrip("/") + "/actions/recover_gateway/invoke",
            json={"subnet": endpoints[0]["subnet"]},
        )
    assert client.get(path).json()["recovery"] == {}


def test_link_accepts_more_than_sixteen_endpoints(client):
    endpoints = link_endpoints(client, count=17)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    assert len(link["endpoints"]) == 17
    saved = client.get(
        resource(client, "compute", "cloud_network_links", link["uuid"])
    ).json()
    assert saved["endpoints"] == link["endpoints"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("orch_endpoint", "https://other.example/orch/"),
        ("status_endpoint", "https://other.example/status/"),
        ("agent_package", "gcl_sdk==99.0.0"),
    ],
)
def test_connection_cannot_override_installation_bootstrap(client, field, value):
    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    with pytest.raises(exceptions.BadRequestError):
        client.put(path, json={field: value})
    assert field not in client.get(path).json()


def test_gateway_address_reservation_cannot_be_released_through_tenant_api(client):
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.user_api.network.dm.models import Address

    endpoints = link_endpoints(client)
    link = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    endpoint = endpoints_for_subnet(uuid.UUID(endpoints[0]["subnet"]))[0]
    address = Address(
        uuid=endpoint.uuid,
        subnet=endpoint.subnet,
        project_id=uuid.UUID(link["project_id"]),
        kind="reservation",
        address="10.244.0.50",
    )
    address.insert()
    path = resource(client, "network", "catalog", "addresses", str(address.uuid))
    with pytest.raises(exceptions.ConflictError):
        client.delete(path)
    assert client.get(path).json()["address"] == address.address
    address.delete()  # Controller cleanup remains possible while the binding is retained.


def test_floating_ip_failure_keeps_gateway_private_ip_reserved(client, monkeypatch):
    from types import SimpleNamespace
    from unittest import mock

    from exordos_core.cloud.links import CloudLinkService
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.user_api.network.dm.models import Address

    endpoints = link_endpoints(client)
    view = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    link = CloudNetworkLink.objects.get_one(
        filters={"uuid": filters.EQ(uuid.UUID(view["uuid"]))}
    )
    endpoint, peer = [
        endpoints_for_subnet(uuid.UUID(e["subnet"]))[0] for e in endpoints
    ]
    service = CloudLinkService()
    subnet = service._subnet(endpoint)
    cloud = mock.Mock()
    cloud.ensure_port.return_value = SimpleNamespace(
        id="port", fixed_ips=[{"ip_address": "10.244.0.50"}], allowed_address_pairs=[]
    )
    cloud.ensure_floating_ip.side_effect = RuntimeError("floating IP quota")
    monkeypatch.setattr(
        service, "_provider", lambda e: (cloud, subnet, SimpleNamespace(id="subnet"))
    )
    with pytest.raises(RuntimeError, match="quota"):
        service._reserve(link, endpoint, [peer])
    reserved = Address.objects.get_one(filters={"uuid": filters.EQ(endpoint.uuid)})
    assert reserved.address == "10.244.0.50"


@pytest.mark.parametrize("deleting", [False, True])
@pytest.mark.parametrize("same_host", [False, True])
@pytest.mark.parametrize("reverse_endpoints", [False, True])
def test_unmanaged_gateway_coexists_with_managed_binding_without_reusing_its_host(
    client, deleting, same_host, reverse_endpoints
):
    from gcl_sdk.agents.universal.dm import models as ua

    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.compute.dm import models as compute

    private = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    subnet = create(
        client, ("network", "subnets"), network=private["uuid"], cidr="10.248.0.0/24"
    )
    external = compute.Network(
        project_id=uuid.UUID(int=0),
        driver_spec={"driver": "flat_bridge", "dhcp_cfg": "/tmp/cloud-link-dhcp.conf"},
    )
    external.insert()
    uplink = create(
        client, ("network", "subnets"), network=str(external.uuid), cidr="192.0.2.0/24"
    )
    managed = create(
        client,
        ("compute", "cloud_network_links"),
        endpoints=[
            link_endpoints(client)[0],
            {
                "subnet": subnet["uuid"],
                "external_subnet": uplink["uuid"],
                "gateway_image": "https://repo.example/gateway.raw.zst",
            },
        ],
    )
    binding = endpoints_for_subnet(uuid.UUID(subnet["uuid"]))[0]
    if deleting:
        client.delete(
            resource(client, "compute", "cloud_network_links", managed["uuid"])
        )
    agent = ua.UniversalAgent(
        uuid=uuid.uuid4(),
        node=binding.uuid if same_host else uuid.uuid4(),
        name="local-router",
        capabilities={"capabilities": ["cloud_net_link"]},
        facts={"facts": []},
    )
    compute.Node(uuid=agent.node, project_id=uuid.UUID(subnet["project_id"]), name="router", node_type="HW", cores=1, ram=512, disk_spec=compute.infra_models.DisksSpec(disks=[])).insert()
    agent.insert()
    other_subnet = create(
        client, ("network", "subnets"), network=private["uuid"], cidr="10.249.0.0/24"
    )
    endpoints = [
        {
            "subnet": other_subnet["uuid"],
            "agent": str(agent.uuid),
            "endpoint": "192.0.2.20",
        },
        link_endpoints(client, cidrs=["10.250.0.0/24", "10.251.0.0/24"])[0],
    ]
    if reverse_endpoints:
        endpoints.reverse()
    if same_host:
        with pytest.raises(exceptions.BadRequestError):
            create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
    else:
        result = create(client, ("compute", "cloud_network_links"), endpoints=endpoints)
        assert [e["subnet"] for e in result["endpoints"]] == [
            e["subnet"] for e in endpoints
        ]


def test_managed_sdn_endpoint_creates_gateway_and_controls_routes(client):
    import netaddr

    from exordos_core.cloud import local
    from exordos_core.cloud.links import CloudLinkService
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.compute.dm import models as compute

    private = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    subnet = create(
        client, ("network", "subnets"), network=private["uuid"], cidr="10.248.0.0/24"
    )
    external = compute.Network(
        uuid=uuid.uuid4(),
        project_id=uuid.UUID(int=0),
        driver_spec={"driver": "flat_bridge", "dhcp_cfg": "/tmp/cloud-link-dhcp.conf"},
    )
    external.insert()  # Operator-owned flat networks are not tenant-created.
    uplink = create(
        client, ("network", "subnets"), network=str(external.uuid), cidr="192.0.2.0/24"
    )
    cloud = link_endpoints(client)[0]
    view = create(
        client,
        ("compute", "cloud_network_links"),
        endpoints=[
            cloud,
            {
                "subnet": subnet["uuid"],
                "external_subnet": uplink["uuid"],
                "gateway_image": "https://repo.example/gateway.raw.zst",
            },
        ],
    )
    endpoint = endpoints_for_subnet(uuid.UUID(subnet["uuid"]))[0]
    link = CloudNetworkLink.objects.get_one(
        filters={"uuid": filters.EQ(uuid.UUID(view["uuid"]))}
    )
    initial = local.reserve(link, endpoint)
    assert initial["endpoint"] and initial["gateway_ip"]
    ports = compute.Port.objects.get_all(filters={"node": filters.EQ(endpoint.uuid)})
    assert len(ports) == 2
    assert next(p for p in ports if p.index == 0).port_security is False
    for port in ports:
        port.ipv4 = netaddr.IPAddress(
            "10.248.0.20" if port.index == 0 else "192.0.2.20"
        )
        # This is the observed state normally written by network reconciliation.
        from restalchemy.storage.sql import orm

        orm.SQLStorableMixin.update(port)
    state = local.reserve(link, endpoint)
    assert state["endpoint"] == "192.0.2.20"
    assert state["gateway_ip"] == "10.248.0.20"
    assert not local.booted(endpoint)
    CloudLinkService()._routes(endpoint, state, ["10.244.0.0/24"])
    actual = compute.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(endpoint.subnet)}
    )
    assert {
        "to": "10.244.0.0/24",
        "via": "10.248.0.20",
    } in actual.dump_to_simple_view()["routers"]
    CloudLinkService()._routes(endpoint, state, [])
    actual = compute.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(endpoint.subnet)}
    )
    assert len(actual.routers) == 1  # implicit default route is preserved
    nodepath = resource(client, "compute", "nodes", str(endpoint.uuid))
    with pytest.raises(exceptions.ConflictError):
        client.delete(nodepath)
    with pytest.raises(exceptions.ConflictError):
        client.put(nodepath, json={"ram": 2048})

    link.observed(state={str(endpoint.uuid): state})
    external_port = next(port for port in ports if port.index == 1)
    portpath = resource(client, "network", "ports", str(external_port.uuid))
    with pytest.raises(exceptions.ConflictError):
        client.delete(portpath)
    with pytest.raises(exceptions.ConflictError):
        client.put(portpath, json={"dhcp": False})
    with pytest.raises(exceptions.ConflictError):
        create(
            client,
            ("network", "ports"),
            node=str(endpoint.uuid),
            subnet=str(endpoint.subnet),
            index=2,
        )
    external_port.delete()  # A lost owned resource is reconstructed by reconciliation.
    assert local.reserve(link, endpoint) == state
    assert CloudLinkService()._remove(link, endpoint)
    assert (
        compute.Node.objects.get_one_or_none(
            filters={"uuid": filters.EQ(endpoint.uuid)}
        )
        is None
    )
    assert not compute.Port.objects.get_all(filters={"node": filters.EQ(endpoint.uuid)})
    assert not endpoints_for_subnet(endpoint.subnet)


@pytest.mark.parametrize("ephemeral", [None, True, False])
def test_cloud_nodeset_defaults_unspecified_replica_disks_to_cinder(client, ephemeral):
    from exordos_core.compute.node_set.dm.models import NodeSet

    endpoint = link_endpoints(client)[0]
    definition = {
        "size": 6,
        "image": "https://repo.example/pg.raw.zst",
        "label": "root",
    }
    if ephemeral is not None:
        definition["ephemeral"] = ephemeral
    group = NodeSet.restore_from_simple_view(
        uuid=str(uuid.uuid4()),
        project_id=str(uuid.UUID(int=0)),
        name="cloud-db",
        cores=1,
        ram=1024,
        replicas=1,
        disk_spec={"kind": "disks", "disks": [definition]},
        default_network={"subnet": endpoint["subnet"]},
    )
    original = group.disk_spec.dump_to_simple_view()
    nodes = group.gen_nodes(uuid.UUID(int=0))
    assert len(nodes) == 1
    volumes = nodes[0].disk_spec.volumes(nodes[0])
    assert len(volumes) == 1
    assert volumes[0].ephemeral is (False if ephemeral is None else ephemeral)
    assert group.disk_spec.dump_to_simple_view() == original


def test_client_cloud_gateway_needs_no_floating_ip_or_transport_ingress(
    client, monkeypatch
):
    from types import SimpleNamespace
    from unittest import mock

    from exordos_core.cloud.links import CloudLinkService
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.compute.dm import models as compute

    definitions = link_endpoints(client)
    definitions[0]["role"], definitions[1]["role"] = "client", "server"
    result = create(client, ("compute", "cloud_network_links"), endpoints=definitions)
    link = CloudNetworkLink.objects.get_one(
        filters={"uuid": filters.EQ(result["uuid"])}
    )
    endpoint, peer = [
        endpoints_for_subnet(uuid.UUID(e["subnet"]))[0] for e in definitions
    ]
    subnet = compute.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(endpoint.subnet)}
    )
    cloud = mock.Mock()
    cloud.ensure_port.return_value = SimpleNamespace(
        id="gateway-port",
        fixed_ips=[{"ip_address": "10.244.0.50"}],
        allowed_address_pairs=[{"ip_address": "10.244.1.0/24"}],
    )
    service = CloudLinkService()
    monkeypatch.setattr(
        service, "_provider", lambda e: (cloud, subnet, SimpleNamespace(id="subnet"))
    )
    state = service._reserve(link, endpoint, [peer])
    assert state["endpoint"] is None
    assert state["gateway_ip"] == "10.244.0.50"
    cloud.ensure_floating_ip.assert_not_called()
    rules = cloud.security_group.call_args.args[1]
    assert not any(rule.get("port_range_min") == 443 for rule in rules)


def test_connection_credentials_are_write_only_and_can_rotate_in_same_project(client):
    from exordos_core.compute.dm.models import MachinePool as CloudConnection

    config = connection(client)
    path = resource(client, "compute", "hypervisors", config["uuid"])
    for value in (
        config,
        client.get(path).json(),
        client.get(collection(client, "compute", "hypervisors")).json(),
    ):
        assert "private-sentinel" not in str(value)
    new = {
        "application_credential_id": "replacement",
        "application_credential_secret": "replacement-sentinel",
    }
    result = client.put(
        path, json={"driver_spec": dict(config["driver_spec"], auth=new)}
    ).json()
    assert "auth" not in result["driver_spec"]
    saved = CloudConnection.objects.get_one(
        filters={"uuid": filters.EQ(config["uuid"])}
    )
    assert saved.driver_spec.auth == new


def test_connection_rotation_cannot_switch_provider_projects(client, monkeypatch):
    from exordos_core.compute.dm.models import MachinePool as CloudConnection

    config = connection(client)
    monkeypatch.setattr(
        CloudConnection, "_authenticate", lambda self: {"project_id": "foreign-project"}
    )
    path = resource(client, "compute", "hypervisors", config["uuid"])
    with pytest.raises(exceptions.ConflictError):
        client.put(
            path,
            json={
                "driver_spec": dict(
                    config["driver_spec"],
                    auth={
                        "application_credential_id": "other",
                        "application_credential_secret": "other-secret",
                    },
                )
            },
        )
    assert (
        CloudConnection.objects.get_one(
            filters={"uuid": filters.EQ(config["uuid"])}
        ).driver_spec.auth["application_credential_id"]
        == "test"
    )


def test_connection_selects_a_typed_driver_and_keeps_provider_fields_nested(client):
    result = create(
        client,
        ("compute", "hypervisors"),
        driver_spec={
            "kind": "openstack",
            "auth_url": "https://identity.example/v3",
            "auth_type": "v3password",
            "auth": {
                "username": "operator",
                "password": "private-sentinel",
                "user_domain_name": "Default",
                "project_id": "provider-project",
            },
        },
    )
    assert result["driver_spec"]["kind"] == "openstack"
    assert "auth" not in result["driver_spec"]
    assert (
        not {
            "auth_url",
            "auth_type",
            "auth",
            "driver",
            "region",
            "interface",
            "provider_project_id",
        }
        & result.keys()
    )
    assert result["driver_state"] == {"project_id": "provider-project"}


@pytest.mark.parametrize("kind", ["unknown", None, [], {}])
def test_connection_invalid_kind_never_echoes_credentials(client, kind):
    with pytest.raises(exceptions.BadRequestError) as error:
        create(
            client,
            ("compute", "hypervisors"),
            driver_spec={
                "kind": kind,
                "auth_url": "https://identity.example/v3",
                "auth": {
                    "application_credential_id": "test",
                    "application_credential_secret": "private-sentinel",
                },
            },
        )
    assert "private-sentinel" not in error.value.cause.response.text


def test_connection_metadata_update_does_not_reauthenticate(client, monkeypatch):
    from exordos_core.compute.dm.models import MachinePool as CloudConnection

    config = connection(client)
    monkeypatch.setattr(
        CloudConnection,
        "_authenticate",
        lambda self: pytest.fail("Metadata update must not authenticate"),
    )
    path = resource(client, "compute", "hypervisors", config["uuid"])
    assert client.put(path, json={"name": "renamed"}).json()["name"] == "renamed"


def test_connection_kind_dispatch_supports_a_non_openstack_spec(
    user_api_client, auth_user_admin, user_api, monkeypatch
):
    from restalchemy.dm import properties
    from restalchemy.dm import types
    from restalchemy.dm import types_dynamic

    from exordos_core.compute.dm.models import MachinePool as CloudConnection
    from exordos_core.cloud.specs import AbstractConnectionSpec

    class TestConnectionSpec(AbstractConnectionSpec):
        KIND = "test"
        SENSITIVE_FIELDS = frozenset({"api_token"})
        account = properties.property(types.String(), required=True)
        api_token = properties.property(types.String(), required=True)

        def validate_config(self, connection):
            pass

        def authenticate(self, connection):
            return {"account": self.account}

        def load_driver(self, connection):
            return "test-driver"

    selector = CloudConnection.properties.properties["driver_spec"].get_property_type()
    monkeypatch.setitem(
        selector._kind_type_map, "test", types_dynamic.KindModelType(TestConnectionSpec)
    )
    client = user_api_client(auth_user_admin)
    try:
        result = create(
            client,
            ("compute", "hypervisors"),
            driver_spec={
                "kind": "test",
                "account": "provider-account",
                "api_token": "private-sentinel",
            },
        )
        assert result["driver_spec"] == {"kind": "test", "account": "provider-account"}
        assert result["driver_state"] == {"account": "provider-account"}
        stored = CloudConnection.objects.get_one(
            filters={"uuid": filters.EQ(result["uuid"])}
        )
        assert stored.load_driver() == "test-driver"
        assert stored.driver_spec.api_token == "private-sentinel"
        path = resource(client, "compute", "hypervisors", result["uuid"])
        changed = client.put(
            path,
            json={
                "driver_spec": {
                    "kind": "test",
                    "account": "provider-account",
                    "api_token": "replacement-sentinel",
                }
            },
        ).json()
        assert "replacement-sentinel" not in str(changed)
        with pytest.raises(exceptions.ConflictError):
            client.put(
                path,
                json={
                    "driver_spec": {
                        "kind": "test",
                        "account": "other-account",
                        "api_token": "other",
                    }
                },
            )
        with pytest.raises(exceptions.ForbiddenError):
            client.put(path, json={"driver_state": {"account": "other-account"}})
    finally:
        user_api._restore_seed_snapshot()


@pytest.mark.parametrize("missing", [False, True])
def test_local_link_cannot_target_a_foreign_or_missing_agent_node(client, missing):
    from gcl_sdk.agents.universal.dm import models as ua

    cloud = dict(link_endpoints(client)[0], role="server")
    net = create(client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"})
    subnet = create(client, ("network", "subnets"), network=net["uuid"], cidr="10.246.0.0/24")
    node = uuid.uuid4()
    if not missing:
        compute.Node(uuid=node, project_id=uuid.uuid4(), name="foreign-router", node_type="HW", cores=1, ram=512, disk_spec=compute.infra_models.DisksSpec(disks=[])).insert()
    agent = ua.UniversalAgent(uuid=uuid.uuid4(), node=node, name="router", capabilities={"capabilities": ["cloud_net_link"]}, facts={"facts": []})
    agent.insert()
    with pytest.raises(exceptions.NotFoundError):
        create(client, ("compute", "cloud_network_links"), endpoints=[cloud, {"subnet": subnet["uuid"], "agent": str(agent.uuid), "role": "client"}])


@pytest.mark.parametrize("bound", [False, True])
def test_openstack_rejects_disabling_anti_spoofing_while_sg_is_required(client, bound):
    config = connection(client)
    net = network(client, config)
    subnet = create(client, ("network", "subnets"), network=net["uuid"], cidr="10.245.0.0/24")
    port = create(client, ("network", "ports"), subnet=subnet["uuid"], enforce_security_group=not bound)
    if bound:
        group = create(client, ("network", "catalog", "security_groups"))
        create(client, ("network", "port_security_groups"), port=port["uuid"], security_group=group["uuid"])
    path = resource(client, "network", "ports", port["uuid"])
    with pytest.raises(exceptions.BadRequestError):
        client.put(path, json={"port_security": False})
    assert client.get(path).json()["port_security"] is True


def test_openstack_cannot_bind_sg_after_disabling_anti_spoofing(client):
    config = connection(client)
    net = network(client, config)
    subnet = create(client, ("network", "subnets"), network=net["uuid"], cidr="10.245.0.0/24")
    port = create(client, ("network", "ports"), subnet=subnet["uuid"], port_security=False)
    group = create(client, ("network", "catalog", "security_groups"))
    with pytest.raises(exceptions.BadRequestError):
        create(client, ("network", "port_security_groups"), port=port["uuid"], security_group=group["uuid"])
