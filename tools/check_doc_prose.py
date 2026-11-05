# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

"""Reject development-status prose in published documentation."""

from pathlib import Path
import re
import sys

DEVELOPMENT_STATUS = re.compile(
    r"\b(?:implemented|available)\s+in\s+(?:the\s+)?"
    r"(?:`[^`]+`\s+)?(?:feature\s+)?branch\b"
    r"|\bthis\s+branch\s+(?:requires|implements|adds|needs)\b"
    r"|\b(?:unmerged|unshipped)\s+(?:feature|migration)\b"
    r"|\bdevelopment\s+prototype\b"
    r"|\brelease\s+the\s+matching\s+SDK\s+first\b"
    r"|\bреализован[аоы]?\s+в\s+ветке\b"
    r"|\b(?:в|для)\s+перв(?:ой|ую)\s+верси(?:и|ю)\b"
    r"|\bограничения\s+первой\s+реализации\b",
    re.IGNORECASE,
)


def main(paths):
    failed = False
    for path in paths:
        text = path.read_text()
        for match in DEVELOPMENT_STATUS.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            phrase = " ".join(match.group().split())
            print(
                f"{path}:{line}: development status in product documentation: {phrase}"
            )
            failed = True
    return int(failed)


if __name__ == "__main__":
    paths = [Path(p) for p in sys.argv[1:]] or [
        Path("README.md"),
        *sorted(Path("docs").rglob("*.md")),
    ]
    sys.exit(main(paths))
