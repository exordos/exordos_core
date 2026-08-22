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
from exordos_core.user_api.quota.api.controllers import QuotaLimitController


@pytest.fixture
def project_id():
    return sys_uuid.uuid4()


_TABLENAME = "net_lb"


@pytest.fixture
def _quota_limit_2(user_api):
    obj = QuotaLimit(
        uuid=sys_uuid.uuid4(),
        project_id=c.ADMIN_PROJECT_UUID,
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
                project_id=c.ADMIN_PROJECT_UUID,
                resource_name="nodes",
                field_name="cores",
                limit=4,
            ),
            QuotaLimit(
                uuid=sys_uuid.uuid4(),
                project_id=c.ADMIN_PROJECT_UUID,
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

    def test_rejects_unknown_quota_resource(self):
        with pytest.raises(ValueError, match="Unknown quota resource: unknown"):
            QuotaLimitController._validate_quota_field("unknown", "")

    def test_rejects_unknown_quota_field(self):
        with pytest.raises(ValueError, match="Unknown quota field: unknown"):
            QuotaLimitController._validate_quota_field("nodes", "unknown")

    def test_rejects_non_integer_quota_field(self):
        with pytest.raises(ValueError, match="Quota field must be an integer: name"):
            QuotaLimitController._validate_quota_field("nodes", "name")

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

    def test_node_field_quotas_are_isolated_per_project(
        self,
        _quota_limits,
        node_factory_with_model,
    ):
        # Assume the quota limits fixture sets per-project limits:
        # cores limit: 4, ram limit: 4096 for each project.
        project_b_uuid = sys_uuid.uuid4()
        project_b_limits = [
            QuotaLimit(
                uuid=sys_uuid.uuid4(),
                project_id=project_b_uuid,
                resource_name="nodes",
                field_name="cores",
                limit=4,
            ),
            QuotaLimit(
                uuid=sys_uuid.uuid4(),
                project_id=project_b_uuid,
                resource_name="nodes",
                field_name="ram",
                limit=4096,
            ),
        ]
        for limit in project_b_limits:
            limit.insert()

        # Project A: exceed cores/ram limits and ensure quota is enforced
        _, a_node1 = node_factory_with_model(
            project_id=c.ADMIN_PROJECT_UUID,
            cores=2,
            ram=2048,
        )
        _, a_node2 = node_factory_with_model(
            project_id=c.ADMIN_PROJECT_UUID,
            cores=2,
            ram=2048,
        )
        _, a_node3 = node_factory_with_model(
            project_id=c.ADMIN_PROJECT_UUID,
            cores=1,
            ram=1024,
        )

        a_node1.insert()
        a_node2.insert()

        # Exceeding the limit in project A should raise, and the error
        # values should only reflect usage in project A.
        with pytest.raises(QuotaExceededError) as exc_info:
            a_node3.insert()

        assert exc_info.value.resource_name in {"nodes.cores", "nodes.ram"}
        assert exc_info.value.limit in {4, 4096}
        assert exc_info.value.current in {5, 5120}

        # Project B: usage should be independent of project A.
        # Staying within limits in project B must not raise.
        _, b_node1 = node_factory_with_model(
            project_id=project_b_uuid,
            cores=2,
            ram=2048,
        )
        _, b_node2 = node_factory_with_model(
            project_id=project_b_uuid,
            cores=2,
            ram=2048,
        )

        b_node1.insert()
        b_node2.insert()

        # Clean up nodes
        a_node1.delete()
        a_node2.delete()
        a_node3.delete()
        b_node1.delete()
        b_node2.delete()
        for limit in project_b_limits:
            limit.delete()

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
        _, lb_b = lb_factory_with_model()  # default: ADMIN_PROJECT_UUID

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
        assert exc_info.value.project_id == c.ADMIN_PROJECT_UUID

        lb1.delete()
        lb2.delete()
