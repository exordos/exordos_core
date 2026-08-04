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

import typing as tp
from unittest import mock
import uuid as sys_uuid

import pytest

from gcl_sdk.agents.universal.drivers import pool as ua_pool

from exordos_core.compute.scheduler import service

_DEFAULT_LIBVIRT_SPEC = ua_pool.LibvirtPoolDriverSpec(
    connection_uri="qemu+tcp://127.0.0.1/system",
)


def _make_local_hyper_spec(node: sys_uuid.UUID) -> ua_pool.ExordosLocalHyperDriverSpec:
    return ua_pool.ExordosLocalHyperDriverSpec(
        connection_uri="qemu+tcp://127.0.0.1/system",
        node=node,
    )


def _make_agent(
    uuid: sys_uuid.UUID,
    node: tp.Optional[sys_uuid.UUID] = None,
    capabilities: tp.Optional[list] = None,
) -> mock.MagicMock:
    agent = mock.MagicMock()
    agent.uuid = uuid
    agent.node = node
    agent.list_capabilities = capabilities or ["pool"]
    return agent


class TestSchedulePoolsLocalHyper:
    """Tests for pool scheduling with exordos_local_hyper kind."""

    def setup_method(self) -> None:
        self._service = service.SchedulerService(
            pool_filters=[],
            pool_weighters=[],
            machine_filters=[],
            machine_weighters=[],
        )

    def _make_pool(
        self,
        driver_spec: tp.Optional[ua_pool.AbstractPoolDriverSpec] = None,
    ) -> mock.MagicMock:
        pool = mock.MagicMock()
        pool.uuid = sys_uuid.uuid4()
        pool.driver_spec = driver_spec or _DEFAULT_LIBVIRT_SPEC
        pool.builder = None
        pool.agent = None
        pool.update = mock.MagicMock()
        return pool

    @pytest.fixture
    def builder(self):
        builder = mock.MagicMock()
        builder.uuid = sys_uuid.uuid4()
        return builder

    @pytest.fixture
    def regular_agent(self):
        return _make_agent(
            uuid=sys_uuid.uuid4(),
            node=sys_uuid.uuid4(),
            capabilities=["pool"],
        )

    @pytest.fixture
    def local_agent(self):
        node = sys_uuid.uuid4()
        return _make_agent(
            uuid=sys_uuid.uuid4(),
            node=node,
            capabilities=["pool", "local_pool"],
        )

    @pytest.fixture
    def local_pool(self, local_agent):
        return self._make_pool(
            driver_spec=_make_local_hyper_spec(local_agent.node),
        )

    @pytest.fixture
    def regular_pool(self):
        return self._make_pool(driver_spec=_DEFAULT_LIBVIRT_SPEC)

    def test_local_pool_scheduled_on_matching_agent(
        self,
        builder,
        local_agent,
        regular_agent,
        local_pool,
    ):
        """Local hyper pool should be scheduled on the agent with matching node."""
        with (
            mock.patch.object(
                self._service, "_get_unscheduled_pools", return_value=[local_pool]
            ),
            mock.patch.object(
                service.ua_models.UniversalAgent,
                "have_capabilities",
                return_value={"pool": [regular_agent, local_agent]},
            ),
        ):
            self._service._schedule_pools([builder])

        assert local_pool.agent == local_agent.uuid
        assert local_pool.builder == builder.uuid
        local_pool.update.assert_called_once()

    def test_local_pool_not_scheduled_without_matching_agent(
        self,
        builder,
        regular_agent,
        local_pool,
    ):
        """Local hyper pool should not be scheduled if no agent with matching node."""
        with (
            mock.patch.object(
                self._service, "_get_unscheduled_pools", return_value=[local_pool]
            ),
            mock.patch.object(
                service.ua_models.UniversalAgent,
                "have_capabilities",
                return_value={"pool": [regular_agent]},
            ),
        ):
            self._service._schedule_pools([builder])

        assert local_pool.agent is None
        local_pool.update.assert_not_called()

    def test_regular_pool_excludes_local_agent(
        self,
        builder,
        regular_agent,
        local_agent,
        regular_pool,
    ):
        """Regular pool should not be scheduled on a local agent."""
        with (
            mock.patch.object(
                self._service, "_get_unscheduled_pools", return_value=[regular_pool]
            ),
            mock.patch.object(
                service.ua_models.UniversalAgent,
                "have_capabilities",
                return_value={"pool": [regular_agent, local_agent]},
            ),
        ):
            self._service._schedule_pools([builder])

        assert regular_pool.agent == regular_agent.uuid
        assert regular_pool.builder == builder.uuid
        regular_pool.update.assert_called_once()

    def test_regular_pool_not_scheduled_if_only_local_agents(
        self,
        builder,
        local_agent,
        regular_pool,
    ):
        """Regular pool should not be scheduled if only local agents available."""
        with (
            mock.patch.object(
                self._service, "_get_unscheduled_pools", return_value=[regular_pool]
            ),
            mock.patch.object(
                service.ua_models.UniversalAgent,
                "have_capabilities",
                return_value={"pool": [local_agent]},
            ),
        ):
            self._service._schedule_pools([builder])

        assert regular_pool.agent is None
        regular_pool.update.assert_not_called()

    def test_mixed_pools_scheduled_correctly(
        self,
        builder,
        regular_agent,
        local_agent,
        regular_pool,
        local_pool,
    ):
        """Both regular and local pools should be scheduled on correct agents."""
        with (
            mock.patch.object(
                self._service,
                "_get_unscheduled_pools",
                return_value=[regular_pool, local_pool],
            ),
            mock.patch.object(
                service.ua_models.UniversalAgent,
                "have_capabilities",
                return_value={"pool": [regular_agent, local_agent]},
            ),
        ):
            self._service._schedule_pools([builder])

        assert regular_pool.agent == regular_agent.uuid
        assert local_pool.agent == local_agent.uuid
        regular_pool.update.assert_called_once()
        local_pool.update.assert_called_once()
