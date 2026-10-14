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

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

PATH = Path(__file__).parents[4] / "exordos/images/ost/prepare.py"
spec = importlib.util.spec_from_file_location("prepare_ost", PATH)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def commands(existing=False, mounted=False, signature=False):
    def run(command, **kwargs):
        code = 0
        if command[:2] in (["zpool", "list"], ["zpool", "import"], ["zfs", "list"]):
            code = 0 if existing else 1
        elif command[0] == "blkid":
            code = 0 if signature else 2
        return SimpleNamespace(returncode=code)

    def output(command, **kwargs):
        if command[0] == "lsblk":
            return json.dumps(
                {
                    "blockdevices": [
                        {
                            "name": "vdb",
                            "type": "disk",
                            "mountpoints": ["/"] if mounted else [None],
                        }
                    ]
                }
            )
        return "rawstor-ost ONLINE /dev/vdb ONLINE"

    return run, output


def test_first_boot_creates_zfs_on_data_disk_and_second_boot_preserves_it():
    run, output = commands()
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        prepare.prepare()
        creates = [
            c.args[0]
            for c in invoked.call_args_list
            if c.args[0][:2] == ["zpool", "create"]
        ]
        assert len(creates) == 1
        assert creates[0][-1] == "/dev/vdb"
        assert "-f" not in creates[0]
    run, output = commands(existing=True)
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        prepare.prepare()
        assert not any(
            c.args[0][:2] == ["zpool", "create"] for c in invoked.call_args_list
        )


@pytest.mark.parametrize("mounted,signature", [(True, False), (False, True)])
def test_preparation_refuses_mounted_or_nonempty_disks(mounted, signature):
    run, output = commands(mounted=mounted, signature=signature)
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        with pytest.raises(RuntimeError):
            prepare.prepare()
        assert not any(
            c.args[0][:2] == ["zpool", "create"] for c in invoked.call_args_list
        )


def test_build_configuration_contains_ost_provisioner_and_manifest_reference():
    import jinja2
    import yaml

    root = PATH.parents[3]
    configuration = yaml.safe_load((root / "exordos/exordos.yaml").read_text())
    images = configuration["build"]["elements"][0]["images"]
    ost = next(image for image in images if image["name"] == "rawstor-ost")
    assert ost["script"] == "images/install_rawstor_ost.sh"
    assert ost["profile"] == "exordos_base"
    assert ost["format"] == "GEN_IMG_FORMAT_RAWSTOR_OST=raw,zst"
    template = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(
        (root / "exordos/manifests/core.yaml.j2").read_text()
    )
    manifest = yaml.safe_load(
        template.render(
            version="0.3.1",
            images={
                "exordos_core_raw_zst": "urn:images:core",
                "rawstor_ost_raw_zst": "urn:images:ost",
            },
        )
    )
    assert (
        manifest["resources"]["$core.vs.values"]["rawstor_ost_image"]["value"]
        == "urn:images:ost"
    )
