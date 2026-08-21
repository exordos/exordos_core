#    Copyright 2025-2026 Genesis Corporation.
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

import itertools
import json
import typing as tp
import uuid as sys_uuid

from gcl_sdk.infra import constants as infra_c
from gcl_sdk.infra import exceptions as infra_exc
from gcl_sdk.infra.dm import models as infra_models
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import engines
from restalchemy.storage.sql import orm


class Profile(
    infra_models.Profile,
    orm.SQLStorableMixin,
):
    __tablename__ = "vs_profiles"

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )

    def activate(self, session: tp.Any = None) -> None:
        if self.profile_type != infra_c.ProfileType.GLOBAL:
            raise ValueError("Only global profiles can be activated")

        global_profiles = Profile.objects.get_all(
            filters={
                "profile_type": dm_filters.EQ(infra_c.ProfileType.GLOBAL.value),
            },
            session=session,
        )

        for profile in global_profiles:
            profile.active = False
            profile.update(session=session)

        self.active = True
        self.update(session=session)

    @classmethod
    def global_profile(cls, session: tp.Any = None) -> "Profile":
        return cls.objects.get_one(
            filters={
                "profile_type": dm_filters.EQ(infra_c.ProfileType.GLOBAL.value),
                "active": dm_filters.EQ(True),
            },
            session=session,
        )

    def _validate_not_used(self, session: tp.Any = None) -> None:
        # FIXME(akremenetsky): Listing all variables
        # is not efficient but:
        # 1. Deleting a profile is not a frequent operation.
        # 2. It's not expected to have a lot of variables
        #    at the current stage of development and this part
        #    can be optimized quite easily using an additional
        #    table to keep binding between profiles and variables.
        #    For now, we keep it simple.
        expression = (
            "SELECT * FROM vs_variables vars  WHERE vars.setter->>'kind' = 'profile';"
        )

        if not session:
            engine = engines.engine_factory.get_engine()
            with engine.session_manager() as session:
                curs = session.execute(expression, tuple())
                resp = curs.fetchall()
        else:
            curs = session.execute(expression, tuple())
            resp = curs.fetchall()

        me = str(self.uuid)

        for p in itertools.chain.from_iterable(r["setter"]["profiles"] for r in resp):
            if p["profile"] == me:
                raise infra_exc.ProfileInUse(profile=self.uuid)

    def delete(self, session: tp.Any = None):
        # Check the profile is not used by any variable
        # before deleting it.
        self._validate_not_used(session=session)
        super().delete(session=session)


class ProfileVariableSetter(infra_models.ProfileVariableSetter):
    """The setter based on profiles.

    Example:
    $core.vs.variables:
      var_profile:
        name: "var_profile"
        project_id: "12345678-c625-4fee-81d5-f691897b8142"
        setter:
          kind: profile
          fallback_strategy: ignore
          profiles:
            - profile: "$core.vs.profiles.$default:uuid"
              value: 1
            - profile: "$core.vs.profiles.$develop:uuid"
              value: 1
            - profile: "$core.vs.profiles.$medium:uuid"
              value: 2
            - profile: "$core.vs.profiles.$custom:uuid"
              value: 3
    """

    def set_value(self, variable: "Variable") -> None:
        """Determine a value for the variable and set it.

        If the value cannot be determined, the method raises an exception.
        """
        profile: tp.Optional[Profile] = None

        # If the variable is binded to an element,
        # we check if the element exists and has profile
        if self.element is not None:
            # Use local import to avoid circular imports
            from exordos_core.elements.dm import models as em_models

            element = em_models.Element.objects.get_one_or_none(
                filters={
                    "uuid": dm_filters.EQ(self.element),
                },
            )
            # No element, it's not possible to determine the value
            if element is None:
                raise infra_exc.VariableCannotFindValue(variable=variable.uuid)

            # The profile is determined for the variable
            profile = element.profile

        # If the profile is not determined, use the global profile
        if not profile:
            profile = Profile.global_profile()

        # The profile is determined, find the value and set it
        for p in self.profiles:
            if p["profile"] == profile.uuid:
                variable.value = p["value"]
                return

        # The profile isn't in the list so ingore it
        if self.fallback_strategy == "ignore":
            return

        raise NotImplementedError


class SelectorVariableSetter(infra_models.SelectorVariableSetter):
    """The selector setter allowing to select a value for the variable.

    Example:
    $core.vs.variables:
      var_profile:
        name: "var_profile"
        project_id: "12345678-c625-4fee-81d5-f691897b8142"
        setter:
          kind: selector
          selector_strategy: latest

    """

    def _set_value_latest_strategy(
        self, variable: "Variable", values: tp.List["Value"]
    ) -> None:
        """Determine a value based on the `latest` strategy."""
        # If there is no manual selected value, select
        # the latest created value
        if not any(v.manual_selected for v in values):
            values = sorted(values, key=lambda v: v.created_at)
            value = values[-1]
            variable.value = value.value
            variable.update()
            return

        # Use the value selected by the user
        value = next(v for v in values if v.manual_selected)

        variable.value = value.value
        variable.update()

    def set_value(self, variable: "Variable") -> None:
        """Determine a value for the variable and set it.

        If the value cannot be determined, the method raises an exception.
        """
        values = Value.objects.get_all(
            filters={
                "variable": dm_filters.EQ(variable),
            },
        )

        # No values, the variable is undefined
        if not values:
            raise infra_exc.VariableCannotFindValue(variable=variable.uuid)

        # Only `latest` strategy is supported for now
        if self.selector_strategy == "latest":
            return self._set_value_latest_strategy(variable, values)

        raise infra_exc.VariableCannotFindValue(variable=variable.uuid)


class AnySimpleTypeValueFieldSQLMixin(orm.SQLStorableMixin):
    def insert(self, session=None) -> None:
        # The `value` field is JSONB in the database
        # so we need to serialize it
        origin_value = self.value
        try:
            self.value = json.dumps(self.value)
            return super().insert(session=session)
        finally:
            self.value = origin_value

    def update(self, session=None, force=False) -> None:
        # The `value` field is JSONB in the database
        # so we need to serialize it
        origin_value = self.value
        try:
            self.value = json.dumps(self.value)
            return super().update(session=session, force=force)
        finally:
            self.value = origin_value


class Variable(
    infra_models.Variable,
    AnySimpleTypeValueFieldSQLMixin,
):
    __tablename__ = "vs_variables"

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )

    setter = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(ProfileVariableSetter),
            types_dynamic.KindModelType(SelectorVariableSetter),
        ),
        required=True,
    )

    @property
    def selected_value(self) -> tp.Optional["Value"]:
        """Returns the selected value for the variable."""
        var_values = Value.objects.get_all(
            filters={"variable": dm_filters.EQ(self)},
        )

        for value in var_values:
            if value.manual_selected:
                return value

        return None

    def release_value(self):
        """Release the selected value for the variable."""
        var_values = Value.objects.get_all(
            filters={"variable": dm_filters.EQ(self)},
        )

        for value in var_values:
            value.manual_selected = False
            value.update()


class Value(
    infra_models.Value,
    models.ModelWithTags,
    AnySimpleTypeValueFieldSQLMixin,
):
    __tablename__ = "vs_values"

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )
    variable = relationships.relationship(Variable, prefetch=True)

    def insert(self, session: tp.Any = None) -> None:
        super().insert(session=session)

        # Notify the variable that a new value has been inserted
        if self.variable:
            self.variable.update(session=session, force=True)

    def update(self, session: tp.Any = None, force: bool = False) -> None:
        super().update(session=session, force=force)

        # Notify the variable that a new value has been inserted
        if self.variable:
            self.variable.update(session=session, force=True)

    def delete(self, session: tp.Any = None):
        super().delete(session=session)

        # Notify the variable that a value has been deleted
        if self.variable:
            self.variable.update(session=session, force=True)

    def select_me(self, variable: Variable, session: tp.Any = None) -> None:
        if self.variable != variable:
            raise ValueError("Value does not belong to the variable")

        var_values = Value.objects.get_all(
            filters={"variable": dm_filters.EQ(variable)},
            session=session,
        )

        for value in var_values:
            value.manual_selected = False
            value.update(session=session)

        self.manual_selected = True
        self.update(session=session)
