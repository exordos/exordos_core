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

from unittest import mock

import pytest

from exordos_core.bootstrap import defaults
from exordos_core.common import constants as c


def test_bootstrap_creates_sync_only_domain_from_realm_spec():
    with mock.patch.object(defaults.dns_models, "Domain") as domain_model:
        domain_model.objects.get_one_or_none.return_value = None

        assert defaults.ensure_realm_dns_domain(
            {
                "realm_id": "a5f3957",
                "realm_domain": "a5f3957.exordos.io",
            }
        )

    domain_model.assert_called_once_with(
        name="exordos.io",
        realm_id="a5f3957",
        sync_only=True,
        sync_to_ecosystem=True,
        project_id=c.ZERO_UUID,
    )
    domain_model.return_value.insert.assert_called_once_with()


def test_bootstrap_accepts_matching_existing_domain():
    existing = mock.Mock(
        realm_id="a5f3957",
        sync_only=True,
        sync_to_ecosystem=True,
    )
    with mock.patch.object(defaults.dns_models, "Domain") as domain_model:
        domain_model.objects.get_one_or_none.return_value = existing

        assert defaults.ensure_realm_dns_domain(
            {
                "realm_id": "a5f3957",
                "realm_domain": "a5f3957.exordos.io",
            }
        )

    domain_model.assert_not_called()


@pytest.mark.parametrize(
    "spec",
    [
        {"realm_id": "a5f3957"},
        {"realm_domain": "a5f3957.exordos.io"},
        {"realm_id": "a5f3957", "realm_domain": "other.exordos.io"},
    ],
)
def test_bootstrap_rejects_incomplete_or_mismatched_realm_dns(spec):
    with pytest.raises(RuntimeError):
        defaults.ensure_realm_dns_domain(spec)
