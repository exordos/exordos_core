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

"""Download the local Mermaid runtime and its license for documentation builds.

Keep the version on Zensical's Mermaid major. To update, change VERSION and
the SHA-256 values from the corresponding npm distribution. The runtime retains
its bundled dependency notices; both files are copied into the generated site.
"""

from contextlib import closing
import hashlib
from http.client import HTTPSConnection
from pathlib import Path

VERSION = "11.17.2"
FILES = {
    "mermaid.min.js": (
        "dist/mermaid.min.js",
        "581ed7d74bd9048d0e3a91363927d72ef22942d7722546b27f7cc29e35390eb8",
    ),
    "mermaid-LICENSE.txt": (
        "LICENSE",
        "ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80",
    ),
}


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "docs/javascripts/vendor"
    destination.mkdir(parents=True, exist_ok=True)
    for filename, (source, expected) in FILES.items():
        with closing(HTTPSConnection("unpkg.com", timeout=30)) as connection:
            connection.request("GET", f"/mermaid@{VERSION}/{source}")
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(
                    f"Download failed for {filename}: HTTP {response.status}"
                )
            content = response.read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"SHA-256 mismatch for Mermaid {VERSION}: {filename}")
        (destination / filename).write_bytes(content)
        print(f"Downloaded Mermaid {VERSION}: {filename}")


if __name__ == "__main__":
    main()
