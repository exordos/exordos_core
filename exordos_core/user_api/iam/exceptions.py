# Copyright 2025-2026 Genesis Corporation
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

from gcl_iam import exceptions as iam_exc

from exordos_core.common import exceptions


class UserNotFound(exceptions.CommonNotFoundException):
    __template__ = "User not found"


class CanNotSetOwner(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to assign a different user as"
        " the owner. This action requires the `{rule}` permission, which is"
        " not granted. Please contact your administrator to request the"
        " necessary access or retain the current owner value."
    )


class CanNotCreateUser(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to create users. This action"
        " requires the `{rule}`, which has not been granted."
    )


class CanNotListUsers(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to list users. This action"
        " requires the `{rule}`, which has not been granted. Please contact"
        " your organization administrator to request access or use an"
        " account with the appropriate privileges."
    )


class CanNotUpdateUser(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to update the user `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotConfirmUser(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = "The code `{code}` is not valid. Please request a new code."


class CanNotDeleteUser(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to delete the user `{uuid}`."
        " This action requires the `{rule1}` or `{rule2}`, which has not been"
        " granted."
    )


class CanNotReadUser(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to read the user `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotReadProject(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to read the project `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotUpdateProject(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to update the project"
        " `{name}`. This action requires the `{rule}`, which has not"
        " been granted."
    )


class CanNotDeleteProject(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to delete the project"
        " `{name}`. This action requires the `{rule}`, which has not"
        " been granted."
    )


class CanNotUpdateOrganization(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to update the organization"
        " `{name}`. Only the owner of the organization may update it."
    )


class CanNotDeleteOrganization(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to delete the organization"
        " `{name}`. Only the owner of the organization may delete it."
    )


class CanNotCreateProjectInOrganization(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to create a project in the"
        " organization `{uuid}`. Only the owner of the organization may"
        " create projects."
    )


class CanNotCreateIamClient(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to create a IAM client `{name}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotListIamClients(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to list IAM clients."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotUpdateIamClient(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to update IAM client `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotDeleteIamClient(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to delete IAM client `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotSendResetPasswordCode(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to send a reset password code"
        " for IAM client `{uuid}`. This action requires the `{rule}`,"
        " which has not been granted."
    )


class CanNotResetUserPassword(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "Password reset failed for user `{uuid}`: a valid reset code or the"
        " `{rule}` permission is required. Please use the reset link sent to"
        " your email or contact your administrator."
    )


class CanNotCreateIdp(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to create an IDP `{name}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotListIdps(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to list IDPs."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotUpdateIdp(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to update IDP `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class CanNotDeleteIdp(exceptions.CommonForbiddenException, iam_exc.Forbidden):
    __template__ = (
        "The current user is not permitted to delete IDP `{uuid}`."
        " This action requires the `{rule}`, which has not been granted."
    )


class InvalidGrantType(
    exceptions.CommonValueErrorException,
    iam_exc.InvalidGrantTypeError,
):
    __template__ = "Invalid grant type: {grant_type}"


class InvalidClientId(
    exceptions.CommonValueErrorException,
    iam_exc.InvalidGrantTypeError,
):
    __template__ = "Invalid or unknown client_id: {client_id}"


class InvalidRedirectUri(
    exceptions.CommonValueErrorException,
    iam_exc.InvalidGrantTypeError,
):
    __template__ = "Invalid or unregistered redirect_uri: {redirect_uri}"


class InvalidNonce(
    exceptions.CommonValueErrorException,
    iam_exc.InvalidGrantTypeError,
):
    __template__ = "Invalid or missing nonce: '{nonce}'"


class ServiceAccountPasswordChangeError(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = "Password cannot be set for service accounts"


class ServiceAccountPasswordAuthError(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = "Password authentication is not allowed for service accounts"


class ServiceAccountTokenError(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = "Cannot get token for service account: {reason}"


class CanNotCreateServiceToken(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to create service account tokens or "
        "service user was not found."
    )


class ServiceAccountUuidRequiredError(exceptions.CommonValueErrorException):
    """Exception raised when service_account_uuid is required but not provided."""

    __template__ = "service_account_uuid is required"


class ProjectScopeRequiredError(exceptions.CommonValueErrorException):
    """Exception raised when project scope is required but not provided."""

    __template__ = "Service account tokens can only be issued with project scope"


class CanNotAddUserToProject(
    exceptions.CommonForbiddenException,
    iam_exc.Forbidden,
):
    __template__ = (
        "The current user is not permitted to add users to the project"
        " `{uuid}`. This action requires the `{rule}`, which has not"
        " been granted."
    )
