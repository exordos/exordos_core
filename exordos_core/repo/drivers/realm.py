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

from urllib.parse import unquote
from urllib.parse import urlsplit

from requests import auth as requests_auth

from exordos_core.repo.drivers import nginx


class _BearerAuth(requests_auth.AuthBase):
    def __init__(self, token: str, url: str) -> None:
        self._token = token
        self._origin = self._get_origin(url)

    @staticmethod
    def _get_origin(url):
        parsed = urlsplit(url)
        return (
            parsed.scheme,
            parsed.hostname,
            parsed.port
            if parsed.port is not None
            else {"https": 443, "http": 80}.get(parsed.scheme),
        )

    def __call__(self, request):
        if self._get_origin(request.url) != self._origin:
            raise ValueError("Request URL escapes the configured realm origin")
        request.headers["Authorization"] = f"Bearer {self._token}"
        return request


class RealmProxyRepoDriver(nginx.NginxProxyRepoDriver):
    KIND = "realm"

    @staticmethod
    def _get_auth(spec):
        return _BearerAuth(spec.access_token, spec.url)

    @staticmethod
    def _validate_components(*components):
        for component in components:
            decoded = unquote(component)
            if (
                not decoded
                or decoded in (".", "..")
                or any(char in decoded for char in "/\\:?#%")
                or any(ord(char) <= 32 or ord(char) == 127 for char in decoded)
            ):
                raise ValueError("Invalid repository path component")

    def get_element(self, name, version="latest"):
        self._validate_components(name, version)
        return super().get_element(name, version)

    def artifact_uri(self, element_name, element_version, category, artifact_name):
        self._validate_components(
            element_name, element_version, category, artifact_name
        )
        return super().artifact_uri(
            element_name, element_version, category, artifact_name
        )
