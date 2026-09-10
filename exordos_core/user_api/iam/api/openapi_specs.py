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

from restalchemy.api import constants as ra_c
from restalchemy.openapi import constants as oa_c

from exordos_core.user_api.iam import constants as c

responses = {}
responses.update(
    oa_c.build_openapi_user_response(
        **{
            "type": "object",
            "required": [
                "access_token",
                "expires_at",
                "id_token",
                "refresh_token",
                "scope",
                "token_type",
            ],
            "properties": {
                "access_token": {
                    "type": "string",
                    "description": "JWT access token",
                    "example": "eyJhbGciOiJSUzI...",
                },
                "expires_at": {
                    "type": "integer",
                    "format": "int64",
                    "description": "UNIX timestamp when token expires",
                    "example": 1740524674,
                },
                "id_token": {
                    "type": "string",
                    "description": "OpenID Connect ID Token",
                    "example": "eyJhbGciOiJSUzI1NiIsInR...",
                },
                "refresh_token": {
                    "type": "string",
                    "description": "Refresh token",
                    "example": "eyJhbGciOiJIUzUxMiIsIn...",
                },
                "scope": {
                    "type": "string",
                    "description": "Granted scopes (space-separated)",
                    "example": "openid email profile",
                },
                "token_type": {
                    "type": "string",
                    "description": "Type of token",
                    "enum": ["Bearer"],
                    "example": "Bearer",
                },
            },
        }
    )
)
responses.update(
    oa_c.build_openapi_user_response(
        code=401,
        **{
            "title": "Wrong OTP error",
            "description": "The provided otp code is invalid",
            "type": "object",
            "required": [
                "error",
                "error_description",
            ],
            "properties": {
                "error": {
                    "type": "string",
                    "description": "Error class name",
                    "example": "OSError",
                },
                "error_description": {
                    "type": "string",
                    "description": "Error description",
                    "example": "A human-readable explanation of problem",
                },
            },
            "example": {
                "error": "invalid_client",
                "error_description": "The provided otp code is invalid",
            },
        },
    )
)


OA_SPEC_SEND_RESET_PASSWORD_CODE = dict(
    summary="Send password reset code to email",
    parameters=[
        oa_c.build_openapi_parameter(
            name="IamClientUuid",
            openapi_type="string",
            param_type="path",
            required=True,
        ),
    ],
    request_body=oa_c.build_openapi_req_body(
        description="Trigger a password reset email for the specified user",
        content_type=ra_c.CONTENT_TYPE_APPLICATION_JSON,
        schema={
            "type": "object",
            "required": ["email"],
            "properties": {
                "email": {
                    "type": "string",
                    "format": "email",
                    "description": "Email address of the user to send the reset link to",
                    "example": "user@example.com",
                },
            },
        },
    ),
    responses=oa_c.build_openapi_object_response(
        properties={},
        description="Password reset email sent successfully. No content returned.",
    ),
)

OA_SPEC_RESET_PASSWORD_USER = dict(
    summary="Reset user password",
    parameters=[
        oa_c.build_openapi_parameter(
            name="UserUuid",
            openapi_type="string",
            param_type="path",
            required=True,
        ),
    ],
    request_body=oa_c.build_openapi_req_body(
        description=(
            "New password and optional confirmation code. "
            "Either a valid reset code (from email) or the "
            "`iam.user.reset_password` permission is required."
        ),
        content_type=ra_c.CONTENT_TYPE_APPLICATION_JSON,
        schema={
            "type": "object",
            "required": ["new_password"],
            "properties": {
                "new_password": {
                    "type": "string",
                    "description": "New password for the user",
                    "example": "MyNewP@ssw0rd",
                },
                "code": {
                    "type": "string",
                    "format": "uuid",
                    "description": (
                        "Confirmation code received via email. "
                        "Required when caller does not have "
                        "`iam.user.reset_password` permission."
                    ),
                    "example": "550e8400-e29b-41d4-a716-446655440000",
                },
            },
        },
    ),
    responses=oa_c.build_openapi_get_update_response("User_Get"),
)

OA_SPEC_GET_TOKEN_KWARGS = dict(
    summary="Create token by password",
    parameters=[
        oa_c.build_openapi_parameter(
            name="IamClientUuid",
            openapi_type="string",
            param_type="path",
            required=True,
        ),
        oa_c.build_openapi_parameter(
            name="X-OTP",
            openapi_type="string",
            param_type="header",
            required=False,
        ),
    ],
    responses=responses,
    request_body=oa_c.build_openapi_req_body(
        description="",
        content_type="application/x-www-form-urlencoded",
        schema={
            "oneOf": [
                {
                    "type": "object",
                    "required": [
                        "grant_type",
                        "client_id",
                        "client_secret",
                        "username",
                        "password",
                    ],
                    "properties": {
                        "grant_type": {
                            "type": "string",
                            "enum": [c.GRANT_TYPE_PASSWORD],
                        },
                        "client_id": {"type": "string"},
                        "client_secret": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                        "scope": {"type": "string"},
                        "ttl": {"type": "number", "format": "float"},
                        "refresh_ttl": {"type": "string", "format": "float"},
                        "service_account_uuid": {
                            "type": "string",
                            "format": "uuid",
                            "description": "UUID of the service account",
                        },
                    },
                },
                {
                    "type": "object",
                    "required": [
                        "grant_type",
                        "client_id",
                        "client_secret",
                        "email",
                        "password",
                    ],
                    "properties": {
                        "grant_type": {
                            "type": "string",
                            "enum": [c.GRANT_TYPE_PASSWORD_EMAIL],
                        },
                        "client_id": {"type": "string"},
                        "client_secret": {"type": "string"},
                        "email": {"type": "string"},
                        "password": {"type": "string"},
                        "scope": {"type": "string"},
                        "ttl": {"type": "number", "format": "float"},
                        "refresh_ttl": {"type": "string", "format": "float"},
                        "service_account_uuid": {
                            "type": "string",
                            "format": "uuid",
                            "description": "UUID of the service account",
                        },
                    },
                },
                {
                    "type": "object",
                    "required": [
                        "grant_type",
                        "client_id",
                        "client_secret",
                        "login",
                        "password",
                    ],
                    "properties": {
                        "grant_type": {
                            "type": "string",
                            "enum": [c.GRANT_TYPE_PASSWORD_LOGIN],
                        },
                        "client_id": {"type": "string"},
                        "client_secret": {"type": "string"},
                        "login": {"type": "string"},
                        "password": {"type": "string"},
                        "scope": {"type": "string"},
                        "ttl": {"type": "number", "format": "float"},
                        "refresh_ttl": {"type": "string", "format": "float"},
                        "service_account_uuid": {
                            "type": "string",
                            "format": "uuid",
                            "description": "UUID of the service account",
                        },
                    },
                },
                {
                    "type": "object",
                    "required": [
                        "grant_type",
                        "service_account_uuid",
                    ],
                    "properties": {
                        "grant_type": {
                            "type": "string",
                            "enum": [c.GRANT_TYPE_ACCESS_TOKEN],
                        },
                        "service_account_uuid": {
                            "type": "string",
                            "format": "uuid",
                            "description": "UUID of the service account to get token for",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Project scope in format 'project:<uuid>' (required)",
                        },
                        "ttl": {"type": "number", "format": "float"},
                        "refresh_ttl": {"type": "string", "format": "float"},
                    },
                },
                {
                    "type": "object",
                    "required": [
                        "grant_type",
                        "refresh_token",
                    ],
                    "properties": {
                        "grant_type": {
                            "type": "string",
                            "enum": ["refresh_token"],
                        },
                        "refresh_token": {"type": "string"},
                    },
                },
            ],
            "discriminator": {"propertyName": "grant_type"},
        },
    ),
)

OA_SPEC_ADD_USER_TO_PROJECT = dict(
    summary="Add a user to a project",
    parameters=[
        oa_c.build_openapi_parameter(
            name="ProjectUuid",
            openapi_type="string",
            param_type="path",
            required=True,
        ),
    ],
    request_body=oa_c.build_openapi_req_body(
        description="User and role to assign",
        content_type=ra_c.CONTENT_TYPE_APPLICATION_JSON,
        schema={
            "type": "object",
            "required": ["user", "role"],
            "properties": {
                "user": {
                    "type": "string",
                    "description": (
                        "UUID, email, or name of the user to add. "
                        "Emails and names are matched case-insensitively."
                    ),
                    "example": "550e8400-e29b-41d4-a716-446655440000",
                },
                "role": {
                    "type": "string",
                    "description": "UUID or name of the role to assign (e.g. 'owner')",
                    "example": "owner",
                },
            },
        },
    ),
    responses=oa_c.build_openapi_object_response(
        properties={},
        description="User added to project successfully. Returns role binding details.",
    ),
)

OA_SPEC_GET_ROLE_PERMISSIONS = dict(
    summary="List permissions granted by a role",
    parameters=[
        oa_c.build_openapi_parameter(
            name="RoleUuid",
            openapi_type="string",
            param_type="path",
            required=True,
        ),
    ],
    responses=oa_c.build_openapi_list_model_response("Permission_Get"),
)
