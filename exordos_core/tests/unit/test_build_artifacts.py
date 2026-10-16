#    Copyright 2026 Genesis Corporation.
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

from pathlib import Path

import yaml


def test_default_core_build_supports_bootstrap_and_seed_upgrade():
    root = Path(__file__).resolve().parents[3]
    config = yaml.safe_load((root / "exordos/exordos.yaml").read_text())
    core = next(
        element
        for element in config["build"]["elements"]
        if element["manifest"] == "manifests/core.yaml.j2"
    )
    image = next(image for image in core["images"] if image["name"] == "exordos-core")
    default_formats = image["format"].split("=", 1)[1].split(",")

    assert "qcow2" in default_formats
    assert "zst" in default_formats
    assert (
        "images/exordos-core.raw.zst"
        in (root / "exordos/manifests/core.yaml.j2").read_text()
    )
