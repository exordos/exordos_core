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

import uuid as sys_uuid

import pytest

from exordos_core.common import constants as c
from exordos_core.quota.dm.models import QuotaExceededError
from exordos_core.quota.dm.models import QuotaLimit
from exordos_core.user_api.network.dm.models import LB


@pytest.fixture
def project_id():
    return sys_uuid.uuid4()


_TABLENAME = "net_lb"


@pytest.fixture
def _quota_limit_2(user_api):
    obj = QuotaLimit(
        uuid=sys_uuid.uuid4(),
        project_id=c.SERVICE_PROJECT_ID,
        resource_name=_TABLENAME,
        limit=2,
    )
    obj.insert()
    yield obj
    try:
        obj.delete()
    except Exception:
        pass


class TestQuotaNoLimit:
    def test_creates_entities_with_default_limit(self, user_api, lb_factory_with_model):
        _, lb1 = lb_factory_with_model()
        _, lb2 = lb_factory_with_model()

        lb1.insert()
        lb2.insert()

        entities_count = LB.objects.count()
        assert entities_count == 2

        lb1.delete()
        lb2.delete()


class TestQuotaAggregateFieldLimit:
    @pytest.fixture
    def _quota_limits(self, user_api):
        limits = [
            QuotaLimit(
                uuid=sys_uuid.uuid4(),
                project_id=c.SERVICE_PROJECT_ID,
                resource_name="nodes",
                field_name="cores",
                limit=4,
            ),
            QuotaLimit(
                uuid=sys_uuid.uuid4(),
                project_id=c.SERVICE_PROJECT_ID,
                resource_name="nodes",
                field_name="ram",
                limit=4096,
            ),
        ]
        for limit in limits:
            limit.insert()
        yield limits
        for limit in limits:
            try:
                limit.delete()
            except Exception:
                pass

    def test_rejects_unknown_quota_field(self, user_api, node_factory_with_model):
        quota_limit = QuotaLimit(
            uuid=sys_uuid.uuid4(),
            project_id=c.SERVICE_PROJECT_ID,
            resource_name="nodes",
            field_name="unknown",
            limit=1,
        )
        quota_limit.insert()
        _, node = node_factory_with_model()

        try:
            with pytest.raises(ValueError, match="Unknown quota field\(s\): unknown"):
                node.insert()
        finally:
            quota_limit.delete()

    def test_blocks_nodes_when_cores_limit_is_exceeded(
        self,
        _quota_limits,
        node_factory_with_model,
    ):
        _, first_node = node_factory_with_model(cores=2, ram=1024)
        _, second_node = node_factory_with_model(cores=3, ram=1024)

        first_node.insert()
        with pytest.raises(QuotaExceededError) as exc_info:
            second_node.insert()

        assert exc_info.value.resource_name == "nodes.cores"
        assert exc_info.value.limit == 4
        assert exc_info.value.current == 5

        first_node.delete()

    def test_blocks_nodes_when_ram_limit_is_exceeded(
        self,
        _quota_limits,
        node_factory_with_model,
    ):
        _, first_node = node_factory_with_model(cores=1, ram=2048)
        _, second_node = node_factory_with_model(cores=1, ram=3072)

        first_node.insert()
        with pytest.raises(QuotaExceededError) as exc_info:
            second_node.insert()

        assert exc_info.value.resource_name == "nodes.ram"
        assert exc_info.value.limit == 4096
        assert exc_info.value.current == 5120

        first_node.delete()


class TestQuotaWithLimit:
    def test_creates_entities(
        self,
        _quota_limit_2,
        lb_factory_with_model,
    ):
        _, lb = lb_factory_with_model()
        lb.insert()

        entities_count = LB.objects.count()
        assert entities_count == 1

        lb.delete()

    def test_blocks_creation_when_exceeded(
        self,
        _quota_limit_2,
        lb_factory_with_model,
    ):
        _, lb1 = lb_factory_with_model()
        _, lb2 = lb_factory_with_model()
        _, lb3 = lb_factory_with_model()

        lb1.insert()
        lb2.insert()
        with pytest.raises(QuotaExceededError):
            lb3.insert()

        lb1.delete()
        lb2.delete()

    def test_delete_releases_entity(
        self,
        _quota_limit_2,
        lb_factory_with_model,
    ):
        _, lb = lb_factory_with_model()
        lb.insert()

        entities_count_before = LB.objects.count()
        assert entities_count_before == 1

        lb.delete()

        entities_count_after = LB.objects.count()
        assert entities_count_after == 0

    def test_create_after_delete_respects_limit(
        self,
        _quota_limit_2,
        lb_factory_with_model,
    ):
        _, lb1 = lb_factory_with_model()
        _, lb2 = lb_factory_with_model()

        lb1.insert()
        lb1.delete()
        lb2.insert()

        lb2.delete()

    def test_limit_isolated_per_project(
        self,
        _quota_limit_2,
        lb_factory_with_model,
        project_id,
    ):

        _, lb_a = lb_factory_with_model(project_id=project_id)
        _, lb_b = lb_factory_with_model()  # default: SERVICE_PROJECT_ID

        lb_a.insert()
        lb_b.insert()  # different project, should succeed even though limit=2

        lb_a.delete()
        lb_b.delete()

    def test_exceeded_error_details(
        self,
        _quota_limit_2,
        lb_factory_with_model,
    ):
        _, lb1 = lb_factory_with_model()
        _, lb2 = lb_factory_with_model()
        _, lb3 = lb_factory_with_model()

        lb1.insert()
        lb2.insert()
        with pytest.raises(QuotaExceededError) as exc_info:
            lb3.insert()

        assert exc_info.value.resource_name == _TABLENAME
        assert exc_info.value.limit == 2
        assert exc_info.value.current == 3
        assert exc_info.value.project_id == c.SERVICE_PROJECT_ID

        lb1.delete()
        lb2.delete()
