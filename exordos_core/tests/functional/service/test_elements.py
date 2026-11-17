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

from unittest import mock
import uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.elements.services import builders
from exordos_core.elements.dm import models as em_models


class TestElementManagerBuilder:
    def setup_method(self) -> None:
        # Run service
        self._service = builders.ElementManagerBuilder()

    def teardown_method(self) -> None:
        pass

    def test_element_manager_builder(
        self,
        default_node,
    ):
        self._service._iteration()

    def test_delete_orphaned_target_resources(self):
        resource_uuid = uuid.uuid4()
        resources = [
            ua_models.TargetResource(uuid=resource_uuid, kind=kind, value={})
            for kind in ("em_test_orphan_one", "em_test_orphan_two", "test_retained")
        ]
        for resource in resources:
            resource.insert()
        self._service._element_engine = mock.Mock()
        self._service._element_engine.get_resources.return_value = []

        try:
            with contexts.Context().session_manager() as session:
                self._service._actualize_target_resources(session)

            remaining = ua_models.TargetResource.objects.get_all(
                filters={"uuid": dm_filters.EQ(resource_uuid)},
            )
            assert [resource.res_uuid for resource in remaining] == [
                resources[2].res_uuid
            ]
        finally:
            for resource in ua_models.TargetResource.objects.get_all(
                filters={"uuid": dm_filters.EQ(resource_uuid)},
            ):
                resource.delete()

    def test_delete_element_cleans_resources_and_matching_targets(self):
        element = em_models.Element(name="test_delete_element", version="1.0.0")
        element.insert()
        resources = [
            em_models.Resource(
                element=element,
                name=f"resource_{number}",
                resource_link_prefix="$test_delete_element.test.resources",
                value={},
            )
            for number in range(3)
        ]
        targets = []
        em_models.element_engine.add_element(element)
        try:
            for resource in resources:
                resource.insert()
                for kind in (resource.kind, "test_other_kind"):
                    target = ua_models.TargetResource(
                        uuid=resource.uuid, kind=kind, value={}
                    )
                    target.insert()
                    targets.append(target)

            with contexts.Context().session_manager() as session:
                element.delete(session=session)

            assert em_models.Element.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(element.uuid)}
            ) is None
            assert not em_models.Resource.objects.get_all(
                filters={"element": dm_filters.EQ(element.uuid)}
            )
            remaining = ua_models.TargetResource.objects.get_all(
                filters={"uuid": dm_filters.In([r.uuid for r in resources])}
            )
            assert len(remaining) == len(resources)
            assert all(target.kind == "test_other_kind" for target in remaining)
        finally:
            for target in targets:
                if ua_models.TargetResource.objects.get_one_or_none(
                    filters={"res_uuid": dm_filters.EQ(target.res_uuid)}
                ):
                    target.delete()
            if em_models.Element.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(element.uuid)}
            ):
                element.delete()
            em_models.element_engine.remove_element(element)
