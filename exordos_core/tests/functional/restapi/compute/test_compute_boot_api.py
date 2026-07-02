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

import typing as tp
from urllib.parse import urljoin
import uuid as sys_uuid

from oslo_config import cfg
import pytest
import requests

from exordos_core.boot_api.api import app as orch_app
from exordos_core.cmd import boot_api as boot_api_cmd
from exordos_core.common import constants as c
from exordos_core.compute.dm import models
from exordos_core.tests.functional import conftest
from exordos_core.tests.functional import utils as test_utils

CONF = cfg.CONF


class TestComputeBootApi:
    @pytest.fixture(scope="class")
    def boot_api_service(self):
        class ApiRestService(test_utils.RestServiceTestCase):
            __FIRST_MIGRATION__ = conftest.FIRST_MIGRATION
            __APP__ = orch_app.build_wsgi_application()

        rest_service = ApiRestService()
        rest_service.setup_class()

        yield rest_service

        rest_service.teardown_class()

    @pytest.fixture()
    def boot_api(self, boot_api_service: test_utils.RestServiceTestCase):
        boot_api_service.setup_method()

        yield boot_api_service

        boot_api_service.teardown_method()

    def test_netboots_default_net(self, boot_api: test_utils.RestServiceTestCase):
        CONF[boot_api_cmd.DOMAIN].gc_host = "10.20.0.2"

        uuid = sys_uuid.uuid4()
        url = urljoin(boot_api.base_url, f"boots/{str(uuid)}")

        response = requests.get(url)

        assert response.status_code == 200
        assert response.text.startswith("#!ipxe")
        assert "initrd" in response.text
        assert "vmlinuz" in response.text
        assert "gc_boot_api" in response.text
        assert "tftp://10.20.0.2" in response.text

    def test_netboots_hd_boot(
        self,
        pool_factory: tp.Callable,
        boot_api: test_utils.RestServiceTestCase,
    ):
        CONF[boot_api_cmd.DOMAIN].gc_host = "10.20.0.2"

        pool_view = pool_factory()
        pool_view["status"] = "ACTIVE"
        pool = models.MachinePool.restore_from_simple_view(**pool_view)
        pool.insert()

        uuid = sys_uuid.uuid4()
        machine = models.Machine(
            cores=1,
            ram=1024,
            boot="hd0",
            uuid=uuid,
            firmware_uuid=uuid,
            pool=pool.uuid,
            status="ACTIVE",
            project_id=c.SERVICE_PROJECT_ID,
        )
        machine.insert()

        url = urljoin(boot_api.base_url, f"boots/{machine.uuid}")

        response = requests.get(url)

        assert response.status_code == 200
        assert response.text.startswith("#!ipxe")
        assert "initrd" not in response.text
        assert "vmlinuz" not in response.text
        assert "0x80" in response.text

        machine.delete()
        pool.delete()

    def test_netboots_default_net_custom_kernel_initrd(
        self, boot_api: test_utils.RestServiceTestCase
    ):
        CONF[boot_api_cmd.DOMAIN].kernel = "https://kernel.org/vmlinuz"
        CONF[boot_api_cmd.DOMAIN].initrd = "https://kernel.org/initrd.img"

        uuid = sys_uuid.uuid4()
        url = urljoin(boot_api.base_url, f"boots/{str(uuid)}")

        response = requests.get(url)

        assert response.status_code == 200
        assert response.text.startswith("#!ipxe")
        assert "initrd" in response.text
        assert "vmlinuz" in response.text
        assert "gc_boot_api" in response.text
        assert "tftp://" not in response.text
        assert "https://kernel.org/vmlinuz" in response.text
        assert "https://kernel.org/initrd.img" in response.text
