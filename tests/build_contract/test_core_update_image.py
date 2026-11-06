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

import json
from pathlib import Path
from unittest import mock

from exordos.builder import base
from exordos.builder.builder import SimpleBuilder
import pytest
import yaml


@pytest.mark.parametrize("formats", [None, "zst", "qcow2,zst"])
def test_core_update_image_resolves_to_built_raw_image(tmp_path, monkeypatch, formats):
    if formats is None:
        monkeypatch.delenv("GEN_IMG_FORMAT_CORE", raising=False)
    else:
        monkeypatch.setenv("GEN_IMG_FORMAT_CORE", formats)
    source = Path(__file__).parents[2] / "exordos"
    config = yaml.safe_load((source / "exordos.yaml").read_text())["build"]
    element = base.Element.from_config(config["elements"][0], source)
    # Stub only expensive image and OpenAPI generation, retaining the real
    # manifest renderer and inventory writer used by `exordos build`.
    element.artifacts = []
    images = [Path(name) for image in element.images for name in image.names]
    builder = SimpleBuilder(
        exordos_dir=source,
        deps=[],
        elements=[element],
        image_builder=mock.Mock(),
        elements_output_dir=tmp_path,
        version="0.0.0.dev1+cassi.local",
    )
    with mock.patch.object(builder, "_build_images", return_value=images):
        builder.build()

    repo = tmp_path / "exordos-elements"
    inventory = json.loads((repo / "inventory.json").read_text())
    built = inventory["elements"]["core"]["0.0.0.dev1+cassi.local"]
    manifest = yaml.safe_load(
        (repo / "core" / built["version"] / "manifests/core.yaml").read_text()
    )
    disks = manifest["resources"]["$core.compute.sets"]["core_set"]["disk_spec"][
        "disks"
    ]
    image = disks[0]["image"]
    assert image.startswith("urn:images:")
    image_id = image.removeprefix("urn:images:")
    assert built["index"]["images"][image_id] == "exordos-core.raw.zst"
    assert "exordos-core.raw.zst" in built["images"]
    assert disks[1].get("image") is None
    assert disks[1]["label"] == "data"
