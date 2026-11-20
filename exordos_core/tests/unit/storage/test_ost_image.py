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


OST_UUID = "f0aa9354-dfa3-5280-b1f9-33d351edc655"
VG = f"rawstor-{OST_UUID}"


def commands(physical_volumes=(), mounted=False, signature=False):
    def run(command, **kwargs):
        return SimpleNamespace(
            returncode=(0 if signature else 2) if command[0] == "blkid" else 0
        )

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
        assert command[0] == "pvs"
        return json.dumps({"report": [{"pv": list(physical_volumes)}]})

    return run, output


def test_first_boot_creates_lvm_on_data_disk():
    run, output = commands()
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        prepare.prepare(OST_UUID)
    assert [call.args[0] for call in invoked.call_args_list] == [
        ["blkid", "-p", "/dev/vdb"],
        ["pvcreate", "/dev/vdb"],
        ["vgcreate", VG, "/dev/vdb"],
        ["vgchange", "--activate", "y", VG],
    ]


@pytest.mark.parametrize("group", [VG, ""])
def test_existing_pv_is_preserved_and_partial_initialization_can_resume(group):
    run, output = commands(physical_volumes=[{"pv_name": "/dev/vdb", "vg_name": group}])
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        prepare.prepare(OST_UUID)
    expected = [] if group else [["vgcreate", VG, "/dev/vdb"]]
    assert [call.args[0] for call in invoked.call_args_list] == [
        *expected,
        ["vgchange", "--activate", "y", VG],
    ]


@pytest.mark.parametrize("mounted,signature", [(True, False), (False, True)])
def test_preparation_refuses_mounted_or_nonempty_disks(mounted, signature):
    run, output = commands(mounted=mounted, signature=signature)
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        with pytest.raises(RuntimeError):
            prepare.prepare(OST_UUID)
    assert all(call.args[0][0] == "blkid" for call in invoked.call_args_list)


@pytest.mark.parametrize(
    "physical_volumes",
    [
        [{"pv_name": "/dev/vdb", "vg_name": "other"}],
        [{"pv_name": "/dev/vdc", "vg_name": VG}],
        [
            {"pv_name": "/dev/vdb", "vg_name": VG},
            {"pv_name": "/dev/vdc", "vg_name": VG},
        ],
    ],
)
def test_preparation_rejects_wrong_volume_group_devices(physical_volumes):
    run, output = commands(physical_volumes=physical_volumes)
    with (
        mock.patch.object(prepare.subprocess, "run", side_effect=run) as invoked,
        mock.patch.object(prepare.subprocess, "check_output", side_effect=output),
    ):
        with pytest.raises(RuntimeError):
            prepare.prepare(OST_UUID)
    invoked.assert_not_called()


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
