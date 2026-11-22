#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def download(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy(ROOT / "exordos/images/download_rawstor.sh", source / "download.sh")
    (source / "rawstor").mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    names = [
        "librawstor_0.3.2_amd64.deb",
        "rawstor-mds_0.3.2_amd64.deb",
        "rawstor-0.3.2-cp39-abi3-manylinux1_x86_64.manylinux_2_5_x86_64.whl",
    ]
    for name in names:
        (assets / name).write_bytes(("verified " + name).encode())
    (source / "rawstor/SHA256SUMS").write_text(
        "".join(
            hashlib.sha256((assets / n).read_bytes()).hexdigest() + "  " + n + "\n"
            for n in names
        )
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    curl = binary / "curl"
    curl.write_text("""#!/usr/bin/env bash
set -eu
while [[ $# -gt 0 ]]; do
    if [[ "$1" == -o ]]; then
        cp "$TEST_ASSETS/$(basename "$2")" "$2"
        exit 0
    fi
    shift
done
exit 1
""")
    curl.chmod(0o755)

    def run(**overrides):
        env = {
            **os.environ,
            "RAWSTOR_VERSION": "0.3.2",
            "RAWSTOR_WHEEL_VERSION": "0.3.2",
            "TEST_ASSETS": str(assets),
            "PATH": str(binary) + ":" + os.environ["PATH"],
            **overrides,
        }
        return subprocess.run(
            [
                "bash",
                str(source / "download.sh"),
                str(tmp_path / "downloads"),
                "librawstor",
                "rawstor-mds",
            ],
            env=env,
            text=True,
            capture_output=True,
        )

    return run, assets


def test_pinned_assets_are_verified(download):
    run, _ = download
    result = run()
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(": OK") == 3


def test_corrupt_native_package_is_rejected_even_with_wheel_override(download):
    run, assets = download
    (assets / "librawstor_0.3.2_amd64.deb").write_bytes(b"corrupted")
    result = run(RAWSTOR_WHEEL_VERSION="different")
    assert result.returncode != 0
    assert "FAILED" in result.stdout


def test_unknown_wheel_override_requires_a_trusted_digest(download):
    run, _ = download
    result = run(RAWSTOR_WHEEL_VERSION="different")
    assert result.returncode != 0
    assert result.stdout.count(": OK") == 2
    assert "No trusted SHA-256" in result.stderr


@pytest.mark.parametrize("enabled,success", [("false", True), ("true", False)])
def test_arm64_only_builds_without_rawstor(enabled, success):
    script = (ROOT / "exordos/images/install.sh").read_text()
    block = script[
        script.index("RAWSTOR_VERSION=${") : script.index(
            "# The package does not start MDS"
        )
    ]
    result = subprocess.run(
        [
            "bash",
            "-eu",
            "-c",
            "dpkg() { echo arm64; }; sudo() { exit 99; }; GC_PATH=/not-used;\n" + block,
        ],
        env={**os.environ, "RAWSTOR_ENABLED": enabled},
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) == success
    if not success:
        assert "require amd64" in result.stderr
