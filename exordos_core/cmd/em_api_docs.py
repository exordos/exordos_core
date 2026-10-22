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

"""Write the table of resource types a manifest may declare.

Every row is derived from the user API create schemas. Run before building
the documentation; nothing else reads these pages.

Both languages are written here because the table is the same table in
either: it was translated by hand once and then drifted, ending up five
resource types short and still listing one that no endpoint has served.
"""

import argparse
import dataclasses
import os
import sys
import typing as tp

from exordos_core.elements.dm import utils

# The page carries mkdocs front matter that nothing else generates, so
# every regeneration used to strip the icon back off the page and the next
# person to regenerate would strip it again.
FRONT_MATTER = "---\nicon: lucide/code\n---\n"

# Relative to the working directory rather than to the installed package:
# this writes into a checkout, and the package it imports may well be
# installed somewhere else.
DEFAULT_OUTPUT_DIR = os.path.join("docs", "em")


@dataclasses.dataclass(frozen=True)
class Page:
    """The page in one language: same rows, translated around them."""

    # mkdocs reads the language off the file name, English carrying none.
    suffix: str
    title: str
    section: str
    headers: tp.Tuple[str, str, str]

    @property
    def filename(self) -> str:
        return f"api_documentation{self.suffix}.md"


PAGES = (
    Page("", "API Documentation", "Resources", ("Entity", "Api", "Manifest")),
    Page(".ru", "Документация API", "Ресурсы", ("Сущность", "API", "Манифест")),
)


def render(page: Page, resources: list) -> str:
    table = utils.generate_resources_markdown_table(resources, headers=page.headers)
    return f"{FRONT_MATTER}# {page.title}\n\n## {page.section}\n\n{table}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"where to write the pages (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    schema = utils.build_full_schema(
        utils.load_base_manifest_schema(), utils.load_user_api_spec()
    )
    resources = utils.extract_resources_for_markdown(schema)
    os.makedirs(args.output_dir, exist_ok=True)
    for page in PAGES:
        path = os.path.join(args.output_dir, page.filename)
        with open(path, "w") as f:
            f.write(render(page, resources))
        print(f"{path}: {len(resources)} resource types")


if __name__ == "__main__":
    sys.exit(main())
