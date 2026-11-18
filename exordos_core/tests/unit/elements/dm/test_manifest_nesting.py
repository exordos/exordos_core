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

import copy

import pytest

from exordos_core.common import exceptions
from exordos_core.elements.dm import utils


def test_expand_nested_lb_resources_and_parent_links():
    resources = {
        "$core.network.lb": {
            "example_lb": {
                "name": "example-lb",
                "/backend_pools": {
                    "https_pool": {
                        "parent": "$parent:uuid",
                    }
                },
                "/vhosts": {
                    "https": {
                        "parent": "$parent:uuid",
                        "/routes": {
                            "default": {
                                "parent": "$parent:uuid",
                                "lb": "$parent.parent:uuid",
                                "pool": "$parent.parent.backend_pools.$https_pool:uuid",
                                "description": 'f"LB {$parent.parent:name}"',
                            }
                        },
                    }
                },
            }
        }
    }

    expanded = utils.expand_nested_resources(resources)

    assert expanded == {
        "$core.network.lb": {
            "example_lb": {"name": "example-lb"},
        },
        "$core.network.lb.$example_lb.backend_pools": {
            "https_pool": {
                "parent": "$core.network.lb.$example_lb:uuid",
            },
        },
        "$core.network.lb.$example_lb.vhosts": {
            "https": {
                "parent": "$core.network.lb.$example_lb:uuid",
            },
        },
        "$core.network.lb.$example_lb.vhosts.$https.routes": {
            "default": {
                "parent": "$core.network.lb.$example_lb.vhosts.$https:uuid",
                "lb": "$core.network.lb.$example_lb:uuid",
                "pool": "$core.network.lb.$example_lb.backend_pools.$https_pool:uuid",
                "description": 'f"LB {$core.network.lb.$example_lb:name}"',
            },
        },
    }


def test_expand_nested_resources_does_not_mutate_input():
    resources = {
        "$core.compute.nodes": {
            "vm": {
                "name": "demo-vm",
                "/volumes": {"data": {"node": "$parent:uuid"}},
            }
        }
    }
    original = copy.deepcopy(resources)

    utils.expand_nested_resources(resources)

    assert resources == original


def test_expand_nested_resources_inherits_project_id_from_parent():
    resources = {
        "$core.network.lb": {
            "lb": {
                "project_id": "parent-project",
                "/vhosts": {
                    "vhost": {
                        "project_id": "vhost-project",
                        "/routes": {"route": {}},
                    },
                },
                "/backend_pools": {"pool": {}},
            },
        }
    }

    expanded = utils.expand_nested_resources(resources)

    assert (
        expanded["$core.network.lb.$lb.vhosts"]["vhost"]["project_id"]
        == "vhost-project"
    )
    assert (
        expanded["$core.network.lb.$lb.vhosts.$vhost.routes"]["route"]["project_id"]
        == "vhost-project"
    )
    assert (
        expanded["$core.network.lb.$lb.backend_pools"]["pool"]["project_id"]
        == "parent-project"
    )


def test_expand_nested_resources_rejects_parent_above_root():
    resources = {
        "$core.compute.nodes": {
            "vm": {
                "/volumes": {
                    "data": {"node": "$parent.parent:uuid"},
                }
            }
        }
    }

    with pytest.raises(exceptions.ValidateException, match="above the root"):
        utils.expand_nested_resources(resources)
