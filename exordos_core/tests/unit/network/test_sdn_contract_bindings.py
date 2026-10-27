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


@pytest.mark.parametrize(
    "model, field",
    [
        (models.NetworkFunction, "provenance"),
        (models.Address, "origin"),
        (compute.Node, "network_initialized"),
        (compute.Port, "legacy_flat_placement"),
        (compute.Port, "network_identity"),
        (compute.Port, "config"),
        (compute.Node, "interfaces"),
        (compute.NodeSet, "interfaces"),
        (models.NetworkFunction, "generated_config"),
        (models.NetworkFunction, "effective_config"),
    ],
)
def test_network_models_do_not_store_obsolete_origin_markers(model, field):
    assert field not in model.properties


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"forwarders": []}, {"forwarders": [], "zone_suffix": "internal"}),
        ({"zone_suffix": None}, {"forwarders": ["1.1.1.1"], "zone_suffix": "internal"}),
    ],
)
def test_nf_render_config_applies_overrides_without_changing_defaults(
    overrides, expected
):
    generated = {"forwarders": ["1.1.1.1"], "zone_suffix": "internal"}
    nf = models.NetworkFunction(
        project_id=uuid.uuid4(),
        kind="dns",
        config=overrides,
    )
    assert nf.render_config(generated) == expected
    assert generated == {"forwarders": ["1.1.1.1"], "zone_suffix": "internal"}
    assert nf.config == overrides


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
        models.SecurityGroup.project_observations([group])
        assert group.rules == [{"direction": "ingress", "protocol": "tcp", "port": 22}]
        assert group.dump_to_simple_view()["rules"] == group.rules
    assert "rules" not in group._get_prepared_data()


def test_port_membership_is_derived_not_stored_on_port():
    port = compute.Port(project_id=uuid.uuid4(), subnet=uuid.uuid4())
    sg = uuid.uuid4()
    with (
        mock.patch.object(models.PortSecurityGroup, "objects") as security,
    ):
        security.get_all.return_value = [
            SimpleNamespace(port=port.uuid, security_group=sg)
        ]
        from exordos_core.network.views import port_security_groups

        assert port_security_groups([port])[port.uuid] == [sg]
    data = port._get_prepared_data()
    assert not {"security_groups", "identity_groups", "network"} & data.keys()


def test_skipped_custom_properties_do_not_query_relations():
    port = compute.Port(project_id=uuid.uuid4(), subnet=uuid.uuid4())
    with (
        mock.patch.object(compute.Subnet, "objects") as subnets,
        mock.patch.object(models.PortSecurityGroup, "objects") as security,
    ):
        view = port.dump_to_simple_view(skip=["network", "security_groups"])
        subnets.get_one_or_none.assert_not_called()
        security.get_all.assert_not_called()
    assert not {"network", "security_groups"} & view.keys()


def test_node_network_template_uses_the_existing_field():
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
        {"remote_ip": "10.0.0.0/24", "remote_group_id": uuid.uuid4()},
        {"protocol": "icmp", "port": 22},
        {"direction": "egress", "remote_group_id": uuid.uuid4()},
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


@pytest.mark.parametrize("enforce", [False, True])
def test_security_groups_open_only_declared_traffic_and_preserve_enforcement(enforce):
    driver = object.__new__(evpn.OvsEvpnNetworkDriver)
    port = compute.Port(
        project_id=uuid.uuid4(),
        subnet=uuid.uuid4(),
        enforce_security_group=enforce,
    )
    unbound = list(driver.DENY_ALL) if enforce else []
    with (
        mock.patch.object(models.PortSecurityGroup, "objects") as bindings,
        mock.patch.object(models.SecurityGroup, "objects") as groups,
        mock.patch.object(models.SecurityGroupRule, "objects") as rules,
    ):
        bindings.get_all.return_value = []
        assert driver._compile_security_rules(port) == unbound
        groups.get_one_or_none.assert_not_called()
        bindings.get_all.return_value = [
            SimpleNamespace(port=port.uuid, security_group=uuid.uuid4())
        ]
        groups.get_one_or_none.return_value = SimpleNamespace(rules=[])
        assert driver._compile_security_rules(port) == list(driver.DENY_ALL)
        groups.get_one_or_none.return_value = None
        assert driver._compile_security_rules(port) == list(driver.DENY_ALL)
        groups.get_one_or_none.return_value = SimpleNamespace(
            rules=[
                {"direction": "ingress", "protocol": "tcp", "port": 22},
                {"direction": "egress", "protocol": "icmp"},
            ]
        )
        rules.get_all.return_value = [
            models.SecurityGroupRule(
                project_id=port.project_id,
                security_group=bindings.get_all.return_value[0].security_group,
                **rule,
            )
            for rule in groups.get_one_or_none.return_value.rules
        ]
        assert driver._compile_security_rules(port) == [
            {"direction": "ingress", "proto": "tcp", "port": 22},
            {"direction": "egress", "proto": "icmp"},
        ]
        bindings.get_all.return_value = []
        assert driver._compile_security_rules(port) == unbound


def test_security_group_enforcement_defaults_off():
    port = compute.Port(project_id=uuid.uuid4(), subnet=uuid.uuid4())
    assert port.enforce_security_group is False
    assert port.port_security is True
