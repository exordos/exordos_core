#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import base64
from contextlib import nullcontext
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from gcl_sdk.agents.universal.api import crypto
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.infra.dm import models as infra
import pytest
from restalchemy.common import contexts
from restalchemy.dm import filters
from restalchemy.storage.sql import engines
import yaml

from exordos_core.agent.universal.drivers.secret.dm import models as issuer
from exordos_core.common import constants
from exordos_core.common import exceptions
from exordos_core.common import spiffe
from exordos_core.common.dm import targets
from exordos_core.common.utils import PROJECT_PATH
from exordos_core.compute.dm import models as compute
from exordos_core.config import endpoints as transport
from exordos_core.config import service as config_service
from exordos_core.config.dm import models as configs
from exordos_core.elements import endpoints
from exordos_core.elements.builders.service import CommonBuilder
from exordos_core.elements.dm import models
from exordos_core.elements.dm import res_models


@pytest.fixture
def endpoint_manifest(user_api):
    examples = Path(PROJECT_PATH) / "exordos/manifests/examples"
    core = models.Manifest(
        **yaml.safe_load((examples / "core.element.yaml").read_text())
    )
    core.save()
    core.install()
    return yaml.safe_load(
        (examples / "service-endpoints.yaml.j2")
        .read_text()
        .replace("{{ project_id }}", str(constants.ZERO_UUID))
        .replace("{{ image }}", "https://example.com/base.raw.zst")
    )


def use_names(config):
    engine = engines.engine_factory.get_engine()
    with engine.session_manager() as session:
        rows = session.execute(
            "SELECT endpoint FROM em_endpoint_uses WHERE config = %s ORDER BY endpoint",
            (str(config.uuid),),
        ).fetchall()
    return [row["endpoint"] for row in rows]


def test_client_config_owns_endpoint_access(endpoint_manifest):
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    _, element = manifest.install()
    engine = models.element_engine
    config = engine.get_resource_by_link(element, "$core.config.configs.$api_config")

    config.actualize()
    initial = config.target_resource.value["body"]["content"]
    assert "catalog_url: http://127.0.0.1:20000" in initial
    assert "catalog_events_url: http://127.0.0.1:20001" in initial
    assert use_names(config) == ["default", "events"]

    literal = initial
    config.value = {**config.value, "body": {"kind": "text", "content": literal}}
    config.update()
    config.actualize()
    assert use_names(config) == []
    assert config.target_resource.value["body"]["content"] == initial

    config.value = endpoint_manifest["resources"]["$core.config.configs"]["api_config"]
    config.update()
    config.actualize()
    assert config.target_resource.value["body"]["content"] == initial
    assert use_names(config) == ["default", "events"]

    config.delete()
    assert use_names(config) == []


@pytest.mark.parametrize("retarget_import", [False, True])
def test_upgrade_preserves_imported_endpoint(endpoint_manifest, retarget_import):
    provider = endpoint_manifest
    config = provider["resources"].pop("$core.config.configs")["api_config"]
    nodes = provider["resources"]["$core.compute.sets"].pop("api_nodes")
    link = "$core.em.services.$catalog"
    provider["exports"] = {"catalog": {"link": link}}
    if retarget_import:
        services = provider["resources"]["$core.em.services"]
        services["alternate"] = {
            **services["catalog"],
            "endpoints": {"default": services["catalog"]["endpoints"]["default"]},
        }
        provider["exports"]["alternate"] = {"link": "$core.em.services.$alternate"}
    manifest = models.Manifest(**provider)
    manifest.save()
    manifest.install()

    config["body"]["content"] = (
        "$endpoint-client.imports.$catalog:endpoints:events:address"
    )
    client = models.Manifest(
        name="endpoint-client",
        schema_version=1,
        version="0.0.1",
        api_version="v1",
        imports={"catalog": {"element": f"${provider['name']}", "link": link}},
        resources={
            "$core.compute.sets": {"api_nodes": nodes},
            "$core.config.configs": {"api_config": config},
        },
    )
    client.save()
    _, client_element = client.install()
    engine = models.element_engine
    client_config = engine.get_resource_by_link(
        client_element, "$core.config.configs.$api_config"
    )
    client_config.actualize()
    assert use_names(client_config) == ["events"]

    if retarget_import:
        upgrade = models.Manifest(
            name=client.name,
            schema_version=1,
            version="0.0.2",
            api_version=client.api_version,
            imports={
                "catalog": {
                    "element": f"${provider['name']}",
                    "link": "$core.em.services.$alternate",
                }
            },
            resources=client.resources,
        )
    else:
        provider["version"] = "0.0.2"
        del provider["resources"]["$core.em.services"]["catalog"]["endpoints"]["events"]
        upgrade = models.Manifest(**provider)
    upgrade.save()
    with pytest.raises(exceptions.ValidateException, match="events"):
        upgrade.upgrade()
    engine.get_resource_by_link(client_element, client_config.link).actualize()
    assert use_names(client_config) == ["events"]


@pytest.mark.parametrize("invalid", ["key", "expired"])
def test_failed_issuance_does_not_reuse_invalid_identity(monkeypatch, invalid):
    now = datetime.now(timezone.utc)
    key = SimpleNamespace(updated_at=now)
    cached = {
        "spiffe_id": f"spiffe://{spiffe.LINK_TRUST_DOMAIN}/node/example",
        "key_updated_at": (
            now - timedelta(minutes=5) if invalid == "key" else now
        ).isoformat(),
        "renew_at": (now - timedelta(hours=1)).isoformat(),
        "expiration_at": (
            now + timedelta(minutes=5 if invalid == "key" else -5)
        ).isoformat(),
    }

    def issuer_unavailable(*args):
        raise ValueError("issuer unavailable")

    monkeypatch.setattr(transport.sql_utils, "savepoint", lambda *args: nullcontext())
    monkeypatch.setattr(issuer.SPIFFEAuthority, "get_or_create", issuer_unavailable)
    assert (
        transport.LinkService._encrypted_identity(cached["spiffe_id"], key, cached)
        is None
    )


def test_stale_actualization_preserves_new_config(endpoint_manifest):
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    _, element = manifest.install()
    config = models.element_engine.get_resource_by_link(
        element, "$core.config.configs.$api_config"
    )
    stale = models.Resource.objects.get_one({"uuid": config.uuid})
    content = config.value["body"]["content"]
    updated = "\n".join(
        line for line in content.splitlines() if not line.startswith("catalog_url:")
    )
    config.value = {**config.value, "body": {"kind": "text", "content": updated}}
    config.update()

    stale.actualize()
    stored = models.Resource.objects.get_one({"uuid": config.uuid})
    assert stored.value == config.value
    assert stored.target_resource is None
    assert use_names(stored) == []
    assert not ua.TargetResource.objects.get_all(filters={"uuid": config.uuid})

    stored.actualize()
    current = models.Resource.objects.get_one({"uuid": config.uuid})
    assert current.target_resource is not None
    assert use_names(current) == ["events"]

    stale = models.Resource.objects.get_one({"uuid": config.uuid})
    current.value = {
        **current.value,
        "body": {"kind": "text", "content": "catalog disabled"},
    }
    current.update()
    current.actualize()
    stale.actualize()
    assert use_names(config) == []
    persisted = models.Resource.objects.get_one({"uuid": config.uuid})
    assert persisted.value == current.value
    assert persisted.target_resource.value["body"]["content"] == "catalog disabled"


def test_install_rejects_missing_default(endpoint_manifest):
    service = endpoint_manifest["resources"]["$core.em.services"]["catalog"]
    default = service["endpoints"].pop("default")
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    with pytest.raises(exceptions.ValidateException) as error:
        manifest.install()
    assert "has no endpoint 'default'" in error.value.err
    assert models.Element.objects.get_one_or_none({"uuid": manifest.uuid}) is None
    service["endpoints"]["default"] = default
    manifest.resources = endpoint_manifest["resources"]
    _, installed = manifest.install()
    assert installed.uuid == manifest.uuid


def test_failed_port_allocation_rolls_back(endpoint_manifest, monkeypatch):
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    _, element = manifest.install()
    engine = models.element_engine
    config = engine.get_resource_by_link(element, "$core.config.configs.$api_config")
    service = engine.get_resource_by_link(element, "$core.em.services.$catalog")
    provider = engine.get_resource_by_link(element, "$core.compute.sets.$catalog_nodes")
    allocate = endpoints.endpoint_port

    def allocate_with_full_provider(node_set, service, endpoint, **kwargs):
        if node_set == provider.uuid:
            raise exceptions.ValidateException(err="Provider range exhausted")
        return allocate(node_set, service, endpoint, **kwargs)

    monkeypatch.setattr(endpoints, "endpoint_port", allocate_with_full_provider)
    config.actualize()
    with engines.engine_factory.get_engine().session_manager() as session:
        assert not session.execute(
            "SELECT port FROM em_endpoint_addresses WHERE service = %s",
            (str(service.uuid),),
        ).fetchall()
    assert use_names(config) == []


def test_shared_transport_prefers_a_common_overlay():
    flat = uuid.UUID(int=1)
    overlay = uuid.UUID(int=2)
    other_overlay = uuid.UUID(int=3)
    subnet = uuid.UUID(int=4)
    attachment = transport.Attachment
    client = [
        attachment(flat, subnet, "10.0.0.1", False),
        attachment(overlay, subnet, "10.1.0.1", True),
    ]
    server = [
        attachment(flat, subnet, "10.0.0.2", False),
        attachment(other_overlay, subnet, "10.2.0.2", True),
    ]
    assert transport.LinkService._shared_transport(client, server) == (
        "10.0.0.1",
        "10.0.0.2",
    )
    server.append(attachment(overlay, subnet, "10.1.0.2", True))
    assert transport.LinkService._shared_transport(client, server) == (
        "10.1.0.1",
        "10.1.0.2",
    )


def test_config_cannot_write_the_managed_proxy_file(user_api):
    with pytest.raises(exceptions.ValidateException, match="Path reserved"):
        configs.Config(
            name="managed-file",
            project_id=constants.ZERO_UUID,
            target=targets.NodeTarget(node=uuid.uuid4()),
            path="/etc/exordos-link/./listeners.json",
            body=configs.TextBodyConfig(content="unmanaged"),
        ).insert()


def test_provider_endpoints_round_trip(endpoint_manifest):
    state = endpoint_manifest["resources"]["$core.em.services"]["catalog"]
    state["target"]["node_set"] = str(constants.ZERO_UUID)
    service = models.Service.restore_from_simple_view(**state)
    service.insert()
    result = models.Service.objects.get_one({"uuid": service.uuid})
    assert result.endpoints == {
        "default": {"port": 8080, "protocol": "http"},
        "events": {"port": 8081, "protocol": "tcp"},
    }
    result.endpoints = {"default": {"port": endpoints.PORT_START}}
    with pytest.raises(exceptions.ValidateException):
        result.update()


def test_service_port_has_one_protocol():
    with pytest.raises(exceptions.ValidateException, match="cannot mix HTTP and TCP"):
        endpoints.validate_endpoints(
            {
                "default": {"port": 8080},
                "raw": {"port": 8080, "protocol": "tcp"},
            },
            "node_set",
        )


def test_services_cannot_publish_mixed_protocols_on_one_nodeset_port(endpoint_manifest):
    state = endpoint_manifest["resources"]["$core.em.services"]["catalog"]
    state["target"]["node_set"] = str(constants.ZERO_UUID)
    service = models.Service.restore_from_simple_view(**state)
    service.insert()
    state["name"] = "other-catalog"
    state["path"] = "/usr/bin/false"
    state["endpoints"] = {"other": {"port": 8080, "protocol": "tcp"}}
    other = models.Service.restore_from_simple_view(**state)
    with pytest.raises(exceptions.ValidateException, match="cannot mix HTTP and TCP"):
        other.insert()
    other.endpoints = {"other": {"port": 8080}}
    other.insert()
    other = models.Service.objects.get_one({"uuid": other.uuid})
    other.endpoints = {"other": {"port": 8080, "protocol": "tcp"}}
    with pytest.raises(exceptions.ValidateException, match="cannot mix HTTP and TCP"):
        other.update()


@pytest.mark.parametrize("provider_kind", ["node_set", "node"])
@pytest.mark.parametrize("consumer_kind", ["node_set", "node"])
def test_endpoint_configs_follow_the_shared_network(
    endpoint_manifest, provider_kind, consumer_kind, request, monkeypatch
):
    def remove_test_authority():
        for authority in issuer.SPIFFEAuthority.objects.get_all(
            filters={"trust_domain": filters.EQ(spiffe.LINK_TRUST_DOMAIN)}
        ):
            authority.delete()

    request.addfinalizer(remove_test_authority)
    resources = endpoint_manifest["resources"]
    nodes = resources.setdefault("$core.compute.nodes", {})
    if provider_kind == "node":
        nodes["catalog_node"] = {
            key: value
            for key, value in resources["$core.compute.sets"]["catalog_nodes"].items()
            if key != "replicas"
        }
        resources["$core.em.services"]["catalog"]["target"] = {
            "kind": "node",
            "node": "$core.compute.nodes.$catalog_node:uuid",
        }
    if consumer_kind == "node":
        nodes["api_node"] = {
            key: value
            for key, value in resources["$core.compute.sets"]["api_nodes"].items()
            if key != "replicas"
        }
        resources["$core.config.configs"]["api_config"]["target"] = {
            "kind": "node",
            "node": "$core.compute.nodes.$api_node:uuid",
        }
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    _, element = manifest.install()
    engine = models.element_engine

    def resource(reference):
        return engine.get_resource_by_link(element, reference)

    client_id = (
        resource("$core.compute.nodes.$api_node").uuid
        if consumer_kind == "node"
        else uuid.uuid4()
    )
    backend_id = (
        resource("$core.compute.nodes.$catalog_node").uuid
        if provider_kind == "node"
        else uuid.uuid4()
    )

    provider = compute.NodeSet.restore_from_simple_view(
        **resource("$core.compute.sets.$catalog_nodes").render_target_state()
    )
    consumer = compute.NodeSet.restore_from_simple_view(
        **resource("$core.compute.sets.$api_nodes").render_target_state()
    )
    for node_set in (provider, consumer):
        node_set.insert()
    service = models.Service.restore_from_simple_view(
        **resource("$core.em.services.$catalog").render_target_state()
    )
    service.insert()
    config = resource("$core.config.configs.$api_config")
    config.actualize()

    def subnet(network_name):
        network = compute.Network(
            name=network_name,
            project_id=constants.ZERO_UUID,
            driver_spec={"driver": "flat_bridge"},
        )
        network.insert()
        value = compute.Subnet.restore_from_simple_view(
            name=network_name,
            project_id=str(constants.ZERO_UUID),
            network=str(network.uuid),
            cidr="10.55.100.0/24",
        )
        value.insert()
        return value

    shared = subnet("shared")
    isolated = subnet("isolated")

    def node(
        name, group, attachment, address, *, supports_endpoints=True, node_id=None
    ):
        result = compute.Node(
            uuid=node_id or uuid.uuid4(),
            name=name,
            project_id=constants.ZERO_UUID,
            cores=1,
            ram=1024,
            disk_spec=infra.RootDiskSpec(
                size=1, image="https://example.com/base.raw.zst"
            ),
            node_set=group.uuid if group else None,
        )
        result.insert()
        ua.UniversalAgent.from_system_uuid(
            capabilities=["render", "link"] if supports_endpoints else ["render"],
            facts=[],
            agent_name=name,
            system_uuid=result.uuid,
        ).insert()
        port = attachment.port(
            node_uuid=result.uuid, ipv4=compute.netaddr.IPAddress(address)
        )
        port.insert()
        port.set_active()
        if result.uuid == backend_id or group == provider:
            unit_id = CommonBuilder.service_uuid_by_node_path(result.uuid, service.path)
            kind = res_models.ServiceNode.get_resource_kind()
            ua.Resource(
                uuid=unit_id,
                kind=kind,
                res_uuid=ua.TargetResource.gen_res_uuid(unit_id, kind),
                node=result.uuid,
                value={},
                status="ACTIVE",
                hash="running",
                full_hash="running",
            ).insert()
        return result

    client = node(
        "client",
        consumer if consumer_kind == "node_set" else None,
        shared,
        "10.55.100.2",
        node_id=client_id,
    )
    old_client = node(
        "old-client", consumer, shared, "10.55.100.6", supports_endpoints=False
    )
    backend = node(
        "backend",
        provider if provider_kind == "node_set" else None,
        shared,
        "10.55.100.3",
        node_id=backend_id,
    )
    outsider = node("outsider", provider, isolated, "10.55.100.4")
    old = node("old-image", provider, shared, "10.55.100.5", supports_endpoints=False)

    def listeners(guest):
        resources = ua.TargetResource.objects.get_all(
            filters={
                "kind": filters.EQ("link"),
                "master": filters.EQ(transport.LINK_OWNER),
                "agent": filters.EQ(guest.uuid),
            }
        )
        assert len(resources) <= 1
        return resources[0].value["listeners"] if resources else []

    link_service = transport.LinkService()
    link_service._loop_iteration()
    agent = ua.UniversalAgent.objects.get_one(filters={"uuid": client.uuid})
    [client_target] = agent.get_payload().caps_resources("link")
    assert client_target.value["listeners"] == listeners(client)
    assert len(listeners(client)) == 2
    assert [route["protocol"] for route in listeners(backend)] == ["http", "tcp"]
    assert listeners(client)[0]["peer_ids"] == [
        link_service._spiffe_id(
            constants.ZERO_UUID,
            provider.uuid if provider_kind == "node_set" else backend_id,
            provider_kind,
        )
    ]
    assert listeners(backend)[0]["peer_ids"] == [
        link_service._spiffe_id(
            constants.ZERO_UUID,
            consumer.uuid if consumer_kind == "node_set" else client_id,
            consumer_kind,
        )
    ]

    def decrypt_identity(envelope, guest):
        key = ua.NodeEncryptionKey.objects.get_one(filters={"uuid": guest.uuid})
        return crypto.decrypt_chacha20_poly1305(
            base64.b64decode(key.private_key),
            base64.b64decode(envelope["nonce"]),
            base64.b64decode(envelope["sealed"]),
            transport.IDENTITY_CONTEXT,
        )

    def check_identity(guest, peer):
        guest_agent = ua.UniversalAgent.objects.get_one(filters={"uuid": guest.uuid})
        [resource] = guest_agent.get_payload().caps_resources("link")
        envelope = resource.value["identity"]
        assert "PRIVATE KEY" not in str(resource.value)
        with pytest.raises(InvalidTag):
            decrypt_identity(envelope, peer)
        pem = decrypt_identity(envelope, guest)
        leaf, ca = x509.load_pem_x509_certificates(pem)
        leaf.verify_directly_issued_by(ca)
        assert (
            leaf.public_key()
            == serialization.load_pem_private_key(pem, password=None).public_key()
        )
        assert (
            leaf.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.UniformResourceIdentifier)
            == listeners(peer)[0]["peer_ids"]
        )
        return resource.hash, leaf.serial_number

    first_revision = check_identity(client, backend)
    check_identity(backend, client)
    link_service._loop_iteration()
    assert check_identity(client, backend) == first_revision

    stored = ua.TargetResource.objects.get_one(
        filters={"res_uuid": client_target.res_uuid}
    )
    stored.value = {
        **stored.value,
        "identity": {
            **stored.value["identity"],
            "renew_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    stored.update()

    def issue_unavailable(*args):
        raise ValueError("issuer unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(spiffe, "issue_svid", issue_unavailable)
        key = ua.NodeEncryptionKey.objects.get_one(filters={"uuid": client.uuid})
        with contexts.Context().session_manager():
            link_service._apply_link(
                client.uuid,
                stored.value["listeners"][1:],
                stored.value["identity"]["spiffe_id"],
                key,
                stored,
            )
    assert len(listeners(client)) == 1
    assert check_identity(client, backend)[1] == first_revision[1]

    link_service._loop_iteration()
    renewed_hash, renewed_serial = check_identity(client, backend)
    assert renewed_hash != first_revision[0]
    assert renewed_serial != first_revision[1]
    assert not listeners(outsider)
    assert not listeners(old)
    for route in listeners(client):
        assert len(route["targets"]) == 1
        assert route["targets"][0]["source"] == "10.55.100.2"
        assert route["targets"][0]["address"].startswith("10.55.100.3:")

    config_model = configs.Config.from_ua_resource(config.target_resource)
    config_model.insert()
    deliver = config_service.ConfigServiceBuilder()
    endpoint_configs = {config_model.uuid}

    def application_renders():
        return ua.TargetResource.objects.get_all(
            filters={
                "kind": filters.EQ("render"),
                "master": filters.EQ(config_model.uuid),
            }
        )

    with contexts.Context().session_manager():
        deliver._actualize_new_configs(
            [config_model], endpoint_configs=endpoint_configs
        )
    assert {render.agent for render in application_renders()} == {client.uuid}
    obsolete_render = config_model.render(node=old_client.uuid)
    obsolete_render.agent = old_client.uuid
    obsolete_render.insert()
    with contexts.Context().session_manager():
        deliver._actualize_config_members(endpoint_configs)
    assert {render.agent for render in application_renders()} == {client.uuid}

    # The machine deletion hook detaches targets before deleting the agent.
    retired = ua.TargetResource.objects.get_one(
        filters={
            "master": filters.EQ(transport.LINK_OWNER),
            "agent": filters.EQ(backend.uuid),
        }
    )
    retired.agent = None
    retired.save()
    ua.UniversalAgent.objects.get_one(filters={"uuid": backend.uuid}).delete()
    link_service._loop_iteration()
    assert [route["targets"] for route in listeners(client)] == [[], []]
    assert not ua.TargetResource.objects.get_all(
        filters={"uuid": retired.uuid, "kind": retired.kind}
    )

    def client_ports():
        with contexts.Context().session_manager() as session:
            return {
                row["endpoint"]: row["client_port"]
                for row in link_service._bindings(session)
            }

    initial_ports = client_ports()
    assert len(initial_ports) == 2
    replacement = resource("$core.compute.sets.$catalog_nodes").render_target_state()
    replacement.update(uuid=str(uuid.uuid4()), name="replacement")
    new_provider = compute.NodeSet.restore_from_simple_view(**replacement)
    new_provider.insert()
    published = service.endpoints
    service.endpoints = {"default": published["default"]}
    service.target = targets.NodeSetTarget(node_set=new_provider.uuid)
    service.update()
    assert client_ports().keys() == {"default"}
    service.endpoints = published
    service.update()
    assert client_ports() == initial_ports

    config.delete()
    link_service._loop_iteration()
    assert listeners(client) == listeners(backend) == []


def test_plain_config_follows_nodeset_members(endpoint_manifest):
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    _, element = manifest.install()
    resource = models.element_engine.get_resource_by_link(
        element, "$core.compute.sets.$api_nodes"
    )
    group = compute.NodeSet.restore_from_simple_view(**resource.render_target_state())
    group.insert()
    config = configs.Config(
        name="plain",
        project_id=constants.ZERO_UUID,
        path="/etc/plain",
        target=targets.NodeSetTarget(node_set=group.uuid),
        body=configs.TextBodyConfig(content="plain"),
    )
    config.insert()
    deliver = config_service.ConfigServiceBuilder()

    def renders():
        return {
            render.agent
            for render in ua.TargetResource.objects.get_all(
                filters={
                    "kind": filters.EQ("render"),
                    "master": filters.EQ(config.uuid),
                }
            )
        }

    deliver._iteration()
    assert not renders()
    assert configs.Config.objects.get_one({"uuid": config.uuid}).status == "NEW"

    nodes = []
    for index in range(2):
        node = compute.Node(
            name=f"plain-{index}",
            project_id=constants.ZERO_UUID,
            cores=1,
            ram=1024,
            disk_spec=infra.RootDiskSpec(
                size=1, image="https://example.com/base.raw.zst"
            ),
            node_set=group.uuid,
        )
        node.insert()
        deliver._iteration()
        assert renders() == {member.uuid for member in nodes}
        ua.UniversalAgent(uuid=node.uuid, node=node.uuid, name=node.name).insert()
        nodes.append(node)
        deliver._iteration()
        assert renders() == {node.uuid for node in nodes}

    nodes[0].delete()
    deliver._iteration()
    assert renders() == {nodes[1].uuid}


def test_config_recovers_active_status_when_unready_member_leaves(endpoint_manifest):
    manifest = models.Manifest(**endpoint_manifest)
    manifest.save()
    _, element = manifest.install()
    resource = models.element_engine.get_resource_by_link(
        element, "$core.compute.sets.$api_nodes"
    )
    group = compute.NodeSet.restore_from_simple_view(**resource.render_target_state())
    group.insert()
    config = configs.Config(
        name="status-on-shrink",
        project_id=constants.ZERO_UUID,
        path="/etc/plain",
        target=targets.NodeSetTarget(node_set=group.uuid),
        body=configs.TextBodyConfig(content="plain"),
    )
    config.insert()
    members = []
    for index in range(2):
        node = compute.Node(
            name=f"status-member-{index}",
            project_id=constants.ZERO_UUID,
            cores=1,
            ram=1024,
            disk_spec=infra.RootDiskSpec(
                size=1, image="https://example.com/base.raw.zst"
            ),
            node_set=group.uuid,
        )
        node.insert()
        ua.UniversalAgent(uuid=node.uuid, node=node.uuid, name=node.name).insert()
        members.append(node)

    deliver = config_service.ConfigServiceBuilder()
    deliver._iteration()
    ready = ua.TargetResource.objects.get_one(
        filters={"master": config.uuid, "agent": members[0].uuid}
    )
    ready.status = "ACTIVE"
    ready.update()
    members[1].node_set = None
    members[1].update()

    deliver._iteration()
    assert configs.Config.objects.get_one({"uuid": config.uuid}).status == "ACTIVE"
