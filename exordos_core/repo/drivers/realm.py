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

from requests import auth as requests_auth

from exordos_core.repo.drivers import nginx


class _BearerAuth(requests_auth.AuthBase):
    def __init__(self, token: str) -> None:
        self._token = token

    def __call__(self, request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        return request


class RealmProxyRepoDriver(nginx.NginxProxyRepoDriver):
    KIND = "realm"

    @staticmethod
    def _get_auth(spec):
        return _BearerAuth(spec.access_token)
