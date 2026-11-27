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
from http.client import HTTPException, HTTPSConnection
from io import BytesIO
from pathlib import Path
import tarfile

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


SOURCES = (
    ("cdn.jsdelivr.net", f"/npm/mermaid@{VERSION}"),
    ("unpkg.com", f"/mermaid@{VERSION}"),
    ("registry.npmjs.org", f"/mermaid/-/mermaid-{VERSION}.tgz"),
)


def download(host: str, path: str) -> bytes:
    with closing(HTTPSConnection(host, timeout=30)) as connection:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: https://{host}{path}")
        return response.read()


def download_files() -> dict[str, bytes]:
    errors = []
    for host, path in SOURCES:
        try:
            if host == "registry.npmjs.org":
                with tarfile.open(
                    fileobj=BytesIO(download(host, path)), mode="r:gz"
                ) as archive:
                    contents = {}
                    for filename, (source, _) in FILES.items():
                        member = archive.getmember(f"package/{source}")
                        if not member.isfile():
                            raise ValueError(f"Not a regular file: {member.name}")
                        with archive.extractfile(member) as stream:
                            contents[filename] = stream.read()
            else:
                contents = {
                    filename: download(host, f"{path}/{source}")
                    for filename, (source, _) in FILES.items()
                }
            for filename, (_, expected) in FILES.items():
                if hashlib.sha256(contents[filename]).hexdigest() != expected:
                    raise ValueError(f"SHA-256 mismatch: {filename}")
        except (OSError, HTTPException, ValueError, tarfile.TarError, KeyError) as exc:
            errors.append(f"{host}: {exc}")
            print(f"Mermaid {VERSION} download failed from {host}: {exc}", flush=True)
            continue
        print(f"Downloaded Mermaid {VERSION} from {host}")
        return contents
    raise RuntimeError("All Mermaid download sources failed: " + "; ".join(errors))


def main() -> None:
    contents = download_files()
    destination = Path(__file__).resolve().parents[1] / "docs/javascripts/vendor"
    destination.mkdir(parents=True, exist_ok=True)
    for filename, content in contents.items():
        (destination / filename).write_bytes(content)


if __name__ == "__main__":
    main()
