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

"""A link to a field the resource doesn't have yet names the whole link."""

import logging
from unittest import mock

import pytest

from exordos_core.elements.dm import models
from exordos_core.elements.dm.models import Element
from exordos_core.elements.dm.models import Resource
from exordos_core.elements.services import builders

MISSING = "`$core.vs.variables.$mail_domain:value` has no field `value`"


@pytest.fixture
def element():
    return Element(
        name="notification",
        version="1.0.0",
        status="ACTIVE",
        link="$notification",
    )


@pytest.fixture
def variable(element):
    # A selector variable with no selected value has no `value` field.
    return Resource(
        element=element,
        name="mail_domain",
        resource_link_prefix="$core.vs.variables",
        value={"name": "notification_mail_domain"},
    )


@pytest.fixture
def mail(element, variable):
    resource = Resource(
        element=element,
        name="notification_mail",
        resource_link_prefix="$mailaas.types.mail.instances",
        value={"domain": 'f"{$core.vs.variables.$mail_domain:value}"'},
    )
    engine = mock.Mock()
    engine.get_resource_by_link.return_value = variable
    return resource, engine


def test_get_parameter_value_names_missing_link(variable):
    with pytest.raises(KeyError) as exc:
        variable.get_parameter_value("$mail_domain:value")

    assert exc.value.args[0] == MISSING


def test_actualize_logs_missing_link(mail, caplog):
    resource, engine = mail

    with (
        mock.patch("exordos_core.elements.dm.models.element_engine", engine),
        caplog.at_level(logging.WARNING),
    ):
        resource.actualize()

    assert f"by reason: {MISSING}" in caplog.text


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("has_target", [False, True])
def test_missing_import_does_not_stop_iteration(element, inline, has_target, caplog):
    link = "$notification.imports.$email_provider:uuid"
    resource = Resource(
        element=element,
        name="mail_template",
        resource_link_prefix="$notification.templates",
        value={"provider": f'f"{{{link}}}"' if inline else link},
    )
    target = mock.Mock(spec=models.sdk_models.TargetResource) if has_target else None
    resource.target_resource = target
    engine = models.ElementEngine()
    engine.add_element(element)
    engine.add_resource(resource)
    healthy = mock.Mock()
    builder = builders.ElementManagerBuilder()
    builder._element_engine = engine

    with (
        mock.patch.object(engine, "load_from_database"),
        mock.patch.object(engine, "get_resources", return_value=[resource, healthy]),
        mock.patch("exordos_core.elements.dm.models.element_engine", engine),
        mock.patch.object(
            models.OutdatedResources,
            "objects",
            mock.Mock(get_all=mock.Mock(return_value=[])),
        ),
        mock.patch.object(builders.contexts, "Context"),
        mock.patch.object(builder, "_actualize_statuses") as statuses,
        mock.patch.object(resource, "_find_actual_resource") as find_actual,
        mock.patch.object(resource, "update") as update,
        caplog.at_level(logging.WARNING),
    ):
        builder._iteration()
        healthy.actualize.assert_called_once_with()
        statuses.assert_called_once()
        find_actual.assert_not_called()
        update.assert_not_called()
        assert resource.target_resource is target
        if target is not None:
            assert target.mock_calls == []
        assert str(resource.uuid) in caplog.text
        assert (
            "by reason: Resource with link string "
            "'$notification.imports.$email_provider' does not exist."
        ) in caplog.text
        assert "_namespace_resources" not in caplog.text

        # Restoring the import makes the same resource eligible on the next pass.
        provider = Resource(
            element=element,
            name="email_provider",
            resource_link_prefix="$notification.imports",
            value={},
        )
        engine.add_resource(provider)
        assert resource.render_target_state()["provider"] == str(provider.uuid)
        find_actual.side_effect = RuntimeError("persistence reached")
        with pytest.raises(RuntimeError, match="persistence reached"):
            builder._iteration()
