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

"""What may be written into the address ledger."""

from unittest import mock

import netaddr
import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.user_api.network.dm import models as net_models


def _subnet(cidr="10.42.0.0/24"):
    return mock.MagicMock(cidr=netaddr.IPNetwork(cidr) if cidr else None)


def _check(address, subnet=None):
    """Called unbound on a stand-in: a model built with `__new__` lies about
    every field but the last one assigned."""
    net_models.Address._validate_address_value(
        mock.MagicMock(address=address), subnet or _subnet()
    )


@pytest.mark.parametrize(
    "value", ["not-an-address", "10.42.0.256", "10.42.0.0/24", "5", ""]
)
def test_a_value_that_is_not_an_address_is_refused(value):
    """The driver reads this ledger every pass so as not to allocate over a
    reservation, and `in subnet.cidr` raises on such a value — one row would
    switch the reservations off for the whole subnet, silently."""
    with pytest.raises(ex_exceptions.ValidateException) as exc:
        _check(value)
    assert "not an IP address" in str(exc.value)


def test_an_address_of_another_subnet_is_refused():
    """Counted against a pool it is not in, and sent to a data plane where
    no link holds it."""
    with pytest.raises(ex_exceptions.ValidateException) as exc:
        _check("10.99.0.5")
    assert "is not in" in str(exc.value)


def test_an_address_of_this_subnet_is_fine():
    _check("10.42.0.5")


def test_no_address_is_the_auto_allocation_path():
    """Omitted means "pick one for me", which is decided after this."""
    _check(None)


def test_a_subnet_with_no_cidr_has_nothing_to_hold_it_to():
    _check("10.42.0.5", subnet=_subnet(cidr=None))
