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

from restalchemy.api import routes

from exordos_core.user_api.secret.api import controllers


class SecretsRoute(routes.Route):
    """Handler for /v1/secret/secrets/ endpoint"""

    __controller__ = controllers.SecretsController


class PasswordsRoute(routes.Route):
    """Handler for /v1/secret/passwords/ endpoint"""

    __controller__ = controllers.PasswordsController


class CertificatesRoute(routes.Route):
    """Handler for /v1/secret/certificates/ endpoint"""

    __controller__ = controllers.CertificatesController


class SSHKeysRoute(routes.Route):
    """Handler for /v1/secret/ssh_keys/ endpoint"""

    __controller__ = controllers.SSHKeysController


class RSAKeysRoute(routes.Route):
    """Handler for /v1/secret/rsa_keys/ endpoint"""

    __controller__ = controllers.RSAKeysController


class SecretRoute(routes.Route):
    """Handler for /v1/secret/ endpoint"""

    __allow_methods__ = [routes.FILTER]
    __controller__ = controllers.SecretController

    secrets = routes.route(SecretsRoute)
    passwords = routes.route(PasswordsRoute)
    certificates = routes.route(CertificatesRoute)
    ssh_keys = routes.route(SSHKeysRoute)
    rsa_keys = routes.route(RSAKeysRoute)
