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

import uuid as sys_uuid

import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.user_api.network.dm import models

PROJECT = sys_uuid.uuid4()


@pytest.mark.parametrize("port", ["http", "80", 0, 65536, True, 1.5])
def test_a_port_that_is_not_one_is_refused_not_crashed_on(port):
    rule = {"direction": "ingress", "protocol": "tcp", "port": port}
    with pytest.raises(ex_exceptions.ValidateException):
        models.validate_filter_rules([rule], PROJECT)


def test_a_port_is_accepted():
    models.validate_filter_rules(
        [{"direction": "ingress", "protocol": "tcp", "port": 443}], PROJECT
    )


def test_a_key_nothing_reads_is_refused():
    """A misspelt peer would compile to the rule without it: wider."""
    rule = {"direction": "ingress", "protocol": "tcp", "remote": "10.0.0.0/8"}
    with pytest.raises(ex_exceptions.ValidateException):
        models.validate_filter_rules([rule], PROJECT)


def test_a_protocol_under_another_spelling_is_refused():
    """`proto` escaped the protocol check, and one protocol the data plane
    cannot compile fails the port's whole flow bundle."""
    rule = {"direction": "ingress", "proto": "gre"}
    with pytest.raises(ex_exceptions.ValidateException):
        models.validate_filter_rules([rule], PROJECT)


@pytest.mark.parametrize("remote_ip", ["2001:db8::/32", "::1", "10.0.0.0/8,x=1"])
def test_a_remote_the_data_plane_cannot_compile_is_refused(remote_ip):
    """One IPv6 match fails the port's whole flow bundle, not just the rule."""
    rule = {"direction": "ingress", "protocol": "tcp", "remote_ip": remote_ip}
    with pytest.raises(ex_exceptions.ValidateException):
        models.validate_filter_rules([rule], PROJECT)


@pytest.mark.parametrize("remote_ip", ["10.0.0.0/8", "192.0.2.7"])
def test_an_ipv4_remote_is_accepted(remote_ip):
    rule = {"direction": "ingress", "protocol": "tcp", "remote_ip": remote_ip}
    models.validate_filter_rules([rule], PROJECT)
