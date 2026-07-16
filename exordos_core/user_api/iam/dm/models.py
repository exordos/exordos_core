# Copyright 2025 Genesis Corporation
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

import base64
import datetime
import enum
import hashlib
import logging
import re
import secrets
import typing as tp
import urllib.parse
import uuid as sys_uuid

from gcl_iam import algorithms
from gcl_iam import exceptions as iam_e
from gcl_iam import tokens
from gcl_sdk.agents.universal.dm import models as ua_models
import pyotp
from restalchemy.common import contexts
from restalchemy.dm import filters as ra_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types as ra_types
from restalchemy.dm import types_dynamic as ra_types_dynamic
from restalchemy.storage.sql import orm

from exordos_core.common import constants as c
from exordos_core.common import utils as u
from exordos_core.events import payloads as event_payloads
from exordos_core.secret.dm import models as secret_models
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam import exceptions as iam_exceptions
from exordos_core.user_api.iam.clients import keycloak
from exordos_core.user_api.iam.dm import types

LOG = logging.getLogger(__name__)


class KindModelSelectorType(ra_types_dynamic.KindModelSelectorType):
    def get_kind_types(self):
        return [self._kind_type_map[k] for k in self._kind_type_map]


class ModelWithSecret(models.Model, models.CustomPropertiesMixin):
    __custom_properties__ = {
        "secret": ra_types.String(min_length=5, max_length=128),
    }

    salt = properties.property(
        ra_types.String(min_length=24, max_length=24),
        required=False,
    )

    secret_hash = properties.property(
        ra_types.String(min_length=128, max_length=128),
        required=True,
    )

    def __init__(self, secret, salt=None, **kwargs):
        salt = salt or self._generate_salt()

        # Check if this is a service account and set password to empty
        user_type = kwargs.get("type", iam_c.UserType.USER.value)
        if user_type == iam_c.UserType.SERVICE.value:
            secret = ""

        super().__init__(
            secret_hash=self._generate_hash(
                secret=secret,
                secret_salt=salt,
                global_salt=self._global_salt,
            ),
            salt=salt,
            **kwargs,
        )

    def _generate_salt(self, length=18):
        return base64.b64encode(secrets.token_bytes(length)).decode("utf-8")

    @property
    def _global_salt(self):
        ctx = contexts.get_context()
        storage = ctx.context_storage
        return storage.get(iam_c.STORAGE_KEY_IAM_GLOBAL_SALT)

    @classmethod
    def _generate_hash(cls, secret, secret_salt, global_salt):

        raw_secret_salt = base64.b64decode(secret_salt)
        raw_global_salt = base64.b64decode(global_salt)

        hashed = hashlib.pbkdf2_hmac(
            "sha512",
            secret.encode("utf-8"),
            raw_secret_salt + raw_global_salt,
            251685,  # count of iterations
        )

        return hashed.hex()

    def check_secret(self, secret):
        return self.secret_hash == self._generate_hash(
            secret=secret,
            secret_salt=self.salt,
            global_salt=self._global_salt,
        )

    def validate_secret(self, secret):
        if not self.check_secret(secret):
            raise iam_e.CredentialsAreInvalidError()

    @property
    def secret(self):
        return "*******"

    @secret.setter
    def secret(self, value):
        # Prohibit password setting for service accounts
        if self.type == iam_c.UserType.SERVICE.value:
            raise iam_exceptions.ServiceAccountPasswordChangeError()

        self.salt = self._generate_salt()
        self.secret_hash = self._generate_hash(
            secret=value,
            secret_salt=self.salt,
            global_salt=self._global_salt,
        )

    def change_secret_safe(self, old_secret, new_secret):
        self.validate_secret(old_secret)
        self.secret = new_secret
        self.save()


class ModelWithStatus(models.Model):
    STATUS = iam_c.Status

    status = properties.property(
        ra_types.Enum([s.value for s in iam_c.Status]),
        default=STATUS.NEW.value,
    )


class ModelWithAlwaysActiveStatus(models.Model):
    STATUS = iam_c.AlwaysActiveStatus

    status = properties.property(
        ra_types.Enum([s.value for s in iam_c.AlwaysActiveStatus]),
        default=STATUS.ACTIVE.value,
    )


class RolesInfo:
    def __init__(self, roles):
        super().__init__()
        self._roles = roles

    def get_response_body(self):
        return [role.get_storable_snapshot() for role in self._roles]


class IdpResponseType(str, enum.Enum):
    CODE = "code"

    @classmethod
    def list_response_types(cls):
        return [cls.CODE.value]


class IdpCallbackBase(ra_types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    def check(self, redirect_uri: str) -> bool:
        raise NotImplementedError()


class IdpCallbackUriKind(IdpCallbackBase):
    KIND = "callback_uri"

    callback = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )

    def check(self, redirect_uri: str) -> bool:
        return self.callback == redirect_uri


class IdpCallbackUriListKind(IdpCallbackBase):
    KIND = "callback_uri_list"

    callbacks = properties.property(
        ra_types.TypedList(ra_types.String(max_length=256)),
        required=True,
        default=list,
    )

    def check(self, redirect_uri: str) -> bool:
        return redirect_uri in self.callbacks


class IdpCallbackRegexpKind(IdpCallbackBase):
    KIND = "callback_regexp"

    pattern = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )

    def check(self, redirect_uri: str) -> bool:
        return re.fullmatch(self.pattern, redirect_uri) is not None


class AbstractUserSource(ra_types_dynamic.AbstractKindModel):
    def process_secret(self, user, secret):
        raise NotImplementedError()


class IamUserSource(AbstractUserSource):
    KIND = "IAM"

    def process_secret(self, user, secret):
        if not user.check_secret(secret):
            raise iam_e.CredentialsAreInvalidError()


class KeycloakUserSource(AbstractUserSource):
    KIND = "KEYCLOAK"

    endpoint = properties.property(
        ra_types.Url(),
        required=True,
    )
    realm = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )
    client_id = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )
    client_secret = properties.property(
        ra_types.String(max_length=512),
        required=True,
    )
    timeout = properties.property(
        ra_types.Integer(min_value=1, max_value=120),
        default=5,
    )

    def process_secret(self, user, secret):
        if user.check_secret(secret):
            return

        client = keycloak.KeycloakClient(
            endpoint=self.endpoint,
            timeout=self.timeout,
        )

        if not client.check_password(
            realm=self.realm,
            client_id=self.client_id,
            client_secret=self.client_secret,
            login=user.email,
            password=secret,
        ):
            raise iam_e.CredentialsAreInvalidError()

        user.secret = secret
        user.update()


class IamUserCustomProps(ra_types_dynamic.AbstractKindModel):
    KIND = "basic"

    role = properties.property(
        ra_types.String(min_length=0, max_length=255),
        default=None,
    )
    legal_entity = properties.property(
        ra_types.String(min_length=0, max_length=255),
        default=None,
    )
    employment_format = properties.property(
        ra_types.String(min_length=0, max_length=255),
        default=None,
    )
    hire_date = properties.property(
        ra_types.AllowNone(ra_types.UTCDateTimeZ()),
        default=None,
    )
    layoff_date = properties.property(
        ra_types.AllowNone(ra_types.UTCDateTimeZ()),
        default=None,
    )
    birth_date = properties.property(
        ra_types.AllowNone(ra_types.UTCDateTimeZ()),
        default=None,
    )
    other = properties.property(
        ra_types.Dict(),
        default=dict,
    )


class User(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ModelWithSecret,
    ModelWithAlwaysActiveStatus,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_users"

    name = properties.property(
        types.Username(min_length=1, max_length=128),
        required=True,
    )

    user_source = properties.property(
        KindModelSelectorType(
            ra_types_dynamic.KindModelType(IamUserSource),
            ra_types_dynamic.KindModelType(KeycloakUserSource),
        ),
        default=IamUserSource,
    )

    type = properties.property(
        ra_types.Enum([t.value for t in iam_c.UserType]),
        default=iam_c.UserType.USER.value,
    )

    first_name = properties.property(
        ra_types.AllowNone(types.Name(min_length=0, max_length=128)),
        default=None,
    )
    last_name = properties.property(
        ra_types.AllowNone(types.Name(min_length=0, max_length=128)),
        default=None,
    )
    surname = properties.property(
        types.Name(min_length=0, max_length=128),
        default="",
    )

    phone = properties.property(
        ra_types.String(min_length=0, max_length=15),
        default=None,
    )
    email = properties.property(
        types.Email(max_length=128),
        required=True,
    )
    email_verified = properties.property(
        ra_types.Boolean(),
        default=False,
    )
    # IamClient the user registered through; drives auto-provisioning
    # of the personal workspace on email confirmation.
    registration_client = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
    )
    confirmation_code = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
    )
    confirmation_code_made_at = properties.property(
        ra_types.AllowNone(ra_types.UTCDateTimeZ()),
        default=None,
    )
    otp_secret = properties.property(
        ra_types.String(max_length=128),
        default="",
    )

    otp_enabled = properties.property(
        ra_types.Boolean(),
        default=False,
    )

    custom_props = properties.property(
        ra_types.AllowNone(
            KindModelSelectorType(
                ra_types_dynamic.KindModelType(IamUserCustomProps),
            )
        ),
        default=None,
    )

    def get_response_body(self):
        return {
            "uuid": str(self.uuid),
            "name": self.name,  # deprecated, use "username"
            "username": self.name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "type": self.type,
        }

    @classmethod
    def me(cls, token_info=None):
        token_info = token_info or contexts.get_context().iam_context.token_info
        if token_info.token_type == "anon":
            user_info = contexts.get_context().iam_context.introspection_info()[
                "user_info"
            ]
            return User(
                uuid=sys_uuid.UUID(user_info["uuid"]),
                type="anon",
                name=user_info["name"],
                first_name=user_info["first_name"],
                last_name=user_info["last_name"],
                email=user_info["email"],
                secret="",
            )
        return User.objects.get_one(
            filters={"uuid": ra_filters.EQ(token_info.user_uuid)}
        )

    def make_newcomer(self):
        role_newcomer = Role.objects.get_one(
            filters={"uuid": ra_filters.EQ(c.NEWCOMER_ROLE_UUID)}
        )
        role_binding = RoleBinding(
            user=self,
            role=role_newcomer,
        )
        role_binding.save()
        return role_binding

    def provision_personal_workspace(self):
        org = Organization(name=self.name, description="Personal workspace")
        org.insert()
        OrganizationMember(
            organization=org,
            user=self,
            role=iam_c.OrganizationRole.OWNER.value,
        ).insert()
        project = Project(
            name="default",
            description="Default project",
            organization=org,
        )
        project.insert()
        project.add_owner(self)

    def get_my_roles(self):
        return RolesInfo(
            [
                role_binding.role
                for role_binding in RoleBinding.objects.get_all(
                    filters={"user": ra_filters.EQ(self)}
                )
            ]
        )

    def validate_otp(self, code):
        if not self.otp_enabled:
            raise iam_e.OTPNotEnabledError()
        if not code:
            return False
        totp = pyotp.TOTP(self.otp_secret)
        return totp.verify(str(code))

    def enable_otp(self, password):
        if self.otp_enabled:
            raise iam_e.OTPAlreadyEnabledError()

        self.process_secret(password)
        self.otp_secret = pyotp.random_base32()
        self.save()

    def activate_otp(self, code):
        if self.otp_enabled:
            raise iam_e.OTPAlreadyEnabledError()

        if not self.otp_secret:
            raise iam_e.OTPNotEnabledError()

        totp = pyotp.TOTP(self.otp_secret)

        if not totp.verify(str(code)):
            raise iam_e.OTPInvalidCodeError()

        self.otp_enabled = True
        self.save()

    def disable_otp(self, password):
        self.process_secret(password)
        self.otp_secret = ""
        self.otp_enabled = False
        self.save()

    def delete(self, session=None, **kwargs):
        u.remove_nested_dm(OrganizationMember, "user", self, session=session)
        u.remove_nested_dm(RoleBinding, "user", self, session=session)
        return super().delete(session=session, **kwargs)

    def send_registration_event(self, app_endpoint="http://localhost/"):
        ctx = contexts.get_context()
        event_client = ctx.context_storage.get(iam_c.STORAGE_KEY_EVENTS_CLIENT)

        registration_event_payload = event_payloads.RegistrationEventPayload(
            site_endpoint=app_endpoint,
            confirmation_code=self.confirmation_code,
        )

        event_client.send_event(
            event=event_client.build_user_event(
                context=ctx,
                user=self,
                payload=registration_event_payload,
            ),
        )

    def send_reset_password_event(self, app_endpoint="http://localhost/"):
        ctx = contexts.get_context()
        event_client = ctx.context_storage.get(iam_c.STORAGE_KEY_EVENTS_CLIENT)

        self.create_confirmation_code()

        registration_event_payload = event_payloads.ResetPasswordEventPayload(
            site_endpoint=app_endpoint,
            reset_code=self.confirmation_code,
        )

        event_client.send_event(
            event=event_client.build_user_event(
                context=ctx,
                user=self,
                payload=registration_event_payload,
            ),
        )

    def resend_confirmation_event(self, app_endpoint="http://localhost/"):
        self.create_confirmation_code()
        self.save()
        self.send_registration_event(app_endpoint=app_endpoint)

    def confirm_email(self):
        self.email_verified = True
        self.clear_confirmation_code()
        self.make_newcomer()
        self.save()
        return self

    def confirm_email_by_code(self, code):
        if self.check_confirmation_code(code):
            return self.confirm_email()
        raise iam_exceptions.CanNotConfirmUser(code=code)

    def create_confirmation_code(self):
        # Janitor service will call .clear_confirmation_code()
        # to set confirmation_code and confirmation_code_made_at to nulls,
        # hourly, for all expired codes.
        self.confirmation_code = self.confirmation_code or sys_uuid.uuid4()
        self.confirmation_code_made_at = datetime.datetime.now(datetime.timezone.utc)
        self.save()

    def check_confirmation_code(self, code):
        if not (code and self.confirmation_code and self.confirmation_code_made_at):
            return False

        now = datetime.datetime.now(datetime.timezone.utc)
        code_age = now - self.confirmation_code_made_at
        if code_age > iam_c.USER_CONFIRMATION_CODE_TTL:
            return False

        return str(self.confirmation_code) == str(code)

    def clear_confirmation_code(self):
        self.confirmation_code = None
        self.confirmation_code_made_at = None
        self.save()

    def reset_secret(self, new_secret):
        self.secret = new_secret
        self.clear_confirmation_code()
        self.save()

    def reset_secret_by_code(self, new_secret, code):
        if self.check_confirmation_code(code):
            return self.reset_secret(new_secret)
        raise iam_exceptions.CanNotConfirmUser(code=code)

    def process_secret(self, secret):
        self.user_source.process_secret(user=self, secret=secret)


class Role(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ModelWithAlwaysActiveStatus,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_roles"

    project_id = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
        read_only=True,
    )


class Permission(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ModelWithAlwaysActiveStatus,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_permissions"


class PermissionBinding(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_binding_permissions"

    project_id = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
        read_only=True,
    )
    role = relationships.relationship(
        Role,
        required=True,
    )

    permission = relationships.relationship(
        Permission,
        required=True,
    )


class Organization(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ModelWithAlwaysActiveStatus,
    ua_models.TargetResourceMixin,
    orm.SQLStorableWithJSONFieldsMixin,
):
    __tablename__ = "iam_organizations"
    __jsonfields__ = ["info"]

    info = properties.property(ra_types.Dict(), default=dict)

    @classmethod
    def list_my(cls):
        user = User.me()

        member_bindings = OrganizationMember.objects.get_all(
            filters={
                "user": ra_filters.EQ(user),
            }
        )

        return [m.organization for m in member_bindings]

    @classmethod
    def get_default(cls, user=None):
        for member in OrganizationMember.objects.get_all(
            filters={
                "role": ra_filters.EQ(iam_c.OrganizationRole.OWNER.value),
                "user": ra_filters.EQ(user or User.me()),
            },
            limit=1,
            order_by={"created_at": "asc"},
        ):
            return member.organization
        return None

    def are_i_owner(self):
        user = User.me()
        for member in OrganizationMember.objects.get_all(
            filters={
                "organization": ra_filters.EQ(self),
                "user": ra_filters.EQ(user),
                "role": ra_filters.EQ(iam_c.OrganizationRole.OWNER.value),
            },
            limit=1,
        ):
            return True

        return False

    def are_i_member(self):
        user = User.me()
        for member in OrganizationMember.objects.get_all(
            filters={
                "organization": ra_filters.EQ(self),
                "user": ra_filters.EQ(user),
            },
            limit=1,
        ):
            return True

        return False


class OrganizationMember(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_organization_members"

    organization = relationships.relationship(
        Organization,
        prefetch=True,
        required=True,
    )
    user = relationships.relationship(
        User,
        prefetch=True,
        required=True,
    )
    role = properties.property(
        ra_types.Enum([s.value for s in iam_c.OrganizationRole]),
        default=iam_c.OrganizationRole.MEMBER.value,
    )


class Project(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_projects"

    status = properties.property(
        ra_types.Enum([s.value for s in iam_c.Status]),
        default=iam_c.Status.ACTIVE.value,
    )

    organization = relationships.relationship(
        Organization,
        required=True,
        prefetch=True,
    )

    def add_owner(self, user):
        role_owner = Role.objects.get_one(
            filters={"uuid": ra_filters.EQ(c.OWNER_ROLE_UUID)}
        )
        role_binding = RoleBinding(
            user=user,
            role=role_owner,
            project=self,
        )
        role_binding.save()
        return role_binding

    @classmethod
    def get_default(cls, organization=None, user=None):
        user = user or User.me()
        org = organization or Organization.get_default(user=user)
        for role_binding in RoleBinding.objects.get_all(
            filters={
                "user": ra_filters.EQ(user),
            },
            order_by={"created_at": "asc"},
        ):
            if role_binding.project and role_binding.project.organization == org:
                return role_binding.project
        return None

    @classmethod
    def list_my(cls, filters=None):
        user = User.me()
        filters = filters or {}
        filters.update(
            {
                "user": ra_filters.EQ(user),
                "project": ra_filters.IsNot(None),
            }
        )
        role_bindings = RoleBinding.objects.get_all(
            filters=filters,
            order_by={"created_at": "asc"},
        )
        return [binding.project for binding in role_bindings]

    def delete(self, session=None):
        u.remove_nested_dm(RoleBinding, "project", self, session=session)
        return super().delete(session=session)


class PermissionFastView(
    models.ModelWithUUID,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_permissions_fast_view"

    permission = relationships.relationship(
        Permission,
        prefetch=True,
        required=True,
    )
    user = relationships.relationship(
        User,
        prefetch=True,
        required=False,
    )
    role = relationships.relationship(
        Role,
        prefetch=True,
        required=False,
    )
    project = relationships.relationship(
        Project,
        prefetch=True,
        required=False,
    )


class RoleBinding(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    ModelWithAlwaysActiveStatus,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_binding_roles"

    project = relationships.relationship(
        Project,
        prefetch=True,
        default=None,
    )
    user = relationships.relationship(
        User,
        prefetch=True,
        required=True,
    )
    role = relationships.relationship(
        Role,
        prefetch=True,
        required=True,
    )


class Introspection(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
):
    user = relationships.relationship(
        User,
        required=True,
    )
    project = relationships.relationship(
        Project,
        required=False,
    )
    otp_verified = properties.property(
        ra_types.Boolean(),
        default=False,
    )
    permissions = properties.property(
        ra_types.List(),
        default=list,
    )

    def get_response_body(self):
        return {
            "user_info": self.user.get_response_body(),
            "project_id": str(self.project.uuid) if self.project else None,
            "otp_verified": self.otp_verified,
            "permissions": [permission.name for permission in self.permissions],
        }


class MeInfo:
    def __init__(self):
        super().__init__()
        self._user = self.get_user()

    def get_user(self):
        return User.me()

    def get_response_body(self):
        skip_fields = [
            "otp_secret",
            "salt",
            "secret_hash",
            "secret",
            "confirmation_code",
            "confirmation_code_made_at",
            "user_source",
            "custom_props",
            "registration_client",
        ]
        user = self._user.get_storable_snapshot()
        for drop_field in skip_fields:
            user.pop(drop_field, None)

        # "name" field is deprecated and will be removed, use "username"
        user["username"] = user["name"]

        organizations = []
        for organization in Organization.list_my():
            organizations.append(organization.get_storable_snapshot())

        project = Token.my().project
        project_id = str(project.uuid) if project else None

        return {
            "user": user,
            "organization": organizations,
            "project_id": project_id,
        }


class Userinfo:
    def __init__(self):
        super().__init__()
        self._token = Token.my()

    def get_user(self):
        return User.me()

    def get_response_body(self):
        return self._token.extend_structure_by_scope({})


class HS256SignatureAlgorithm(ra_types_dynamic.AbstractKindModel):
    KIND = "HS256"

    secret_uuid = properties.property(
        ra_types.UUID(),
        required=True,
        default=sys_uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )

    previous_secret_uuid = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
    )

    @property
    def secret(self):
        return secret_models.Password.objects.get_one(
            filters={"uuid": ra_filters.EQ(str(self.secret_uuid))}
        )

    @property
    def previous_secret(self):
        if self.previous_secret_uuid is None:
            return None

        return secret_models.Password.objects.get_one(
            filters={"uuid": ra_filters.EQ(str(self.previous_secret_uuid))}
        )

    def update_secret_uuid(self, new_secret_uuid):
        self.previous_secret_uuid = self.secret_uuid
        self.secret_uuid = new_secret_uuid

    def update_secret(self, new_secret):
        self.update_secret_uuid(new_secret.uuid)


class RS256SignatureAlgorithm(ra_types_dynamic.AbstractKindModel):
    KIND = "RS256"

    secret_uuid = properties.property(
        ra_types.UUID(),
        required=True,
    )

    previous_secret_uuid = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
    )

    @property
    def secret(self):
        return secret_models.RSAKey.objects.get_one(
            filters={"uuid": ra_filters.EQ(str(self.secret_uuid))}
        )

    @property
    def previous_secret(self):
        if self.previous_secret_uuid is None:
            return None

        return secret_models.RSAKey.objects.get_one(
            filters={"uuid": ra_filters.EQ(str(self.previous_secret_uuid))}
        )

    def safe_update_secret_uuid(self, new_secret_uuid):
        self.previous_secret_uuid = self.secret_uuid
        self.secret_uuid = new_secret_uuid

    def force_update_secret_uuid(self, new_secret_uuid):
        self.secret_uuid = new_secret_uuid

    def safe_update_secret(self, new_secret):
        self.safe_update_secret_uuid(new_secret.uuid)

    def force_update_secret(self, new_secret):
        self.force_update_secret_uuid(new_secret.uuid)


class IamClient(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ModelWithSecret,
    ModelWithAlwaysActiveStatus,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_clients"

    project_id = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
        read_only=True,
    )
    client_id = properties.property(
        ra_types.String(max_length=64),
        required=True,
    )
    registration_auto_provision = properties.property(
        ra_types.Boolean(),
        default=True,
    )
    signature_algorithm = properties.property(
        KindModelSelectorType(
            ra_types_dynamic.KindModelType(HS256SignatureAlgorithm),
            ra_types_dynamic.KindModelType(RS256SignatureAlgorithm),
        ),
        default=HS256SignatureAlgorithm,
    )

    @classmethod
    def get_id_token_signing_alg_values_supported(cls) -> tp.List[str]:
        selector_type = cls.properties.properties[
            "signature_algorithm"
        ].get_property_type()
        return [t.kind for t in selector_type.get_kind_types()]

    def validate_client_creds(self, client_id, client_secret):
        if not client_id or not client_secret:
            raise iam_e.ClientAuthenticationError()

        if self.client_id != client_id or not self.check_secret(client_secret):
            raise iam_e.ClientAuthenticationError()

    def _get_token_by_password_and_smth(
        self,
        users_query,
        password,
        scope=iam_c.PARAM_SCOPE_DEFAULT,
        ttl=None,
        refresh_ttl=None,
        otp_code=None,
        root_endpoint=c.DEFAULT_ROOT_ENDPOINT,
        **kwargs,
    ):
        # Check if service account token is requested
        service_account_uuid = kwargs.get("service_account_uuid")
        if service_account_uuid:
            # Authenticate user first
            authenticated_user = self._authenticate_user_with_password(
                users_query, password, otp_code
            )

            return self._handle_service_account_token_request(
                authenticated_user=authenticated_user,
                service_account_uuid=service_account_uuid,
                scope=scope,
                ttl=ttl,
                refresh_ttl=refresh_ttl,
                root_endpoint=root_endpoint,
            )

        # Regular token flow
        authenticated_user = self._authenticate_user_with_password(
            users_query, password, otp_code
        )
        return self._create_token_for_user(
            user=authenticated_user,
            scope=scope,
            ttl=ttl,
            refresh_ttl=refresh_ttl,
            root_endpoint=root_endpoint,
            **kwargs,
        )

    def get_token_by_password(self, username, **kwargs):
        """
        Get auth token by username + password (default approach).
        If service_account_uuid is provided, returns token for service account instead.
        """
        users_query = User.objects.query(
            where_conditions="LOWER(name) = %s",
            where_values=(username.lower(),),
            limit=1,
        )

        return self._get_token_by_password_and_smth(users_query=users_query, **kwargs)

    def _authenticate_user(self, users_query, password, otp_code=None):
        """
        Authenticate a user from users_query with password and optional OTP
        Returns the authenticated user or None if authentication fails
        """
        for user in users_query:
            # Service accounts cannot authenticate with password
            if user.type == iam_c.UserType.SERVICE.value:
                raise iam_exceptions.ServiceAccountPasswordAuthError()

            user.process_secret(secret=password)
            if user.otp_enabled and not user.validate_otp(otp_code):
                raise iam_e.OTPInvalidCodeError()
            return user

        return None

    def _perform_security_hardening(self, password):
        """
        Perform security hardening for failed authentication attempts
        This prevents timing attacks and username enumeration
        """
        self._generate_hash(
            secret=password or "",
            secret_salt=iam_c.DUMMY_PBKDF2_SALT,
            global_salt=contexts.get_context().context_storage.get(
                iam_c.STORAGE_KEY_IAM_GLOBAL_SALT
            ),
        )

    def _authenticate_user_with_password(self, users_query, password, otp_code=None):
        """
        Authenticate a user from users_query with password and optional OTP
        Returns the authenticated user
        Raises appropriate exceptions if authentication fails
        """
        authenticated_user = self._authenticate_user(users_query, password, otp_code)
        if not authenticated_user:
            # Security: run dummy PBKDF2 to prevent timing attacks
            self._perform_security_hardening(password)
            raise iam_e.CredentialsAreInvalidError()

        return authenticated_user

    def _check_service_token_permission(self, user_role_bindings):
        """
        Check if user has service token creation permission
        """
        # Get the service token creation permission
        perm_uuid = Permission.objects.get_one(
            filters={"name": ra_filters.EQ("iam.service_token.create")}
        ).uuid

        # Check if any of user's roles has the service token creation permission
        return bool(
            PermissionBinding.objects.get_all(
                filters={
                    "role": ra_filters.In([rb.role for rb in user_role_bindings]),
                    "permission": ra_filters.EQ(perm_uuid),
                }
            )
        )

    def _handle_service_account_token_request(
        self,
        authenticated_user,
        service_account_uuid,
        scope,
        **kwargs,
    ):
        """
        Handle service account token request - validation and token creation only.
        Authentication should be done by caller.
        """
        # Extract project UUID from scope for permission check
        project_uuid = self._extract_project_from_scope(scope)
        if not project_uuid:
            raise iam_exceptions.ProjectScopeRequiredError()

        # Validate project UUID
        try:
            project_uuid = sys_uuid.UUID(project_uuid)
        except ValueError:
            raise iam_exceptions.CanNotCreateServiceToken()

        # Check if user has any role binding to the project
        user_role_bindings = RoleBinding.objects.get_all(
            filters={
                "user": ra_filters.EQ(authenticated_user.uuid),
                "project": ra_filters.EQ(project_uuid),
            }
        )

        if not user_role_bindings:
            raise iam_exceptions.CanNotCreateServiceToken()

        # Check service token permission
        if not self._check_service_token_permission(user_role_bindings):
            raise iam_exceptions.CanNotCreateServiceToken()

        # Get service account (filter by type to prevent data leakage)
        try:
            service_account = User.objects.get_one(
                filters={
                    "uuid": service_account_uuid,
                    "type": iam_c.UserType.SERVICE.value,
                }
            )
        except iam_e.ObjectNotFound:
            # User either doesn't exist or is not a service account
            raise iam_e.UserNotFound(uuid=service_account_uuid)

        # Check if service account also has access to the same project
        service_role_bindings = RoleBinding.objects.get_all(
            filters={
                "user": ra_filters.EQ(service_account.uuid),
                "project": ra_filters.EQ(project_uuid),
            }
        )

        if not service_role_bindings:
            raise iam_exceptions.CanNotCreateServiceToken()

        # Explicit override of project scope for service token
        filtered_scope = f"project:{project_uuid}"

        # Create token for service account
        token_kwargs = kwargs.copy()
        token_kwargs.pop("scope", None)

        return self._create_token_for_user(
            user=service_account,
            scope=filtered_scope,
            **token_kwargs,
        )

    def _create_token_for_user(
        self,
        user,
        scope=iam_c.PARAM_SCOPE_DEFAULT,
        ttl=None,
        refresh_ttl=None,
        root_endpoint=c.DEFAULT_ROOT_ENDPOINT,
        **kwargs,
    ):
        """
        Create token for specified user with given parameters
        """

        def _calculate_delta(value, default_func):
            return (
                datetime.timedelta(seconds=float(value))
                if value is not None
                else default_func()
            )

        expiration_delta = _calculate_delta(ttl, Token.get_default_expiration_delta)
        refresh_expiration_delta = _calculate_delta(
            refresh_ttl, Token.get_default_refresh_expiration_delta
        )

        token = Token(
            user=user,
            iam_client=self,
            scope=scope,
            issuer=f"{root_endpoint}iam/clients/{self.uuid}",
            audience=self.client_id,
            expiration_delta=expiration_delta,
            refresh_expiration_delta=refresh_expiration_delta,
            **kwargs,
        )
        token.insert()
        return token

    def get_token_by_password_username(self, username, **kwargs):
        """
        Get auth token by username + password.
        This method supports service account tokens via service_account_uuid parameter.
        """
        # Create users query
        users_query = User.objects.query(
            where_conditions="LOWER(name) = %s",
            where_values=(username.lower(),),
            limit=1,
        )

        return self._get_token_by_password_and_smth(users_query=users_query, **kwargs)

    def get_token_by_password_email(self, email, **kwargs):
        """
        Get auth token by email + password.
        This method supports service account tokens via service_account_uuid parameter.
        """
        # Create users query
        users_query = User.objects.get_all(filters={"email": ra_filters.EQ(email)})

        return self._get_token_by_password_and_smth(users_query=users_query, **kwargs)

    def get_token_by_password_phone(self, phone, **kwargs):
        """
        Get auth token by phone + password.
        Will be added later.
        """
        raise NotImplementedError()

    def get_token_by_password_login(self, login, **kwargs):
        """
        Get auth token by any login field + password.
        Dynamic "smart" lookup is done by one of these fields:
         - by email (if it has "@")
         - by username (if no "@")
         - by phone [to be done later]
        This method supports service account tokens via service_account_uuid parameter.
        """
        if "@" in login:
            return self.get_token_by_password_email(login, **kwargs)
        else:
            return self.get_token_by_password_username(login, **kwargs)

    def _extract_project_from_scope(self, scope):
        """
        Extract the single project UUID from scope string.
        Returns None if no project found.
        Raises ValueError if multiple projects found in scope.
        """
        if not scope:
            return None

        project_parts = []
        for part in scope.split(" "):
            if part.startswith("project:"):
                project_info = part.split(":", 1)
                if len(project_info) > 1:
                    project_parts.append(project_info[1])

        if len(project_parts) == 0:
            return None
        elif len(project_parts) == 1:
            return project_parts[0]
        else:
            raise ValueError(f"Multiple projects found in scope: {project_parts}")

    def get_token_by_refresh_token(self, refresh_token, scope=None):
        algorithm = self.get_token_algorithm()
        refresh_token_info = tokens.RefreshToken(
            token=refresh_token,
            algorithm=algorithm,
            ignore_audience=True,
            ignore_expiration=True,
        )
        for token in Token.objects.get_all(
            filters={"refresh_token_uuid": ra_filters.EQ(refresh_token_info.uuid)}
        ):
            token.validate_refresh_expiration()
            token.refresh(scope=scope)
            return token
        else:
            raise iam_e.InvalidRefreshTokenError()

    def get_token_by_authorization_code(self, code, redirect_uri):
        for auth_info in IdpAuthorizationInfo.objects.get_all(
            filters={"code": ra_filters.EQ(code)}
        ):
            if auth_info.redirect_uri != redirect_uri:
                LOG.warning(
                    "IAM OIDC: authorization code redirect_uri mismatch "
                    "auth_info_uuid=%s stored_redirect_uri=%s requested_redirect_uri=%s",
                    auth_info.uuid,
                    auth_info.redirect_uri,
                    redirect_uri,
                )
            # TODO: Restore strict redirect_uri validation after URI normalization
            # and proxy handling are fixed.
            # if auth_info.redirect_uri == redirect_uri:
            #     auth_info.delete()
            #     return auth_info.token
            auth_info.delete()
            return auth_info.token

        raise iam_e.CredentialsAreInvalidError()

    def introspect(self):
        return Token.my().introspect()

    def me(self):
        return MeInfo()

    def userinfo(self):
        return Userinfo()

    def send_reset_password_event(self, email, app_endpoint="http://localhost/"):
        email = email.lower()
        # Result for non-existing email should not differ from existing one
        #  to mitigate with email enumeration.
        if user := User.objects.get_one_or_none(
            filters={"email": ra_filters.EQ(email)}
        ):
            user.send_reset_password_event(app_endpoint=app_endpoint)

    def get_token_algorithm(self):
        if self.signature_algorithm.kind == iam_c.ALGORITHM_HS256:
            secret = self.signature_algorithm.secret
            previous_secret = self.signature_algorithm.previous_secret

            return algorithms.HS256(
                key=secret.value,
                previous_key=(
                    None if previous_secret is None else previous_secret.value
                ),
            )

        if self.signature_algorithm.kind == iam_c.ALGORITHM_RS256:
            secret = self.signature_algorithm.secret
            previous_secret = self.signature_algorithm.previous_secret

            return algorithms.RS256(
                private_key=secret.private_key,
                public_key=secret.public_key,
                previous_public_key=(
                    None if previous_secret is None else previous_secret.public_key
                ),
            )

        raise ValueError(
            f"Unknown signature algorithm: {self.signature_algorithm.kind}"
        )

    def get_jwks(self):
        if self.signature_algorithm.kind == iam_c.ALGORITHM_HS256:
            ctx = contexts.get_context()
            storage = ctx.context_storage
            encryption_key = storage.get(
                iam_c.STORAGE_KEY_IAM_HS256_JWKS_ENCRYPTION_KEY
            )

            secret = self.signature_algorithm.secret
            previous_secret = self.signature_algorithm.previous_secret

            keys = []
            keys.append(
                {
                    "kty": "oct",
                    "alg": iam_c.ALGORITHM_HS256,
                    "use": "sig",
                    "kid": str(secret.uuid),
                    "k": algorithms.encrypt_hs256_jwks_secret(
                        secret=secret.value,
                        encryption_key=encryption_key,
                    ),
                }
            )

            if previous_secret is not None:
                keys.append(
                    {
                        "kty": "oct",
                        "alg": iam_c.ALGORITHM_HS256,
                        "use": "sig",
                        "kid": str(previous_secret.uuid),
                        "k": algorithms.encrypt_hs256_jwks_secret(
                            secret=previous_secret.value,
                            encryption_key=encryption_key,
                        ),
                    }
                )

            result = {"keys": keys}

        elif self.signature_algorithm.kind == iam_c.ALGORITHM_RS256:
            secret = self.signature_algorithm.secret
            previous_secret = self.signature_algorithm.previous_secret

            keys = []
            keys.append(
                algorithms.public_pem_to_jwk(
                    public_key_pem=secret.public_key,
                )
            )

            if previous_secret is not None:
                keys.append(
                    algorithms.public_pem_to_jwk(
                        public_key_pem=previous_secret.public_key,
                    )
                )

            result = {"keys": keys}

        else:
            raise ValueError(
                f"Unknown signature algorithm: {self.signature_algorithm.kind}"
            )

        result["algorithm"] = self.signature_algorithm.kind
        return result


class Token(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_tokens"

    @staticmethod
    def get_default_expiration_delta():
        return datetime.timedelta(minutes=60)

    @staticmethod
    def get_default_refresh_expiration_delta():
        return datetime.timedelta(days=1)

    expiration_delta = properties.property(
        ra_types.TimeDelta(),
        default=get_default_expiration_delta,
    )
    refresh_expiration_delta = properties.property(
        ra_types.TimeDelta(),
        default=get_default_refresh_expiration_delta,
    )

    user = relationships.relationship(
        User,
        prefetch=True,
        required=True,
    )
    iam_client = relationships.relationship(
        IamClient,
        prefetch=True,
        required=True,
    )
    project = relationships.relationship(
        Project,
        prefetch=True,
        required=False,
    )
    expiration_at = properties.property(
        ra_types.UTCDateTimeZ(),
        required=True,
    )
    refresh_expiration_at = properties.property(
        ra_types.UTCDateTimeZ(),
        required=True,
    )
    refresh_token_uuid = properties.property(
        ra_types.UUID(),
        default=sys_uuid.uuid4,
    )
    issuer = properties.property(
        ra_types.String(max_length=256),
        required=False,
    )
    audience = properties.property(
        ra_types.String(max_length=64),
        default="account",
    )
    typ = properties.property(
        ra_types.String(max_length=64),
        default="Bearer",
    )
    scope = properties.property(
        ra_types.String(max_length=128),
        default=iam_c.PARAM_SCOPE_DEFAULT,
    )
    nonce = properties.property(
        ra_types.String(max_length=256),
        default=None,
    )

    def __init__(self, user=None, scope="", project=None, **kwargs):
        user = user or User.me()
        now = datetime.datetime.now(datetime.timezone.utc)

        if "expiration_at" not in kwargs:
            expiration_delta = kwargs.get(
                "expiration_delta",
                self.get_default_expiration_delta(),
            )

            kwargs["expiration_at"] = now + expiration_delta

        if "refresh_expiration_at" not in kwargs:
            expiration_delta = kwargs.get(
                "refresh_expiration_delta",
                self.get_default_refresh_expiration_delta(),
            )

            kwargs["refresh_expiration_at"] = now + expiration_delta

        if project is None and scope:
            project = self._get_project_by_scope(user, scope)
        super().__init__(user=user, project=project, scope=scope, **kwargs)

    def check_refresh_expiration(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        return now < self.refresh_expiration_at

    def validate_refresh_expiration(self):
        if not self.check_refresh_expiration():
            raise iam_e.InvalidRefreshTokenError()

    def check_expiration(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        return now < self.expiration_at

    def validate_expiration(self):
        if not self.check_expiration():
            raise iam_e.InvalidAuthTokenError()

    def _get_default_project(self, user):
        return Project.get_default(user=user)

    def _get_project_by_uuid(self, user, str_uuid):
        for project in Project.objects.get_all(
            filters={"uuid": ra_filters.EQ(str_uuid)},
            limit=1,
        ):
            return project

    def extend_structure_by_scope(self, struct_dict):
        if self.nonce:
            struct_dict["nonce"] = self.nonce

        splitted_scope = self.scope.split(" ")

        if "openid" in splitted_scope:
            struct_dict["sub"] = str(self.user.uuid)

        if "profile" in splitted_scope:
            struct_dict["name"] = f" {self.user.first_name} {self.user.last_name}"
            struct_dict["given_name"] = self.user.first_name
            struct_dict["family_name"] = self.user.last_name
            struct_dict["middle_name"] = self.user.surname
            struct_dict["nickname"] = self.user.name
            struct_dict["preferred_username"] = self.user.name
            struct_dict["updated_at"] = int(self.user.updated_at.timestamp())

        if "email" in splitted_scope:
            struct_dict["email"] = self.user.email
            struct_dict["email_verified"] = self.user.email_verified

        return struct_dict

    def _get_project_by_scope(self, user, scope):
        scope = scope.lower()
        project = None
        for piece in scope.split(" "):
            if piece.startswith("project"):
                project_info = piece.split(":", 1)
                project = (
                    self._get_project_by_uuid(user, project_info[1])
                    if len(project_info) > 1 and project_info[1] != "default"
                    else self._get_default_project(user)
                )
                break

        return project

    def refresh(self, scope=None):
        scope = scope or self.scope
        now = datetime.datetime.now(datetime.timezone.utc)
        new_expiration_at = now + self.expiration_delta
        self.expiration_at = new_expiration_at
        self.project = self._get_project_by_scope(self.user, scope)
        self.scope = scope
        self.update()

    def has_permission(self, name: str) -> bool:
        for permission in Permission.objects.get_all(
            filters={"name": ra_filters.EQ(name)},
            limit=1,
        ):
            for _ in PermissionFastView.objects.get_all(
                filters={
                    "user": ra_filters.EQ(self.user),
                    "project": ra_filters.Is(self.project),
                    "permission": ra_filters.EQ(permission),
                },
                limit=1,
            ):
                return True
        return False

    def get_response_body(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        algorithm = self.iam_client.get_token_algorithm()

        access_token_info = {
            "exp": int(self.expiration_at.timestamp()),
            "iat": int(self.created_at.timestamp()),
            "auth_time": int(self.created_at.timestamp()),
            "jti": str(self.uuid),
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(self.user.uuid),
            "typ": self.typ,
            "otp": self.user.otp_enabled,
        }
        access_token = algorithm.encode(access_token_info)

        id_token_info = {
            "exp": int(self.expiration_at.timestamp()),
            "iat": int(self.created_at.timestamp()),
            "auth_time": int(self.created_at.timestamp()),
            "jti": str(self.uuid),
            "iss": self.issuer,
            "aud": self.audience,
            # "acr": "gold",
            # "amr": ["pwd", "mfa"],
            "project_id": str(self.project.uuid) if self.project else None,
        }

        id_token_info = self.extend_structure_by_scope(id_token_info)

        id_token = algorithm.encode(id_token_info)

        refresh_token_info = {
            "exp": int(self.refresh_expiration_at.timestamp()),
            "iat": int(self.created_at.timestamp()),
            "jti": str(self.refresh_token_uuid),
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(self.user.uuid),
        }

        refresh_token = algorithm.encode(refresh_token_info)

        return {
            "access_token": access_token,
            "token_type": self.typ,
            "expires_at": int(self.expiration_at.timestamp()),
            "expires_in": (self.expiration_at - now).seconds,
            "id_token": id_token,
            "refresh_token": refresh_token,
            "refresh_expires_in": (self.refresh_expiration_at - now).seconds,
            "scope": self.scope,
        }

    @classmethod
    def my(cls, token_info=None):
        token_info = token_info or contexts.get_context().iam_context.token_info
        for token in Token.objects.get_all(
            filters={"uuid": ra_filters.EQ(token_info.uuid)},
            limit=1,
        ):
            return token
        raise iam_e.InvalidAuthTokenError()

    def introspect(self, token_info=None, otp_code=None):
        user = User.me(token_info=token_info)

        values = PermissionFastView.objects.get_all(
            filters={
                "user": ra_filters.EQ(user),
                "project": ra_filters.Is(self.project),
            }
        )

        otp_verified = False
        if otp_code is not None:
            if not user.validate_otp(otp_code):
                raise iam_e.OTPInvalidCodeError()
            otp_verified = True

        return Introspection(
            user=self.user,
            project=self.project,
            permissions=[v.permission for v in values],
            otp_verified=otp_verified,
        )


class Idp(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    ModelWithAlwaysActiveStatus,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_idp"

    NONCE_DEFAULT = ""

    project_id = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
        read_only=True,
    )
    iam_client = relationships.relationship(
        IamClient,
        prefetch=True,
        required=True,
    )
    scope = properties.property(
        ra_types.String(max_length=64),
        default="openid",
    )
    callback = properties.property(
        KindModelSelectorType(
            ra_types_dynamic.KindModelType(IdpCallbackUriKind),
            ra_types_dynamic.KindModelType(IdpCallbackUriListKind),
            ra_types_dynamic.KindModelType(IdpCallbackRegexpKind),
        ),
        required=True,
    )
    nonce_required = properties.property(
        ra_types.Boolean(),
        default=True,
    )

    @property
    def client_id(self):
        return self.iam_client.client_id

    @property
    def client_secret(self):
        return self.iam_client.secret

    @property
    def well_known_endpoint(self):
        ctx = contexts.get_context()
        app_url = ctx.get_real_url_with_prefix()
        return f"{app_url}/v1/iam/idp/{self.uuid}/.well-known/openid-configuration"

    def get_wellknown_info(self):
        ctx = contexts.get_context()
        app_url = ctx.get_real_url_with_prefix()

        return {
            "issuer": (f"{app_url}/v1/iam/clients/{self.iam_client.uuid}"),
            "authorization_endpoint": (
                f"{app_url}/v1/iam/idp/{self.uuid}/actions/authorize/invoke"
            ),
            "token_endpoint": (
                f"{app_url}/v1/iam/clients/{self.iam_client.uuid}"
                "/actions/get_token/invoke"
            ),
            "userinfo_endpoint": (
                f"{app_url}/v1/iam/clients/{self.iam_client.uuid}/actions/userinfo"
            ),
            "jwks_uri": (
                f"{app_url}/v1/iam/clients/{self.iam_client.uuid}/actions/jwks"
            ),
            "response_types_supported": IdpResponseType.list_response_types(),
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": (
                self.iam_client.get_id_token_signing_alg_values_supported()
            ),
            "scopes_supported": ["openid", "profile", "email"],
            "claims_supported": ["preferred_username", "sub", "iss", "name", "email"],
            "end_session_endpoint": (
                f"{app_url}/v1/iam/clients/{self.iam_client.uuid}/actions/logout/invoke"
            ),
        }

    def authorize(
        self,
        client_id,
        redirect_uri,
        state,
        response_type,
        scope,
        nonce=NONCE_DEFAULT,
    ):
        if self.client_id != client_id:
            raise iam_exceptions.InvalidClientId(client_id=client_id)
        if not self.callback.check(redirect_uri):
            raise iam_exceptions.InvalidRedirectUri(redirect_uri=redirect_uri)
        if self.nonce_required and not nonce:
            raise iam_exceptions.InvalidNonce(nonce=nonce)

        ctx = contexts.get_context()
        app_url = ctx.get_real_url_with_prefix()

        auth_info = IdpAuthorizationInfo(
            idp=self,
            state=state,
            response_type=response_type,
            nonce=nonce,
            scope=scope,
            redirect_uri=redirect_uri,
        )

        auth_info.insert()

        return urllib.parse.urljoin(
            app_url,
            f"/?auth_uuid={auth_info.uuid}"
            f"&client_uuid={self.iam_client.uuid}"
            f"&idp_uuid={self.uuid}",
        )

    def construct_callback_uri(self, auth_info):
        if not auth_info.redirect_uri:
            raise iam_exceptions.InvalidRedirectUri(redirect_uri="")
        return (
            auth_info.redirect_uri + f"?code={auth_info.code}&state={auth_info.state}"
        )


class IdpAuthorizationInfo(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "iam_idp_authorization_info"

    idp = relationships.relationship(
        Idp,
        required=True,
    )
    state = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )
    response_type = properties.property(
        ra_types.Enum([s.value for s in IdpResponseType]),
        default=IdpResponseType.CODE.value,
    )
    nonce = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )
    redirect_uri = properties.property(
        ra_types.String(max_length=256),
        required=True,
        default="",
    )
    scope = properties.property(
        ra_types.String(max_length=256),
        required=True,
    )
    expiration_time_at = properties.property(
        ra_types.UTCDateTimeZ(),
        default=lambda: (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=10)
        ),
    )
    token = relationships.relationship(
        Token,
        prefetch=True,
        required=False,
    )

    code = properties.property(
        ra_types.UUID(),
        default=sys_uuid.uuid4,
    )

    def confirm(self):
        ctx = contexts.get_context()
        app_url = ctx.get_real_url_with_prefix()

        if self.idp.nonce_required and not self.nonce:
            raise iam_exceptions.InvalidNonce(nonce=self.nonce)
        else:
            nonce = self.nonce or Idp.NONCE_DEFAULT

        current_token = Token.my()
        self.token = Token(
            user=current_token.user,
            iam_client=self.idp.iam_client,
            scope=self.scope,
            project=current_token.project,
            nonce=nonce,
            audience=self.idp.iam_client.client_id,
            issuer=f"{app_url}/v1/iam/clients/{self.idp.iam_client.uuid}",
        )
        self.token.insert()
        self.update()

    def construct_callback_uri(self):
        return self.idp.construct_callback_uri(self)
