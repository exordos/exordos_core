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

from gcl_sdk.agents.universal import constants as ua_c
from gcl_sdk.infra import constants as infra_c
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.vs.builders import service
from exordos_core.vs.dm import models


class TestVSServiceBuilder:
    def setup_method(self) -> None:
        self._service = service.VSBuilderService()

    def teardown_method(self) -> None:
        pass

    def test_variables_value_depends_on_active_global_profile(
        self,
        default_node: tp.Dict[str, tp.Any],
    ):
        profile_1 = models.Profile(
            name="p1",
            profile_type=infra_c.ProfileType.GLOBAL.value,
            active=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        profile_1.insert()

        profile_2 = models.Profile(
            name="p2",
            profile_type=infra_c.ProfileType.GLOBAL.value,
            active=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        profile_2.insert()
        profile_2.activate()

        variable = models.Variable(
            name="var_profile",
            project_id=c.SERVICE_PROJECT_ID,
            status=ua_c.InstanceStatus.NEW.value,
            setter=models.ProfileVariableSetter(
                profiles=[
                    {"profile": profile_1.uuid, "value": "value_1"},
                    {"profile": profile_2.uuid, "value": "value_2"},
                ],
            ),
        )
        variable.insert()

        self._service._iteration()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "ACTIVE"
        assert updated.value == "value_2"

        variable.delete()
        profile_2.delete()
        profile_1.delete()

    def test_variables_value_set_undefined_profile(
        self,
        default_node: tp.Dict[str, tp.Any],
    ):
        profile_1 = models.Profile(
            name="p1",
            profile_type=infra_c.ProfileType.GLOBAL.value,
            active=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        profile_1.insert()
        profile_1.activate()

        profile_2 = models.Profile(
            name="p2",
            profile_type=infra_c.ProfileType.GLOBAL.value,
            active=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        profile_2.insert()

        variable = models.Variable(
            name="var_profile",
            project_id=c.SERVICE_PROJECT_ID,
            status=ua_c.InstanceStatus.NEW.value,
            setter=models.ProfileVariableSetter(
                profiles=[
                    {"profile": profile_1.uuid, "value": "value_1"},
                ],
            ),
        )
        variable.insert()

        self._service._iteration()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "ACTIVE"
        assert updated.value == "value_1"

        profile_2.activate()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "ACTIVE"
        assert updated.value == "value_1"

        variable.delete()
        profile_2.delete()
        profile_1.delete()

    def test_variables_selector_strategy_latest(
        self,
        default_node: tp.Dict[str, tp.Any],
    ):
        variable = models.Variable(
            name="var_selector",
            project_id=c.SERVICE_PROJECT_ID,
            status=ua_c.InstanceStatus.NEW.value,
            setter=models.SelectorVariableSetter(
                kind="selector",
                selector_strategy="latest",
            ),
        )
        variable.insert()

        value_1 = models.Value(
            variable=variable,
            value="v1",
            manual_selected=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        value_1.insert()

        value_2 = models.Value(
            variable=variable,
            value="v2",
            manual_selected=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        value_2.insert()

        self._service._iteration()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "ACTIVE"
        assert updated.value == "v2"

        value_2.delete()
        value_1.delete()
        variable.delete()

    def test_variables_selector_recalculate_on_delete(
        self,
        default_node: tp.Dict[str, tp.Any],
    ):
        variable = models.Variable(
            name="var_selector",
            project_id=c.SERVICE_PROJECT_ID,
            status=ua_c.InstanceStatus.NEW.value,
            setter=models.SelectorVariableSetter(
                kind="selector",
                selector_strategy="latest",
            ),
        )
        variable.insert()

        value_1 = models.Value(
            variable=variable,
            value="v1",
            manual_selected=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        value_1.insert()

        value_2 = models.Value(
            variable=variable,
            value="v2",
            manual_selected=False,
            project_id=c.SERVICE_PROJECT_ID,
        )
        value_2.insert()

        self._service._iteration()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "ACTIVE"
        assert updated.value == "v2"

        value_2.delete()

        self._service._iteration()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "ACTIVE"
        assert updated.value == "v1"

        value_1.delete()
        variable.delete()

    def test_variables_undefined_no_value_field_in_ua_resource(
        self,
        default_node: tp.Dict[str, tp.Any],
    ):
        variable = models.Variable(
            name="var_selector",
            project_id=c.SERVICE_PROJECT_ID,
            status=ua_c.InstanceStatus.NEW.value,
            setter=models.SelectorVariableSetter(
                kind="selector",
                selector_strategy="latest",
            ),
        )
        variable.insert()

        self._service._iteration()

        updated = models.Variable.objects.get_one(
            filters={"uuid": dm_filters.EQ(variable.uuid)}
        )
        assert updated.status == "IN_PROGRESS"
        assert updated.value is None

        variable.delete()
