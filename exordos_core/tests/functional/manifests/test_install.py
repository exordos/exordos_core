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
import yaml

from exordos_core.common import exceptions
from exordos_core.common.utils import PROJECT_PATH
from exordos_core.elements.dm import models


class TestManifests:
    def test_check_requirements(
        self,
        full_manifest_schema,
        user_api,
    ):
        core_path = os.path.join(
            PROJECT_PATH, "exordos", "manifests", "examples", "core.element.yaml"
        )
        with open(core_path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest_core = models.Manifest(**manifest_data)
        manifest_core.save()
        manifest_core.install()

        path = os.path.join(
            PROJECT_PATH,
            "exordos_core",
            "tests",
            "functional",
            "manifests",
            "examples",
            "test-export-node-1.yaml",
        )
        with open(path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest_export_1 = models.Manifest(**manifest_data)
        manifest_export_1.save()
        manifest_export_1.install()

        path = os.path.join(
            PROJECT_PATH,
            "exordos_core",
            "tests",
            "functional",
            "manifests",
            "examples",
            "test-export-node-2.yaml",
        )
        with open(path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest_export_2 = models.Manifest(**manifest_data)
        manifest_export_2.save()
        manifest_export_2.install()

        path = os.path.join(
            PROJECT_PATH,
            "exordos_core",
            "tests",
            "functional",
            "manifests",
            "examples",
            "test-import-node.yaml",
        )
        with open(path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest_import_valid = models.Manifest(**manifest_data)
        manifest_import_valid.save()
        manifest_import_valid.install()

        path = os.path.join(
            PROJECT_PATH,
            "exordos_core",
            "tests",
            "functional",
            "manifests",
            "examples",
            "test-import-node-invalid-1.yaml",
        )
        with open(path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest_import_invalid_1 = models.Manifest(**manifest_data)
        manifest_import_invalid_1.save()

        with pytest.raises(exceptions.NamespaceNotFound):
            manifest_import_invalid_1.install()

        path = os.path.join(
            PROJECT_PATH,
            "exordos_core",
            "tests",
            "functional",
            "manifests",
            "examples",
            "test-import-node-invalid-2.yaml",
        )
        with open(path, "r") as f:
            manifest_data = yaml.safe_load(f)

        manifest_import_invalid_2 = models.Manifest(**manifest_data)
        manifest_import_invalid_2.save()

        with pytest.raises(exceptions.ValidateException) as exc_info_2:
            manifest_import_invalid_2.install()

        assert "is not in export list" in exc_info_2.value.err

        manifest_export_2.uninstall()
        manifest_export_2.delete()

        manifest_export_1.uninstall()
        manifest_export_1.delete()

        manifest_import_valid.uninstall()
        manifest_import_valid.delete()

        manifest_import_invalid_1.uninstall()
        manifest_import_invalid_1.delete()

        manifest_import_invalid_2.uninstall()
        manifest_import_invalid_2.delete()

        manifest_core.uninstall()
        manifest_core.delete()
