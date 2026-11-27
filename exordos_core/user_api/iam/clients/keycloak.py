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

import urllib.parse

import bazooka
from bazooka import exceptions as bazooka_exc

from exordos_core.user_api.iam import exceptions


class KeycloakClient:
    def __init__(self, endpoint, timeout=5):
        super().__init__()
        self._endpoint = endpoint
        self._client = bazooka.Client(default_timeout=timeout)

    def check_password(self, realm, client_id, client_secret, login, password):
        endpoint = self._endpoint.rstrip("/") + "/"
        token_endpoint = urllib.parse.urljoin(
            endpoint,
            (f"realms/{realm}/protocol/openid-connect/token"),
        )

        try:
            response = self._client.post(
                token_endpoint,
                data={
                    "grant_type": "password",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "username": login,
                    "password": password,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except bazooka_exc.BadRequestError as exc:
            error = exc.cause.response.json()
            if error.get("error") != "invalid_grant":
                raise
            if error.get("error_description") == "Account is not fully set up":
                account_url = urllib.parse.urljoin(
                    endpoint,
                    f"realms/{urllib.parse.quote(realm, safe='')}/account/",
                )
                raise exceptions.KeycloakRequiredAction(
                    redirect_url=account_url,
                ) from None
            return False

        return response.status_code == 200
