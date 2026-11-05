#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml


@pytest.mark.parametrize("existing", [None, {"draft": True}, {"draft": False}])
def test_release_publication_preserves_prepared_notes(tmp_path, existing):
    workflow = (
        pathlib.Path(__file__).parents[3] / ".github/workflows/publish-to-pypi.yml"
    )
    steps = yaml.safe_load(workflow.read_text())["jobs"]["github-release"]["steps"]
    command = next(
        step["run"] for step in steps if step["name"] == "Create GitHub Release"
    )
    state = tmp_path / "release.json"
    notes = "Core email integration and data-preserving upgrade instructions."
    state.write_text(json.dumps(None if existing is None else dict(existing, body=notes)))
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "path = pathlib.Path(os.environ['RELEASE_STATE'])\n"
        "release = json.loads(path.read_text())\n"
        "action = sys.argv[2]\n"
        "if action == 'view':\n"
        "    sys.exit(0 if release is not None else 1)\n"
        "if action == 'create':\n"
        "    if release is not None:\n"
        "        sys.exit('Release already exists')\n"
        "    if '--notes-from-tag' not in sys.argv:\n"
        "        sys.exit('Missing tag notes')\n"
        "    release = {'draft': False, 'body': os.environ['TAG_NOTES']}\n"
        "elif action == 'edit':\n"
        "    if '--draft=false' not in sys.argv or release is None:\n"
        "        sys.exit('Invalid release edit')\n"
        "    release['draft'] = False\n"
        "else:\n"
        "    sys.exit('Unexpected gh action')\n"
        "path.write_text(json.dumps(release))\n"
    )
    gh.chmod(0o755)
    subprocess.run(
        ["bash", "-e", "-c", command],
        check=True,
        env=dict(
            os.environ,
            PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            RELEASE_STATE=str(state),
            TAG_NOTES=notes,
            GITHUB_REF_NAME="0.4.0",
            GITHUB_REPOSITORY="example/core",
        ),
    )
    assert json.loads(state.read_text()) == {"draft": False, "body": notes}
