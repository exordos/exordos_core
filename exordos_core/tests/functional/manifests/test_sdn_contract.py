# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""Independent manifests share canonical references, never replacement lists."""

import uuid

from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient
from gcl_sdk.agents.universal.clients.backend.db import ModelSpec
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.agents.universal.drivers.direct import DirectAgentDriver
from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage
from restalchemy.dm import filters

from exordos_core.compute.dm import models as compute
from exordos_core.elements.dm import models as em
from exordos_core.user_api.network.dm import models as catalog


def test_two_manifests_preserve_independent_links_and_auto_index(user_api, tmp_path):
    project = str(uuid.UUID(int=0))
    core = em.Manifest(name="core", version="1.0.0", resources={})
    core.save()
    core.install()
    net = compute.Network(
        project_id=uuid.UUID(int=0), driver_spec={"driver": "ovs_evpn"}
    )
    net.insert()
    import netaddr

    sub = compute.Subnet(
        project_id=net.project_id,
        network=net.uuid,
        cidr=netaddr.IPNetwork("10.101.0.0/24"),
    )
    sub.insert()
    external = compute.Network(
        project_id=net.project_id, access="public", driver_spec={"driver": "ovs_evpn"}
    )
    external.insert()
    pool = compute.Subnet(
        project_id=net.project_id,
        network=external.uuid,
        cidr=netaddr.IPNetwork("192.168.1.0/24"),
        ip_range=netaddr.IPRange("192.168.1.50", "192.168.1.99"),
        floating_pool=True,
        placeable=False,
        dhcp=False,
    )
    pool.insert()
    base = em.Manifest(
        name="sdn_base",
        version="1.0.0",
        requirements={"core": {"from_version": "0.0.0"}},
        resources={
            "$core.compute.nodes": {
                "vm": {
                    "project_id": project,
                    "name": "vm",
                    "cores": 1,
                    "ram": 1024,
                    "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
                    "networking": [],
                }
            },
            "$core.network.ports": {
                "private": {
                    "project_id": project,
                    "subnet": str(sub.uuid),
                    "node": "$core.compute.nodes.$vm:uuid",
                }
            },
        },
        exports={"port": {"link": "$core.network.ports.$private"}},
    )
    base.save()
    base.validate()
    base.install()
    storage = TargetFieldsFileStorage(str(tmp_path / "target-fields.json"))
    specs = [
        ModelSpec(model, kind, filters={})
        for model, kind in (
            (compute.Node, "em_core_compute_nodes"),
            (compute.Port, "em_core_network_ports"),
            (catalog.SecurityGroup, "em_core_network_catalog_security_groups"),
            (catalog.SecurityGroupRule, "em_core_network_catalog_security_group_rules"),
            (catalog.PortSecurityGroup, "em_core_network_port_security_groups"),
            (catalog.Address, "em_core_network_catalog_addresses"),
        )
    ]
    backend = DatabaseBackendClient(specs, storage)

    class Driver(DirectAgentDriver):
        def get_capabilities(self):
            return list(backend._model_spec_map)

    driver = Driver(backend, storage)

    def apply_element(name):
        resources = [
            item
            for item in em.element_engine.get_resources()
            if isinstance(item, em.Resource) and item.element.name == name
        ]
        rank = {
            "em_core_compute_nodes": 0,
            "em_core_network_catalog_security_groups": 0,
            "em_core_network_ports": 1,
            "em_core_network_catalog_security_group_rules": 2,
            "em_core_network_port_security_groups": 3,
            "em_core_network_catalog_addresses": 4,
        }
        resources.sort(key=lambda item: rank[item.kind])
        responses = []
        for item in resources:
            item.actualize()
            request = item.target_resource
            # The driver owns hash projection, including create-only fields.
            if (
                backend._model_spec_map[item.kind].model.objects.get_one_or_none(
                    filters={"uuid": filters.EQ(request.uuid)}
                )
                is None
            ):
                actual = driver.create(request)
            else:
                actual = driver.update(request)
            assert actual.hash == request.hash, (item.kind, actual.value, request.value)
            existing = ua.Resource.objects.get_one_or_none(
                filters={"res_uuid": filters.EQ(actual.res_uuid)}
            )
            if existing is None:
                actual.insert()
            else:
                existing.value, existing.hash, existing.full_hash = (
                    actual.value,
                    actual.hash,
                    actual.full_hash,
                )
                existing.update()
                actual = existing
            item.actual_resource = actual
            item.update()
            responses.append(actual)
        return resources, responses

    base_resources, _ = apply_element("sdn_base")
    port = compute.Port.objects.get_one(filters={"subnet": filters.EQ(sub.uuid)})
    assert port.index == 0
    addon = em.Manifest(
        name="sdn_policy",
        version="1.0.0",
        requirements={"core": {"from_version": "0.0.0"}},
        imports={
            "port": {
                "element": "$sdn_base",
                "kind": "resource",
                "link": "$core.network.ports.$private",
            }
        },
        resources={
            "$core.network.catalog.security_groups": {
                "ssh": {"project_id": project, "name": "ssh"}
            },
            "$core.network.catalog.security_group_rules": {
                "ssh": {
                    "project_id": project,
                    "security_group": "$core.network.catalog.security_groups.$ssh:uuid",
                    "direction": "ingress",
                    "protocol": "tcp",
                    "port": 22,
                }
            },
            "$core.network.port_security_groups": {
                "ssh": {
                    "project_id": project,
                    "port": "$sdn_policy.imports.$port:uuid",
                    "security_group": "$core.network.catalog.security_groups.$ssh:uuid",
                }
            },
            "$core.network.catalog.addresses": {
                "fip": {
                    "project_id": project,
                    "kind": "floating",
                    "network": str(external.uuid),
                    "port": "$sdn_policy.imports.$port:uuid",
                }
            },
        },
    )
    addon.save()
    addon.validate()
    addon.install()
    apply_element("sdn_policy")
    original = compute.Port.objects.get_one(filters={"uuid": filters.EQ(port.uuid)})
    addresses = catalog.Address.objects.get_all(filters={"port": filters.EQ(port.uuid)})
    assert len(original.security_groups) == len(addresses) == 1
    apply_element("sdn_base")
    after = compute.Port.objects.get_one(filters={"uuid": filters.EQ(port.uuid)})
    assert after.index == 0 and after.ipv4 == original.ipv4
    assert after.security_groups == original.security_groups
    assert [address.uuid for address in addresses] == [
        address.uuid
        for address in catalog.Address.objects.get_all(
            filters={"port": filters.EQ(after.uuid)}
        )
    ]


def test_shipped_sdn_example_validates(user_api):
    from pathlib import Path

    import yaml

    core = em.Manifest(name="core", version="1.0.0", resources={})
    core.save()
    core.install()
    path = (
        Path(__file__).resolve().parents[4]
        / "exordos/manifests/examples/sdn-networking.yaml"
    )
    manifest = em.Manifest(**yaml.safe_load(path.read_text()))
    manifest.save()
    manifest.validate()
    manifest.install()
    resources = [
        item
        for item in em.element_engine.get_resources()
        if isinstance(item, em.Resource) and item.element.name == manifest.name
    ]
    assert len(resources) == 11
    port = next(item for item in resources if item.kind == "em_core_network_ports")
    port.actualize()
    barrier = port.target_resource.value.get("activation_barrier")
    assert barrier, "initial SG bindings and rules must gate the example port"
