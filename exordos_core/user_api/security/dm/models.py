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

import abc
import enum
import json
import logging
import re

import altcha
import firebase_admin
from firebase_admin import app_check
from firebase_admin import credentials
from firebase_admin import exceptions as firebase_exceptions
from gcl_iam import exceptions as gcl_iam_exceptions
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types
from restalchemy.dm import types_dynamic as ra_types_dynamic
from restalchemy.storage.sql import orm

from exordos_core.user_api.iam import constants as iam_c

LOG = logging.getLogger(__name__)

# The http.HTTPMethod has all necessary methods, but it was added in 3.11
HTTP_METHODS = (
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "OPTIONS",
    "HEAD",
    "TRACE",
    "CONNECT",
)


class AbstractConditions(ra_types_dynamic.AbstractKindModel):
    @abc.abstractmethod
    def can_handle(self, context):
        raise NotImplementedError()


class UriConditions(AbstractConditions):
    KIND = "uri"

    uri = properties.property(
        ra_types.Uri(),
        required=True,
    )
    method = properties.property(
        ra_types.AllowNone(ra_types.Enum(HTTP_METHODS)),
        default=None,
    )

    def can_handle(self, context):
        request = context.request
        if self.method:
            if request.method.upper() != self.method:
                return False
        return request.path_info.lower() == self.uri.lower()


class UriRegexConditions(AbstractConditions):
    KIND = "uri_regex"

    uri_regex = properties.property(
        ra_types.String(),
        required=True,
    )
    method = properties.property(
        ra_types.AllowNone(ra_types.Enum(HTTP_METHODS)),
        default=None,
    )

    def can_handle(self, context):
        request = context.request
        if self.method:
            if request.method.upper() != self.method:
                return False
        return (
            re.match(
                self.uri_regex,
                request.path_info,
                flags=re.IGNORECASE,
            )
            is not None
        )


class AbstractVerifier(ra_types_dynamic.AbstractKindModel):
    @abc.abstractmethod
    def execute(self, context):
        raise NotImplementedError()


class FieldNotInRequestVerifier(AbstractVerifier):
    KIND = "no_fields"

    fields = properties.property(
        ra_types.TypedList(ra_types.String()),
        required=True,
    )

    def execute(self, context):
        payload = context.get_raw_payload()
        if not isinstance(payload, dict):
            return True
        json_keys = set(key.lower() for key in payload)
        return not any(field.lower() in json_keys for field in self.fields)


class FirebaseAppCheckVerifier(AbstractVerifier):
    """Verifier that checks Firebase App Check token in request headers."""

    KIND = "firebase_app_check"

    credentials_path = properties.property(
        ra_types.String(),
        required=True,
    )
    allowed_app_ids = properties.property(
        ra_types.TypedList(ra_types.String()),
        required=False,
        default=list,
    )

    FIREBASE_HEADERS = (
        "X-Firebase-AppCheck",
        "X-Goog-Firebase-AppCheck",
    )

    def _get_token_from_headers(self, request):
        for header_name in self.FIREBASE_HEADERS:
            token = request.headers.get(header_name)
            if token:
                return token
        return None

    def _get_firebase_app(self):
        """Return initialised default Firebase app, same semantics as original action."""
        try:
            return firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(self.credentials_path)
            return firebase_admin.initialize_app(cred)

    def execute(self, context):
        request = context.request

        token = self._get_token_from_headers(request)
        if not token:
            return False

        app = self._get_firebase_app()
        try:
            app_check_token = app_check.verify_token(token, app=app)
        except firebase_exceptions.FirebaseError as exc:
            LOG.warning("Firebase App Check token verification failed: %s", exc)
            return False
        except ValueError:
            LOG.exception("Firebase App Check token ValueError:")
            return False

        if self.allowed_app_ids:
            allowed_ids = set(self.allowed_app_ids)
            app_id = app_check_token.get("app_id")
            if app_id not in allowed_ids:
                LOG.warning(
                    "Firebase App Check token app_id '%s' not in allowed list.",
                    app_id,
                )
                return False

        return True


class CaptchaVerifier(AbstractVerifier):
    """Verifier that validates CAPTCHA solution from X-Captcha header."""

    KIND = "captcha"

    hmac_key = properties.property(
        ra_types.String(),
        required=True,
    )

    CAPTCHA_HEADER = "X-Captcha"

    def execute(self, context):
        request = context.request
        captcha_header = request.headers.get(self.CAPTCHA_HEADER)
        if not captcha_header:
            return False

        try:
            payload = json.loads(captcha_header)
        except (TypeError, json.JSONDecodeError):
            LOG.exception("Failed to parse CAPTCHA payload.")
            return False

        verified, _error = altcha.verify_solution(
            payload,
            hmac_key=self.hmac_key,
            check_expires=True,
        )
        return bool(verified)


class AdminBypassVerifier(AbstractVerifier):
    """Verifier that allows admin / trusted users to bypass other checks."""

    KIND = "admin_bypass"

    bypass_users = properties.property(
        ra_types.TypedList(ra_types.String()),
        required=True,
        default=list,
    )

    def execute(self, context):
        """Return True if current user is explicitly allowed to bypass."""
        # Introspection info may be missing for unauthenticated requests
        try:
            info = context.iam_context.get_introspection_info()
        except gcl_iam_exceptions.NoIamSessionStored:
            return False

        user_info = getattr(info, "user_info", None)
        if user_info is None:
            return False

        allowed = {v.lower() for v in self.bypass_users}

        email = getattr(user_info, "email", None)
        if email and str(email).lower() in allowed:
            return True

        uuid_val = getattr(user_info, "uuid", None)
        if uuid_val and str(uuid_val).lower() in allowed:
            return True

        return False


class GrantPermissionAction(AbstractVerifier):
    """Verifier that grants a specific IAM permission when the Rule matches.

    This action enables "street" registration - allowing user creation
    without explicit iam.user.create permission when this Rule is active.
    It always returns True (the action itself is a pass-through), but
    the Rule.execute() will record the granted permission in the context
    so that enforce() checks succeed for anonymous users.
    """

    KIND = "grant_permission"

    permission = properties.property(
        ra_types.String(),
        required=True,
    )

    def execute(self, context):
        """Return True for all requests.

        The Rule.execute() will record the granted permission in the
        context only when this action is part of a Rule that fully passes.
        """
        return True


class OperatorEnum(str, enum.Enum):
    OR = "OR"
    AND = "AND"


class ModelWithAlwaysActiveStatus(ra_models.Model):
    STATUS = iam_c.AlwaysActiveStatus

    status = properties.property(
        ra_types.Enum([s.value for s in iam_c.AlwaysActiveStatus]),
        default=STATUS.ACTIVE.value,
    )


class Rule(
    ra_models.ModelWithUUID,
    ra_models.ModelWithNameDesc,
    ra_models.ModelWithTimestamp,
    ModelWithAlwaysActiveStatus,
    orm.SQLStorableMixin,
):
    __tablename__ = "security_rules"

    condition = properties.property(
        ra_types_dynamic.KindModelSelectorType(
            ra_types_dynamic.KindModelType(UriConditions),
            ra_types_dynamic.KindModelType(UriRegexConditions),
        ),
        required=True,
    )
    action = properties.property(
        ra_types_dynamic.KindModelSelectorType(
            ra_types_dynamic.KindModelType(FieldNotInRequestVerifier),
            ra_types_dynamic.KindModelType(FirebaseAppCheckVerifier),
            ra_types_dynamic.KindModelType(CaptchaVerifier),
            ra_types_dynamic.KindModelType(AdminBypassVerifier),
            ra_types_dynamic.KindModelType(GrantPermissionAction),
        ),
        required=True,
    )
    operator = properties.property(
        ra_types.Enum([s.value for s in OperatorEnum]),
        default=OperatorEnum.OR.value,
    )
    project_id = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
        read_only=True,
    )

    def can_handle(self, context):
        return self.condition.can_handle(context)

    def execute(self, context):
        result = self.action.execute(context)
        # This is called by Rule.execute() when a GrantPermissionAction, record
        # the granted permission so that enforce() can check it later
        if result and isinstance(self.action, GrantPermissionAction):
            context.add_granted_permission(self.action.permission)
        return result
