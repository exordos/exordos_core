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

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script", ["build_openapi_spec.sh", "build_docs.sh"])
def test_relative_sdk_path_is_resolved_from_caller(tmp_path, script):
    source = Path(__file__).parents[3] / "exordos/images" / script
    repo = tmp_path / "repo"
    scripts = repo / "exordos/images"
    scripts.mkdir(parents=True)
    shutil.copy2(source, scripts / script)
    caller = tmp_path / "caller"
    sdk = caller / "sdk with spaces"
    sdk.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output = tmp_path / "invocation.json"
    tox = bin_dir / "tox"
    tox.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "pathlib.Path(os.environ['INVOCATION']).write_text(json.dumps("
        "{'cwd': os.getcwd(), 'sdk': os.environ['LOCAL_GENESIS_SDK_PATH']}))\n"
        "pathlib.Path('site').mkdir(exist_ok=True)\n"
    )
    tox.chmod(0o755)
    subprocess.run(
        ["bash", str(scripts / script)],
        cwd=caller,
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "LOCAL_GENESIS_SDK_PATH": sdk.name,
            "INVOCATION": str(output),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(output.read_text()) == {"cwd": str(repo), "sdk": str(sdk)}
