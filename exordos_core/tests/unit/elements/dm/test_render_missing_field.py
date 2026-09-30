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

from exordos_core.elements.dm.models import Element
from exordos_core.elements.dm.models import Resource

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
