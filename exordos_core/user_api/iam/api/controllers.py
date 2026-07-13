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

import errno
import logging
import mimetypes
from os import path as os_path
import re
import string
import typing as tp
from urllib import parse as urllib_parse

from authlib.integrations import requests_client
from gcl_iam import rules
from gcl_iam.api import controllers as iam_controllers
from gcl_iam.api import field_perms as iam_fp
import jinja2
import pyotp
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import contexts as ra_a_contexts
from restalchemy.api import controllers
from restalchemy.api import resources
from restalchemy.common import contexts
from restalchemy.common import exceptions as ra_e
from restalchemy.common import utils as ra_utils
from restalchemy.dm import filters as ra_filters
from restalchemy.openapi import utils as oa_utils

from exordos_core.common import constants as common_c
from exordos_core.user_api.iam import constants as c
from exordos_core.user_api.iam import exceptions as iam_e
from exordos_core.user_api.iam.api import openapi_specs as oa_specs
from exordos_core.user_api.iam.clients import idp
from exordos_core.user_api.iam.dm import models

LOG = logging.getLogger(__name__)


class EnforceMixin:
    def enforce(self, rule, do_raise=False, exc=None):
        ctx = contexts.get_context()
        # Check if the permission was granted by a GrantPermissionAction Rule
        if ctx.has_granted_permission(str(rule)):
            return True
        return ctx.iam_context.enforcer.enforce(rule, do_raise, exc)


class ValidationException(ra_e.RestAlchemyException):
    code = 400
    message = "Validation Error. %(description)s"


class ValidateMixin:
    __validate_min_length__ = 8
    __validate_not_contain__: tp.List[str] = [string.whitespace]
    __validate_must_contain__: tp.List[str] = None  # [digits, punctuation]
    __validate_regex__: str = None

    def validate(self, value):
        error = None
        if value is None:
            error = "Value is required"
        elif self.__validate_min_length__ and len(value) < self.__validate_min_length__:
            error = (
                f"Value must be at least {self.__validate_min_length__} characters long"
            )
        elif self.__validate_not_contain__:
            value_set = set(value)
            for not_contain in self.__validate_not_contain__:
                if set(not_contain) & value_set:
                    error = f"Value must not contain {self.__validate_not_contain__}"
                    break
        elif self.__validate_must_contain__:
            value_set = set(value)
            for required in self.__validate_must_contain__:
                if not set(required) & value_set:
                    error = f"Value must contain one of {required}"
                    break
        elif self.__validate_regex__ and not re.match(self.__validate_regex__, value):
            error = f"Value must match regex {self.__validate_regex__}"
        if error:
            raise ValidationException(description=error)


class ValidateSecretMixin(ValidateMixin):
    def validate_secret(self, kwargs: dict):
        if "secret" in kwargs:
            self.validate(kwargs["secret"])


class IamController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/iam/"


def _get_app_endpoint(req):
    origin = req.headers.get("Origin")
    result = req.host_url

    if not origin:
        return result

    parsed_referer = urllib_parse.urlparse(origin)

    if parsed_referer.scheme not in ("http", "https") or not parsed_referer.netloc:
        return result

    return f"{parsed_referer.scheme}://{parsed_referer.netloc}"


class UserController(
    controllers.BaseResourceControllerPaginated,
    EnforceMixin,
    ValidateSecretMixin,
):
    __resource__ = resources.ResourceByModelWithCustomProps(
        models.User,
        convert_underscore=False,
        process_filters=True,
        fields_permissions=iam_fp.FieldsIamPermissions(
            default=iam_fp.Permissions.RW,
            fields={
                "salt": {ra_c.ALL: iam_fp.Permissions.HIDDEN},
                "user_source": {
                    ra_c.ALL: iam_fp.Permissions.HIDDEN,
                    ra_c.CREATE: iam_fp.Permissions.RW,
                },
                "secret": {
                    ra_c.ALL: iam_fp.Permissions.HIDDEN,
                    ra_c.CREATE: iam_fp.Permissions.RW,
                },
                "secret_hash": {ra_c.ALL: iam_fp.Permissions.HIDDEN},
                "otp_secret": {ra_c.ALL: iam_fp.Permissions.HIDDEN},
                "confirmation_code": {ra_c.ALL: iam_fp.Permissions.HIDDEN},
                "confirmation_code_made_at": {ra_c.ALL: iam_fp.Permissions.HIDDEN},
                "type": {
                    ra_c.ALL: iam_fp.Permissions.RO,
                    ra_c.CREATE: iam_fp.Permissions.RW,
                },
                "custom_props": {
                    ra_c.GET: rules.Rule("iam", "user_custom_props", "read"),
                    ra_c.FILTER: rules.Rule("iam", "user_custom_props", "list"),
                    ra_c.CREATE: rules.Rule("iam", "user_custom_props", "create"),
                    ra_c.UPDATE: rules.Rule("iam", "user_custom_props", "update"),
                    ra_c.DELETE: rules.Rule("iam", "user_custom_props", "delete"),
                },
                "email": {
                    ra_c.UPDATE: iam_fp.Permissions.HIDDEN,
                },
            },
        ),
        name_map={"secret": "password", "name": "username"},
    )

    def get(self, uuid, **kwargs):
        # When active_method is GET, this is a direct resource GET request
        # and we should enforce permissions. When active_method is None,
        # get() is called from get_resource_by_uuid() for an action, so
        # we skip the permission check — the action itself is responsible
        # for its own permission checks.
        try:
            active_method = self._req.api_context.get_active_method()
        except ra_a_contexts.CanNotGetActiveMethod:
            active_method = None

        if active_method == ra_c.GET:
            is_me = models.User.me().uuid == uuid
            if not is_me and not self.enforce(c.PERMISSION_USER_READ_ALL):
                raise iam_e.CanNotReadUser(uuid=uuid, rule=c.PERMISSION_USER_READ_ALL)

        return super().get(uuid, **kwargs)

    def create(self, **kwargs):
        self.enforce(
            c.PERMISSION_USER_CREATE,
            do_raise=True,
            exc=iam_e.CanNotCreateUser,
        )
        self.validate_secret(kwargs)
        kwargs.pop("email_verified", None)
        user = super().create(**kwargs)
        app_endpoint = _get_app_endpoint(req=self._req)
        user.resend_confirmation_event(app_endpoint=app_endpoint)
        ctx = self.get_context()
        LOG.info(
            "IAM AUDIT: user created name=%s uuid=%s ip=%s",
            user.name,
            user.uuid,
            ctx.get_user_ip(),
        )
        return user

    def filter(self, filters, **kwargs):
        self.enforce(
            c.PERMISSION_USER_LISTING, do_raise=True, exc=iam_e.CanNotListUsers
        )
        return super().filter(filters, **kwargs)

    def update(self, uuid, **kwargs):
        self.validate_secret(kwargs)
        kwargs.pop("email_verified", None)
        is_me = models.User.me().uuid == uuid
        if is_me or self.enforce(c.PERMISSION_USER_WRITE_ALL):
            return super().update(uuid, **kwargs)
        raise iam_e.CanNotUpdateUser(uuid=uuid, rule=c.PERMISSION_USER_WRITE_ALL)

    def delete(self, uuid):
        is_me = models.User.me().uuid == uuid
        if self.enforce(c.PERMISSION_USER_DELETE_ALL) or (
            is_me and self.enforce(c.PERMISSION_USER_DELETE)
        ):
            return super().delete(uuid)
        raise iam_e.CanNotDeleteUser(
            uuid=uuid,
            rule1=c.PERMISSION_USER_DELETE_ALL,
            rule2=c.PERMISSION_USER_DELETE,
        )

    @actions.post
    def change_password(self, resource, old_password, new_password):
        self.validate(new_password)
        is_me = models.User.me() == resource
        if is_me or self.enforce(c.PERMISSION_USER_WRITE_ALL):
            resource.change_secret_safe(
                old_secret=old_password,
                new_secret=new_password,
            )
            return resource
        raise iam_e.CanNotUpdateUser(
            uuid=resource.uuid, rule=c.PERMISSION_USER_WRITE_ALL
        )

    @actions.post
    def enable_otp(self, resource, password):
        is_me = models.User.me() == resource
        if is_me or self.enforce(c.PERMISSION_USER_WRITE_ALL):
            resource.enable_otp(
                password=password,
            )
            # TODO: get better issuer name?
            return {
                "otp_uri": pyotp.totp.TOTP(resource.otp_secret).provisioning_uri(
                    name=resource.email, issuer_name="Exordos IAM"
                )
            }

        raise iam_e.CanNotUpdateUser(
            uuid=resource.uuid, rule=c.PERMISSION_USER_WRITE_ALL
        )

    @actions.post
    def activate_otp(self, resource, code):
        is_me = models.User.me() == resource
        if is_me or self.enforce(c.PERMISSION_USER_WRITE_ALL):
            resource.activate_otp(
                code=code,
            )
            return resource

        raise iam_e.CanNotUpdateUser(
            uuid=resource.uuid, rule=c.PERMISSION_USER_WRITE_ALL
        )

    @actions.post
    def disable_otp(self, resource, password):
        # TODO: check token for OTP auth
        is_me = models.User.me() == resource
        if is_me or self.enforce(c.PERMISSION_USER_WRITE_ALL):
            resource.disable_otp(
                password=password,
            )
            return resource

        raise iam_e.CanNotUpdateUser(
            uuid=resource.uuid, rule=c.PERMISSION_USER_WRITE_ALL
        )

    @actions.post
    def resend_email_confirmation(self, resource):
        app_endpoint = _get_app_endpoint(req=self._req)
        resource.resend_confirmation_event(app_endpoint=app_endpoint)
        # Don't leak user data
        return None

    @actions.post
    def force_confirm_email(self, resource):
        rule = c.PERMISSION_USER_WRITE_ALL
        if not self.enforce(rule):
            raise iam_e.CanNotUpdateUser(uuid=resource.uuid, rule=rule)

        resource.confirm_email()
        return None

    @actions.post
    def confirm_email(self, resource, code=None):
        code = code or self._req.params.get("code", "")
        resource.confirm_email_by_code(code)
        return resource

    @oa_utils.extend_schema(**oa_specs.OA_SPEC_RESET_PASSWORD_USER)
    @actions.post
    def reset_password(self, resource, new_password=None, code=None):
        code = code or self._req.params.get("code")
        has_permission = self.enforce(c.PERMISSION_USER_RESET_PASSWORD)
        if not code and not has_permission:
            raise iam_e.CanNotResetUserPassword(
                uuid=resource.uuid,
                rule=c.PERMISSION_USER_RESET_PASSWORD,
            )
        new_secret = new_password or self._req.params.get("new_password")
        self.validate(new_secret)
        if code:
            resource.reset_secret_by_code(new_secret=new_secret, code=code)
        else:
            resource.reset_secret(new_secret=new_secret)
        return resource

    @actions.get
    def get_my_roles(self, resource):
        is_me = models.User.me() == resource
        if is_me or self.enforce(c.PERMISSION_USER_READ_ALL):
            return resource.get_my_roles().get_response_body()
        raise iam_e.CanNotReadUser(uuid=resource.uuid, rule=c.PERMISSION_USER_READ_ALL)


class OrganizationController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
    EnforceMixin,
):
    __resource__ = resources.ResourceByRAModel(
        models.Organization,
        convert_underscore=False,
    )
    __policy_service_name__ = "iam"
    __policy_name__ = "organization"

    def create(self, **kwargs):
        result = super().create(**kwargs)
        models.OrganizationMember(
            organization=result,
            user=models.User.me(),
            role=c.OrganizationRole.OWNER.value,
        ).insert()
        return result

    def filter(self, filters, **kwargs):
        pclass = iam_controllers.PolicyBasedWithoutProjectController
        if self.enforce(c.PERMISSION_ORGANIZATION_READ_ALL):
            return super(pclass, self).filter(filters, **kwargs)
        return models.Organization.list_my()

    def get(self, uuid, **kwargs):
        pclass = iam_controllers.PolicyBasedWithoutProjectController
        return super(pclass, self).get(uuid, **kwargs)

    def update(self, uuid, **kwargs):
        pclass = iam_controllers.PolicyBasedWithoutProjectController
        org = self.get(uuid)
        if org.are_i_owner() or self.enforce(c.PERMISSION_ORGANIZATION_WRITE_ALL):
            return super(pclass, self).update(uuid, **kwargs)
        raise iam_e.CanNotUpdateOrganization(name=org.name)

    def delete(self, uuid):
        pclass = iam_controllers.PolicyBasedWithoutProjectController
        org = self.get(uuid)
        if self.enforce(c.PERMISSION_ORGANIZATION_DELETE_ALL):
            return super(pclass, self).delete(uuid)
        if org.are_i_owner():
            return super().delete(uuid)
        raise iam_e.CanNotDeleteOrganization(name=org.name)


class OrganizationMemberController(
    controllers.BaseResourceControllerPaginated, EnforceMixin
):
    __resource__ = resources.ResourceByRAModel(
        models.OrganizationMember,
        convert_underscore=False,
    )

    def create(self, organization, **kwargs):
        if not (
            organization.are_i_owner()
            or self.enforce(c.PERMISSION_ORGANIZATION_WRITE_ALL)
        ):
            raise iam_e.CanNotUpdateOrganization(name=organization.name)

        return super().create(organization=organization, **kwargs)

    def update(self, uuid, **kwargs):
        member = super().get(uuid)
        organization = member.organization
        if organization.are_i_owner() or self.enforce(
            c.PERMISSION_ORGANIZATION_WRITE_ALL
        ):
            return super().update(uuid, **kwargs)
        raise iam_e.CanNotUpdateOrganization(name=organization.name)

    def delete(self, uuid):
        member = super().get(uuid)
        organization = member.organization
        if organization.are_i_owner() or self.enforce(
            c.PERMISSION_ORGANIZATION_WRITE_ALL
        ):
            return super().delete(uuid)
        raise iam_e.CanNotUpdateOrganization(name=organization.name)


class ProjectController(controllers.BaseResourceControllerPaginated, EnforceMixin):
    __resource__ = resources.ResourceByRAModel(
        models.Project,
        convert_underscore=False,
    )

    def create(self, organization, **kwargs):
        if not (
            organization.are_i_owner()
            or self.enforce(c.PERMISSION_PROJECT_WRITE_ALL)
            or self.enforce(c.PERMISSION_ORGANIZATION_WRITE_ALL)
        ):
            raise iam_e.CanNotCreateProjectInOrganization(uuid=organization.uuid)

        project = super().create(organization=organization, **kwargs)
        project.add_owner(models.User.me())
        return project

    def filter(self, filters, order_by=None):
        if self.enforce(c.PERMISSION_PROJECT_LIST_ALL):
            return super().filter(filters=filters, order_by=order_by)
        return models.Project.list_my()

    def get(self, uuid, **kwargs):
        project = super().get(uuid, **kwargs)
        filters = {"project": ra_filters.EQ(project)}
        for _ in models.Project.list_my(filters=filters):
            return project
        if self.enforce(c.PERMISSION_PROJECT_READ_ALL):
            return project
        raise iam_e.CanNotReadProject(
            uuid=project.uuid,
            rule=c.PERMISSION_PROJECT_READ_ALL,
        )

    def update(self, uuid, **kwargs):
        project = self.get(uuid)
        filters = {"project": ra_filters.EQ(project)}
        for _ in models.Project.list_my(filters=filters):
            return super().update(uuid, **kwargs)
        if self.enforce(c.PERMISSION_PROJECT_WRITE_ALL):
            return super().update(uuid, **kwargs)
        raise iam_e.CanNotUpdateProject(
            name=project.name,
            rule=c.PERMISSION_PROJECT_WRITE_ALL,
        )

    def delete(self, uuid):
        project = self.get(uuid)
        filters = {"project": ra_filters.EQ(project)}
        for _ in models.Project.list_my(filters=filters):
            return super().delete(uuid)
        if self.enforce(c.PERMISSION_PROJECT_DELETE_ALL):
            return super().delete(uuid)
        raise iam_e.CanNotDeleteProject(
            name=project.name,
            rule=c.PERMISSION_PROJECT_WRITE_ALL,
        )


class RoleController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    __resource__ = resources.ResourceByRAModel(
        models.Role,
        convert_underscore=False,
    )

    __policy_service_name__ = "iam"
    __policy_name__ = "role"


class RoleBindingController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    __resource__ = resources.ResourceByRAModel(
        models.RoleBinding,
        convert_underscore=False,
    )

    __policy_service_name__ = "iam"
    __policy_name__ = "role_binding"

    def create(self, **kwargs):
        role_binding = super().create(**kwargs)
        user = role_binding.user
        role = role_binding.role
        ctx = self.get_context()
        LOG.info(
            "IAM AUDIT: role granted user=%s user_uuid=%s role=%s role_uuid=%s ip=%s",
            user.name,
            user.uuid,
            role.name,
            role.uuid,
            ctx.get_user_ip(),
        )
        return role_binding


class PermissionController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    __resource__ = resources.ResourceByRAModel(
        models.Permission,
        convert_underscore=False,
    )

    __policy_service_name__ = "iam"
    __policy_name__ = "permission"


class PermissionBindingController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    __resource__ = resources.ResourceByRAModel(
        models.PermissionBinding,
        convert_underscore=False,
    )

    __policy_service_name__ = "iam"
    __policy_name__ = "permission_binding"

    def create(self, **kwargs):
        binding = super().create(**kwargs)
        role = binding.role
        permission = binding.permission
        ctx = self.get_context()
        actor = ctx.me()
        LOG.info(
            "IAM AUDIT: permission added actor=%s actor_uuid=%s role=%s role_uuid=%s permission=%s ip=%s",
            actor.name,
            actor.uuid,
            role.name,
            role.uuid,
            permission.name,
            ctx.get_user_ip(),
        )
        return binding

    def delete(self, uuid):
        binding = None
        for item in models.PermissionBinding.objects.get_all(
            filters={"uuid": ra_filters.EQ(uuid)},
            limit=1,
        ):
            binding = item
            break
        result = super().delete(uuid)
        if binding:
            role = binding.role
            permission = binding.permission
            ctx = self.get_context()
            actor = ctx.me()
            LOG.info(
                "IAM AUDIT: permission removed actor=%s actor_uuid=%s role=%s role_uuid=%s permission=%s ip=%s",
                actor.name,
                actor.uuid,
                role.name,
                role.uuid,
                permission.name,
                ctx.get_user_ip(),
            )
        return result


class WellKnownController(
    controllers.BaseNestedResourceController,
    EnforceMixin,
):
    def get(self, parent_resource, uuid):
        return parent_resource.get_wellknown_info()

    def filter(self, parent_resource, **kwargs):
        return ["openid-configuration"]


class IdpController(
    controllers.BaseResourceControllerPaginated,
    EnforceMixin,
):
    __resource__ = resources.ResourceByRAModel(
        models.Idp,
        convert_underscore=False,
    )

    def create(self, **kwargs):
        if self.enforce(c.PERMISSION_IDP_CREATE):
            return super().create(**kwargs)
        raise iam_e.CanNotCreateIdp(
            name=kwargs.get("name", ""),
            rule=c.PERMISSION_IDP_CREATE,
        )

    def filter(self, filters, **kwargs):
        if self.enforce(c.PERMISSION_IDP_READ_ALL):
            return super().filter(filters, **kwargs)
        raise iam_e.CanNotListIdps(
            rule=c.PERMISSION_IDP_READ_ALL,
        )

    def update(self, uuid, **kwargs):
        if self.enforce(c.PERMISSION_IDP_UPDATE):
            return super().update(uuid, **kwargs)
        raise iam_e.CanNotUpdateIdp(
            uuid=uuid,
            rule=c.PERMISSION_IDP_UPDATE,
        )

    def delete(self, uuid):
        if self.enforce(c.PERMISSION_IDP_DELETE):
            return super().delete(uuid)
        raise iam_e.CanNotDeleteIdp(
            uuid=uuid,
            rule=c.PERMISSION_IDP_DELETE,
        )

    def _get_request_params(self, resource):
        return {
            "idp_uuid": resource.uuid,
            "host_url": self._req.host_url,
        }

    def _build_oauth_session(self, resource):

        oauth_session = requests_client.OAuth2Session(
            client_id=resource.client_id,
            client_secret=resource.client_secret,
            redirect_uri=resource.get_redirect_uri(
                self._get_request_params(resource),
            ),
        )

        return oauth_session

    @actions.get
    def login(self, resource):
        idp_client = idp.IdpClient(resource.well_known_endpoint)
        metadata = idp_client.get_idp_metadata()

        oauth_session = self._build_oauth_session(resource)

        auth_url, _ = oauth_session.create_authorization_url(
            metadata["authorization_endpoint"],
            scope=resource.scope,
        )

        return None, 307, [("Location", auth_url)]

    @actions.get
    def authorize(
        self,
        resource,
        client_id,
        redirect_uri,
        state,
        response_type,
        scope,
        nonce=models.Idp.NONCE_DEFAULT,
        approval_prompt="auto",
        access_type="online",
        include_granted_scopes=None,
    ):
        # TODO(agent): Implement approval_prompt
        redirect_uri = resource.authorize(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            response_type=response_type,
            scope=scope,
            nonce=nonce,
        )

        return None, 307, [("Location", redirect_uri)]

    @actions.get
    def callback(self, resource, state, session_state, iss, code):

        idp_client = idp.IdpClient(resource.well_known_endpoint)
        metadata = idp_client.get_idp_metadata()

        oauth_session = self._build_oauth_session(resource)
        token = oauth_session.fetch_token(
            metadata["token_endpoint"],
            code=code,
            authorization_response=resource.get_redirect_uri(
                self._get_request_params(resource),
            ),
        )
        return token


class ClientsController(controllers.BaseResourceControllerPaginated, EnforceMixin):
    __resource__ = resources.ResourceByModelWithCustomProps(
        models.IamClient,
        convert_underscore=False,
        hidden_fields=resources.HiddenFieldMap(
            get=["salt", "secret_hash", "secret"],
            create=["salt", "secret_hash"],
            update=["salt", "secret_hash"],
            filter=["salt", "secret_hash", "secret"],
        ),
    )

    def get_resource_by_uuid(self, uuid, parent_resource=None):
        is_default_alias = uuid == common_c.DEFAULT_CLIENT_ALIAS
        if is_default_alias:
            uuid = common_c.DEFAULT_CLIENT_UUID

        resource = super().get_resource_by_uuid(uuid, parent_resource)
        resource._resolved_from_default_alias = is_default_alias
        return resource

    def create(self, **kwargs):
        if self.enforce(c.PERMISSION_IAM_CLIENT_CREATE):
            return super().create(**kwargs)
        raise iam_e.CanNotCreateIamClient(
            name=kwargs["name"],
            rule=c.PERMISSION_IAM_CLIENT_CREATE,
        )

    def filter(self, filters, **kwargs):
        if self.enforce(c.PERMISSION_IAM_CLIENT_READ_ALL):
            return super().filter(filters, **kwargs)
        raise iam_e.CanNotListIamClients(
            rule=c.PERMISSION_IAM_CLIENT_READ_ALL,
        )

    def update(self, uuid, **kwargs):
        if self.enforce(c.PERMISSION_IAM_CLIENT_UPDATE):
            return super().update(uuid, **kwargs)
        raise iam_e.CanNotUpdateIamClient(
            uuid=uuid,
            rule=c.PERMISSION_IAM_CLIENT_UPDATE,
        )

    def delete(self, uuid):
        if self.enforce(c.PERMISSION_IAM_CLIENT_DELETE):
            return super().delete(uuid)
        raise iam_e.CanNotDeleteIamClient(
            uuid=uuid,
            rule=c.PERMISSION_IAM_CLIENT_DELETE,
        )

    @actions.get
    def auth(self, resource, **kwargs):
        login_url = (
            f"{self._req.host_url}/v1/iam/web/login/index.html?"
            + "&".join([f"{name}={value}" for name, value in kwargs.items()])
            + f"&client_uuid={resource.uuid}"
        )
        return None, 307, [("Location", login_url)]

    @actions.post
    def login(self, resource, user, password, **kwargs):
        raise NotImplementedError()

    @oa_utils.extend_schema(**oa_specs.OA_SPEC_GET_TOKEN_KWARGS)
    @actions.post
    def get_token(self, resource, grant_type, **kwargs):
        grant_type_map = {
            c.GRANT_TYPE_PASSWORD: (
                c.PARAM_USERNAME,
                resource.get_token_by_password,
            ),
            c.GRANT_TYPE_PASSWORD_USERNAME: (
                c.PARAM_USERNAME,
                resource.get_token_by_password_username,
            ),
            c.GRANT_TYPE_PASSWORD_EMAIL: (
                c.PARAM_EMAIL,
                resource.get_token_by_password_email,
            ),
            c.GRANT_TYPE_PASSWORD_PHONE: (
                c.PARAM_PHONE,
                resource.get_token_by_password_phone,
            ),
            c.GRANT_TYPE_PASSWORD_LOGIN: (
                c.PARAM_LOGIN,
                resource.get_token_by_password_login,
            ),
        }
        ctx = self.get_context()
        if grant_type in grant_type_map:
            client_id = kwargs.get(
                c.PARAM_CLIENT_ID,
                self._req.headers.get(c.HEADER_CLIENT_ID, ""),
            )
            client_secret = kwargs.get(
                c.PARAM_CLIENT_SECRET,
                self._req.headers.get(c.HEADER_CLIENT_SECRET, ""),
            )
            is_default_alias = getattr(
                resource,
                "_resolved_from_default_alias",
                False,
            )
            if client_id or client_secret or not is_default_alias:
                resource.validate_client_creds(
                    client_id=client_id,
                    client_secret=client_secret,
                )
            payload = dict(
                password=kwargs.get(c.PARAM_PASSWORD),
                scope=kwargs.get(c.PARAM_SCOPE, ""),
                ttl=kwargs.get(c.PARAM_TTL, None),
                refresh_ttl=kwargs.get(c.PARAM_REFRESH_TTL, None),
                otp_code=self._req.headers.get(c.HEADER_OTP_CODE, None),
                root_endpoint=ra_utils.lastslash(
                    ctx.get_real_url_with_prefix(),
                ),
                service_account_uuid=kwargs.get(c.PARAM_SERVICE_ACCOUNT_UUID),
            )
            login_attr, token_getter = grant_type_map[grant_type]
            payload[login_attr] = kwargs.get(login_attr)
            if not payload[login_attr]:
                raise iam_e.ServiceAccountUuidRequiredError()

            try:
                token = token_getter(**payload)
            except Exception:
                LOG.info(
                    "IAM AUDIT: login failed grant_type=%s login=%s ip=%s",
                    grant_type,
                    kwargs.get(login_attr),
                    ctx.get_user_ip(),
                )
                raise
            LOG.info(
                "IAM AUDIT: login success user=%s uuid=%s wildcard=%s ip=%s",
                token.user.name,
                token.user.uuid,
                token.has_permission(c.PERMISSION_WILDCARD_NAME),
                ctx.get_user_ip(),
            )

        elif grant_type == c.GRANT_TYPE_REFRESH_TOKEN:
            try:
                token = resource.get_token_by_refresh_token(
                    refresh_token=kwargs.get("refresh_token"),
                    scope=kwargs.get(c.PARAM_SCOPE, None),
                )
            except Exception:
                LOG.info(
                    "IAM AUDIT: login failed grant_type=%s ip=%s",
                    grant_type,
                    ctx.get_user_ip(),
                )
                raise
            LOG.info(
                "IAM AUDIT: login success user=%s uuid=%s wildcard=%s ip=%s",
                token.user.name,
                token.user.uuid,
                token.has_permission(c.PERMISSION_WILDCARD_NAME),
                ctx.get_user_ip(),
            )
        elif grant_type == c.GRANT_TYPE_AUTHORIZATION_CODE:
            try:
                token = resource.get_token_by_authorization_code(
                    code=kwargs.get(c.PARAM_CODE),
                    redirect_uri=kwargs.get(c.PARAM_REDIRECT_URI),
                )
            except Exception:
                LOG.info(
                    "IAM AUDIT: login failed grant_type=%s ip=%s",
                    grant_type,
                    ctx.get_user_ip(),
                )
                raise
            LOG.info(
                "IAM AUDIT: login success user=%s uuid=%s wildcard=%s ip=%s",
                token.user.name,
                token.user.uuid,
                token.has_permission(c.PERMISSION_WILDCARD_NAME),
                ctx.get_user_ip(),
            )
        elif grant_type == c.GRANT_TYPE_ACCESS_TOKEN:
            service_account_uuid = kwargs.get(c.PARAM_SERVICE_ACCOUNT_UUID)
            if not service_account_uuid:
                raise iam_e.ServiceAccountUuidRequiredError()

            try:
                # Get current user from token context
                current_user = models.User.me()

                token = resource._handle_service_account_token_request(
                    authenticated_user=current_user,
                    service_account_uuid=service_account_uuid,
                    scope=kwargs.get(c.PARAM_SCOPE, ""),
                    ttl=kwargs.get(c.PARAM_TTL, None),
                    refresh_ttl=kwargs.get(c.PARAM_REFRESH_TTL, None),
                    root_endpoint=ra_utils.lastslash(
                        ctx.get_real_url_with_prefix(),
                    ),
                )
            except Exception:
                LOG.info(
                    "IAM AUDIT: service token exchange failed grant_type=%s service_account_uuid=%s ip=%s",
                    grant_type,
                    service_account_uuid,
                    ctx.get_user_ip(),
                )
                raise
            LOG.info(
                "IAM AUDIT: service token exchange success user=%s uuid=%s wildcard=%s ip=%s",
                token.user.name,
                token.user.uuid,
                token.has_permission(c.PERMISSION_WILDCARD_NAME),
                ctx.get_user_ip(),
            )
        else:
            raise iam_e.InvalidGrantType(grant_type=grant_type)
        return token.get_response_body()

    @actions.get
    def introspect(self, resource):
        return contexts.get_context().iam_context.introspection_info()

    @actions.get
    def me(self, resource):
        return resource.me().get_response_body()

    @actions.get
    def userinfo(self, resource):
        return resource.userinfo().get_response_body()

    @oa_utils.extend_schema(**oa_specs.OA_SPEC_SEND_RESET_PASSWORD_CODE)
    @actions.post
    def send_reset_password_code(self, resource, email=None):
        if not self.enforce(c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE):
            raise iam_e.CanNotSendResetPasswordCode(
                uuid=resource.uuid,
                rule=c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE,
            )
        email = email or self._req.params.get("email")
        if not email:
            raise ValidationException(description="Email is required")
        app_endpoint = _get_app_endpoint(req=self._req)
        resource.send_reset_password_event(email=email, app_endpoint=app_endpoint)

    @actions.post
    def logout(self, resource):
        token = models.Token.my()
        token.delete()
        return {}

    @actions.get
    def jwks(self, resource):
        return resource.get_jwks()


class WebController:
    def __init__(self, request):
        super().__init__()
        self._req = request

    def do_404(self):
        return self._req.ResponseClass(body="Not Found", status=404)

    def do(self, path, parent_resource=None, **kwargs):
        return self.do_404()

    @classmethod
    def get_resource(cls):
        return None


class IamWebController(WebController):
    TEMPLATE_DIR = os_path.abspath("web")

    ERROR_FILES = {
        404: "errors/404.html",
        500: "errors/500.html",
    }

    RENDER_FILES = [
        "login/index.html",
    ]

    def _get_file_body(self, full_path):
        with open(full_path, "rb") as fp:
            return fp.read()

    def _build_response(self, path, request_context=None):
        full_path = os_path.join(self.TEMPLATE_DIR, path)
        http_code = 500

        try:
            buff = self._get_file_body(full_path)
            if path in self.RENDER_FILES:
                buff = buff.decode("utf-8")
                buff = jinja2.Template(buff).render(**request_context)
                buff = buff.encode("utf-8")
            file_mimetype = mimetypes.guess_file_type(full_path)[0]
            http_code = 200
        except OSError as e:
            full_path = os_path.join(self.TEMPLATE_DIR, self.ERROR_FILES[500])
            if e.errno == errno.ENOENT:
                full_path = os_path.join(self.TEMPLATE_DIR, self.ERROR_FILES[404])
                http_code = 404
            buff = self._get_file_body(full_path)
            file_mimetype = mimetypes.guess_file_type(full_path)[0]
        except Exception:
            full_path = os_path.join(self.TEMPLATE_DIR, self.ERROR_FILES[500])
            buff = self._get_file_body(full_path)
            file_mimetype = mimetypes.guess_file_type(full_path)[0]

        return self._req.ResponseClass(
            body=buff,
            status=http_code,
            headerlist=[("Content-Type", file_mimetype)],
        )

    def _build_request_context(self):
        result = dict(self._req.params)
        result.update({"host_url": self._req.host_url})
        return result

    def do(self, path, parent_resource=None):
        request_context = self._build_request_context()
        return self._build_response(path.lstrip("/"), request_context)


class AuthorizationInfoController(controllers.BaseResourceControllerPaginated):
    __resource__ = resources.ResourceByRAModel(
        models.IdpAuthorizationInfo,
        convert_underscore=False,
        hidden_fields=[
            "idp",
            "state",
            "nonce",
            "code",
            "response_type",
            "token",
        ],
    )

    @actions.post
    def confirm(self, resource, redirect_me=None):
        resource.confirm()
        callback_uri = resource.construct_callback_uri()

        if redirect_me:
            return None, 307, [("Location", callback_uri)]

        return {
            "redirect_url": callback_uri,
        }
