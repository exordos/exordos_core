#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from types import SimpleNamespace
from unittest import mock
import uuid

from gcl_sdk.infra.dm import models as infra_models
import pytest

from exordos_core.common import exceptions
from exordos_core.compute.dm import models as compute
from exordos_core.network.driver import evpn
from exordos_core.user_api.network.dm import models


def test_security_group_rules_are_derived_from_independent_resources():
    group = models.SecurityGroup(project_id=uuid.uuid4())
    rule = models.SecurityGroupRule(
        project_id=uuid.uuid4(),
        security_group=group.uuid,
        direction="ingress",
        protocol="tcp",
        port=22,
    )
    with mock.patch.object(models.SecurityGroupRule, "objects") as objects:
        objects.get_all.return_value = [rule]
        assert group.rules == [{"direction": "ingress", "protocol": "tcp", "port": 22}]
        assert group.dump_to_simple_view()["rules"] == group.rules
    assert "rules" not in group._get_prepared_data()


def test_port_membership_is_derived_not_stored_on_port():
    port = compute.Port(project_id=uuid.uuid4(), subnet=uuid.uuid4())
    sg, ig = uuid.uuid4(), uuid.uuid4()
    with (
        mock.patch.object(models.PortSecurityGroup, "objects") as security,
        mock.patch.object(models.PortIdentityGroup, "objects") as identity,
    ):
        security.get_all.return_value = [SimpleNamespace(security_group=sg)]
        identity.get_all.return_value = [SimpleNamespace(identity_group=ig)]
        assert port.security_groups == [sg]
        assert port.identity_groups == [ig]
    data = port._get_prepared_data()
    assert not {"security_groups", "identity_groups", "network"} & data.keys()


def test_skipped_custom_properties_do_not_query_relations():
    port = compute.Port(project_id=uuid.uuid4(), subnet=uuid.uuid4())
    with (
        mock.patch.object(compute.Subnet, "objects") as subnets,
        mock.patch.object(models.PortSecurityGroup, "objects") as security,
        mock.patch.object(models.PortIdentityGroup, "objects") as identity,
    ):
        view = port.dump_to_simple_view(
            skip=["network", "security_groups", "identity_groups"]
        )
        subnets.get_one_or_none.assert_not_called()
        security.get_all.assert_not_called()
        identity.get_all.assert_not_called()
    assert not {"network", "security_groups", "identity_groups"} & view.keys()


def test_node_creation_inputs_are_not_stored():
    node = compute.Node(
        project_id=uuid.uuid4(),
        cores=1,
        ram=1024,
        disk_spec=infra_models.DisksSpec(disks=[]),
        default_network={},
    )
    assert node.default_network == {}
    assert not {"ports", "networking", "network_activation_barrier"} & set(
        node._get_prepared_data()
    )
    restored = compute.Node.restore(
        project_id=node.project_id, cores=1, ram=1024, disk_spec=node.disk_spec
    )
    assert restored.default_network is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"remote_ip": "10.0.0.0/24", "remote_group": uuid.uuid4()},
        {"protocol": "icmp", "port": 22},
        {"direction": "egress", "remote_group": uuid.uuid4()},
    ],
)
def test_invalid_rule_combinations_are_rejected(kwargs):
    rule = models.SecurityGroupRule(
        project_id=uuid.uuid4(),
        security_group=uuid.uuid4(),
        direction="ingress",
        **{k: v for k, v in kwargs.items() if k != "direction"},
    )
    if "direction" in kwargs:
        rule.direction = kwargs["direction"]
    with (
        mock.patch.object(models, "validate_reference"),
        pytest.raises(exceptions.ValidateException),
    ):
        rule._validate_rule()


def test_no_security_group_allows_all_but_an_attached_empty_group_denies():
    driver = object.__new__(evpn.OvsEvpnNetworkDriver)
    port = SimpleNamespace(
        uuid=uuid.uuid4(),
        config=SimpleNamespace(security_groups=[]),
        security_groups=[],
    )
    assert driver._compile_security_rules(port) == []
    port.security_groups = [uuid.uuid4()]
    with mock.patch.object(models.SecurityGroup, "objects") as groups:
        groups.get_one_or_none.return_value = SimpleNamespace(rules=[])
        assert driver._compile_security_rules(port) == list(driver.DENY_ALL)
        groups.get_one_or_none.return_value = None
        assert driver._compile_security_rules(port) == list(driver.DENY_ALL)


def test_initial_barrier_includes_dependent_element_bindings():
    from exordos_core.network.manifest import activation_barrier

    port, group, link, rule = [uuid.uuid4() for _ in range(4)]
    first, second = (
        SimpleNamespace(uuid=uuid.uuid4()),
        SimpleNamespace(uuid=uuid.uuid4()),
    )
    binding = SimpleNamespace(
        element=second,
        resource_link_prefix="$core.network.port_security_groups",
        uuid=link,
        value={"port": "$policy.imports.$port:uuid", "security_group": str(group)},
        _render_value=lambda value, engine: (
            str(port) if value.startswith("$") else value
        ),
    )
    rule_model = SimpleNamespace(
        element=second,
        resource_link_prefix="$core.network.catalog.security_group_rules",
        uuid=rule,
        value={"security_group": str(group)},
        _render_value=lambda value, engine: value,
    )
    resource = SimpleNamespace(element=first)
    engine = SimpleNamespace(get_resources=lambda: [binding, rule_model])
    assert activation_barrier(resource, {"uuid": str(port)}, engine) == {
        "bindings": [str(link)],
        "rules": [str(rule)],
    }
