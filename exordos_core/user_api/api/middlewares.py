# Copyright 2026 Genesis Corporation
#
# All Rights Reserved.
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

import dataclasses

from gcl_iam import exceptions as gcl_iam_exceptions
from restalchemy.api import middlewares as ra_middlewares
from restalchemy.common import contexts as ra_contexts
from restalchemy.dm import filters as ra_filters

from exordos_core.user_api.security import exceptions as security_exceptions
from exordos_core.user_api.security.dm import models as security_models


@dataclasses.dataclass
class RulesContext:
    and_rules: list
    or_rules: list

    @property
    def available(self) -> bool:
        return bool(self.and_rules or self.or_rules)


class SecurityRulesMiddleware(ra_middlewares.Middleware):
    def process_request(self, req):
        context = ra_contexts.get_context()
        rules_context = self._prepare_rules(context)
        if self._verify_rules(context, rules_context):
            return None
        self._raise_error_answer()

    def _prepare_rules(self, context):
        try:
            project_id = context.iam_context.get_introspection_info().project_id
        except gcl_iam_exceptions.NoIamSessionStored:
            project_id = None

        if project_id is None:
            filters = {"project_id": ra_filters.Is(project_id)}
        else:
            filters = {"project_id": ra_filters.EQ(project_id)}

        rules = security_models.Rule.objects.get_all(filters=filters)

        available_rules = [rule for rule in rules if rule.can_handle(context)]
        and_rules = [
            rule
            for rule in available_rules
            if rule.operator == security_models.OperatorEnum.AND.value
        ]
        or_rules = [
            rule
            for rule in available_rules
            if rule.operator == security_models.OperatorEnum.OR.value
        ]
        return RulesContext(
            and_rules=and_rules,
            or_rules=or_rules,
        )

    def _verify_rules(self, context, rules_context):
        if not rules_context.available:
            return True
        if rules_context.and_rules and all(
            rule.execute(context) for rule in rules_context.and_rules
        ):
            return True
        if rules_context.or_rules:
            return any(rule.execute(context) for rule in rules_context.or_rules)

        return False

    def _raise_error_answer(self):
        raise security_exceptions.ActionNotAllowed()
