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

import pytest

from exordos_core.cmd import bootstrap_templates


def _spec(main_cidr, boot_cidr):
    return {
        "admin_password": "secret",
        "stand": {
            "network": {"cidr": main_cidr},
            "boot_network": {"cidr": boot_cidr},
            "bootstraps": [
                {
                    "ports": [
                        {"ip": "10.31.0.2", "mac": "52:54:00:00:00:01"},
                        {"ip": None, "mac": "52:54:00:00:00:02"},
                    ]
                }
            ],
        },
    }


def test_disjoint_networks_build_context():
    ctx = bootstrap_templates._build_template_context(
        _spec("10.31.0.0/22", "10.30.0.0/24")
    )

    assert ctx["main_ip_with_mask"] == "10.31.0.2/22"
    assert ctx["boot_ip_with_mask"] == "10.30.0.2/24"


@pytest.mark.parametrize(
    "boot_cidr",
    [
        "10.31.0.0/24",  # inside the main network
        "10.31.0.0/22",  # same as the main network
        "10.0.0.0/8",  # contains the main network
    ],
)
def test_overlapping_networks_rejected(boot_cidr):
    with pytest.raises(ValueError, match="overlaps main network"):
        bootstrap_templates._build_template_context(_spec("10.31.0.0/22", boot_cidr))
