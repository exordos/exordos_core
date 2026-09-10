#    Copyright 2025 Genesis Corporation.
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

import re
import typing as tp

import yaml

CONTENT_TYPE_APPLICATION_YAML = "application/yaml"


def download_response(
    manifest: dict, name: str, version: str
) -> tp.Tuple[bytes, int, dict]:
    """Build a manifest YAML file response for a download action.

    Controllers using it must pack the encoded body as is, see
    `packers.JSONPackerPreEncoded`.
    """
    body = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", f"{name}-{version}.yaml")
    return (
        body.encode(),
        200,
        {
            "Content-Type": CONTENT_TYPE_APPLICATION_YAML,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
