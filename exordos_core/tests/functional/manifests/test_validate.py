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

import os

import pytest
import requests
import yaml

from exordos_core.common import exceptions
from exordos_core.common.utils import PROJECT_PATH
from exordos_core.elements.dm import models
from exordos_core.elements.dm import utils

REPO_URL = "https://repo.exordos.com/exordos-elements"


class TestSpec:
    @pytest.mark.parametrize(
        "element",
        [
            "communal_pg_cluster",
            "core",
            "dbaas",
            "core-node-set-example",
            "core-service-example",
            "genesis_notification",
            # TODO(slashburygin): need mutate imports to validate them
        ],
    )
    def test_validate_manifests(
        self, element, base_manifest_schema, full_manifest_schema
    ):
        resp = requests.get(f"{REPO_URL}/{element}/latest/manifests/{element}.yaml")
        if resp.status_code == 404:
            pytest.skip(f"latest version for {element} is not available")
        assert resp.status_code == 200
        manifest = yaml.safe_load(resp.text)

        utils.validate_manifest(manifest, base_manifest_schema)

        # TODO(slashburygin): full_schema is not ready now
        mutated_manifest = utils.mutate_manifest(manifest, full_manifest_schema)
        utils.validate_manifest(mutated_manifest, full_manifest_schema)

    def test_validate_example(
        self,
        full_manifest_schema,
        user_api,
    ):
        path = os.path.join(
            PROJECT_PATH, "exordos", "manifests", "examples", "core.element.yaml"
        )
        with open(path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest = models.Manifest(**manifest_data)
        manifest.save()

        manifest.install()

        manifest.validate_schema_base()
        manifest.validate_schema_full()

        manifest.uninstall()
        manifest.delete()

    @pytest.mark.parametrize(
        "invalid_manifest",
        [
            "invalid_exports.yaml",
            "invalid_imports.yaml",
            "invalid_resource.yaml",
        ],
    )
    def test_validate_error(
        self, invalid_manifest, base_manifest_schema, full_manifest_schema
    ):
        with open(
            os.path.join(
                PROJECT_PATH,
                "exordos_core",
                "tests",
                "functional",
                "manifests",
                "examples",
                invalid_manifest,
            ),
            "r",
        ) as f:
            manifest = yaml.safe_load(f)
        with pytest.raises(exceptions.OpenApiValidateException):
            utils.validate_manifest(manifest, base_manifest_schema)
            utils.validate_manifest(manifest, full_manifest_schema)

    @pytest.mark.skip(reason="for manual running")
    def test_build_full_schema(self, base_manifest_schema, user_api_spec):
        full_schema = utils.build_full_schema(base_manifest_schema, user_api_spec)
        utils.dump_full_manifest_schema(full_schema)

        resources = utils.extract_resources_for_markdown(full_schema)
        content = "# API Documentation\n\n"
        content += "## Resources\n\n"
        content += utils.generate_resources_markdown_table(resources)
        content += "\n"
        utils.dump_api_spec(content)
