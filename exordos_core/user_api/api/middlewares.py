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
import urllib.parse
import uuid as sys_uuid

from gcl_iam import exceptions as gcl_iam_exceptions
from gcl_iam import rules
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


REPO_UPLOAD_AUTH_PATH = "/v1/repo/upload_auth"


class RepoUploadAuthMiddleware(ra_middlewares.Middleware):
    """Answer the nginx `auth_request` subrequests of an element repository.

    An LB route serving a writable `local_dir` at `<prefix>/<project_id>/`
    asks `/v1/repo/upload_auth<prefix>` about every request, passing the
    original method and URI in `X-Original-Method` / `X-Original-URI` and
    the caller's `Authorization`. The subrequest keeps the original method,
    so this is a middleware rather than a resource route.

    Reads are open: hypervisors and the repo proxy fetch without a token.
    A write needs a token scoped to the project the path names and the
    `repo.repository.upload` permission. Anything else is refused.
    """

    READ_METHODS = frozenset(("GET", "HEAD"))
    WRITE_METHODS = frozenset(("PUT", "DELETE"))

    def process_request(self, req):
        path = req.path
        if path != REPO_UPLOAD_AUTH_PATH and not path.startswith(
            REPO_UPLOAD_AUTH_PATH + "/"
        ):
            return None
        prefix = path[len(REPO_UPLOAD_AUTH_PATH) :]
        return req.ResponseClass(status=self._decide(req, prefix))

    @staticmethod
    def _path_project(uri, prefix):
        """Return the project a request URI writes into, or None.

        nginx serves the percent-decoded, normalized URI but passes the raw
        one here, so any `.`/`..`/empty segment or backslash is refused
        rather than resolved: what is checked must be what gets written.
        """
        path = urllib.parse.unquote(uri.split("?", 1)[0])
        prefix = prefix.rstrip("/") + "/"
        if "\\" in path or not path.startswith(prefix):
            return None
        segments = path[len(prefix) :].split("/")
        if any(s in ("", ".", "..") for s in segments[:-1]) or segments[-1] in (
            ".",
            "..",
        ):
            return None
        try:
            return sys_uuid.UUID(segments[0])
        except ValueError:
            return None

    def _decide(self, req, prefix):
        method = req.headers.get("X-Original-Method", "")
        if method in self.READ_METHODS:
            return 204
        if method not in self.WRITE_METHODS:
            return 403

        context = ra_contexts.get_context()
        project_id = context.iam_context.get_introspection_info().project_id
        if project_id is None:
            return 401

        target = self._path_project(req.headers.get("X-Original-URI", ""), prefix)
        if target is None or target != sys_uuid.UUID(str(project_id)):
            return 403

        allowed = context.iam_context.enforcer.enforce(
            rules.Rule("repo", "repository", "upload"), do_raise=False
        )
        return 204 if allowed else 403
