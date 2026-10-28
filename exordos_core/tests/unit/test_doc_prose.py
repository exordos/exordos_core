# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "text",
    [
        "Implemented in the `example` feature branch.",
        "This branch requires another SDK branch.",
        "Unmerged feature requires an SDK update.",
        "Unshipped migration defines the schema.",
        "Development prototype database is not an upgrade target.",
        "Release the matching SDK first, then update Core.",
        "Реализован в ветке `example`.",
        "В первой версии объединяем подсети.",
        "Для первой версии используется OpenVPN.",
        "В первую версию это не входит.",
        "Ограничения первой реализации: IPv4.",
    ],
)
def test_doc_prose_rejects_development_status_across_line_breaks(tmp_path, text):
    page = tmp_path / "page with spaces.md"
    page.write_text("# Page\n\n" + text.replace(" ", "\n"))
    checker = Path(__file__).parents[3] / "tools/check_doc_prose.py"
    result = subprocess.run(
        [sys.executable, str(checker), str(page)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert f"{page}:3:" in result.stdout


def test_doc_prose_accepts_operational_requirements_and_contributor_workflow(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        "The SDK must support cloud network links.\n"
        "Private Core APIs require an attachment and a return route.\n"
        "Provider failures require independent installations.\n"
        "Image import needs temporary disk space.\n"
        "Prepare fixes on a feature branch with a regression test.\n"
    )
    checker = Path(__file__).parents[3] / "tools/check_doc_prose.py"
    result = subprocess.run(
        [sys.executable, str(checker), str(page)], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout == ""
