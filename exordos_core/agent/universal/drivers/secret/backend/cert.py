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

import logging
import typing as tp

from cryptography import x509
from gcl_certbot_plugin import acme
from gcl_certbot_plugin import clients as dns_clients
from gcl_certbot_plugin.acme import acme_lib_client
from gcl_sdk.agents.universal.clients.backend import base
from gcl_sdk.agents.universal.clients.backend import exceptions
from gcl_sdk.agents.universal.dm import models
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_exc

from exordos_core.agent.universal.drivers.secret.dm import models as driver_dm
from exordos_core.secret.dm import models as secret_dm

LOG = logging.getLogger(__name__)


class CertBotBackendClient(base.AbstractBackendClient):
    """Cert bot backend client."""

    def __init__(
        self,
        dns_client: dns_clients.TinyDNSCoreClient,
        admin_email: str,
        private_key_path: str,
    ) -> None:
        self._dns_client = dns_client
        self._admin_email = admin_email
        self._client_acme: tp.Optional[acme_lib_client.ClientV2] = None
        self._private_key = acme.get_or_create_client_private_key(private_key_path)

    def _get_or_create_acme_client(self) -> acme_lib_client.ClientV2:
        if self._client_acme is None:
            self._client_acme = acme.get_acme_client(
                self._private_key, self._admin_email
            )
        return self._client_acme

    def get(self, resource: models.Resource) -> tp.Dict[str, tp.Any]:
        """Get the resource value in dictionary format."""
        try:
            cert = driver_dm.Certificate.objects.get_one(
                filters={
                    "uuid": dm_filters.EQ(resource.uuid),
                },
            )
        except ra_exc.RecordNotFound:
            raise exceptions.ResourceNotFound(resource=resource)

        return cert.to_resource_value()

    def create(self, resource: models.Resource) -> tp.Dict[str, tp.Any]:
        """Creates the resource. Returns the created resource."""
        try:
            self.get(resource)
        except exceptions.ResourceNotFound:
            pass
        else:
            raise exceptions.ResourceAlreadyExists(resource=resource)

        cert = secret_dm.Certificate.from_ua_resource(resource)

        # Create cert via DNS
        pkey_pem, csr_pem, fullchain_pem = acme.create_cert(
            self._get_or_create_acme_client(),
            self._dns_client,
            cert.domains,
        )
        cert_x509 = x509.load_pem_x509_certificate(fullchain_pem.encode())
        expiration_at = cert_x509.not_valid_after_utc

        # Build storage_cert and save
        driver_cert = driver_dm.Certificate.from_cert_resource(
            resource, pkey_pem, csr_pem, fullchain_pem, expiration_at
        )

        driver_cert.save()
        return driver_cert.to_resource_value()

    def update(self, resource: models.Resource) -> tp.Dict[str, tp.Any]:
        """Update the resource. Returns the updated resource."""
        try:
            cert = driver_dm.Certificate.objects.get_one(
                filters={
                    "uuid": dm_filters.EQ(resource.uuid),
                },
            )
        except ra_exc.RecordNotFound:
            raise exceptions.ResourceNotFound(resource=resource)

        # TODO(akremenetsky): It's tricky logic to update the cert
        # if domains changed. Need to check domains intersection,
        # check wildcards
        if set(cert["meta"]["domains"]) != set(resource.value["domains"]):
            LOG.error("Not implemented yet")
            raise NotImplementedError("Domains changed")
            # return cert.to_resource_value()

        # Should the cert be renewed?
        if not cert.is_under_threshold():
            return cert.to_resource_value()

        pkey_pem, csr_pem, fullchain_pem = acme.renew_cert(
            self._get_or_create_acme_client(),
            self._dns_client,
            resource.value["domains"],
            cert.pkey.encode(),
        )
        cert_x509 = x509.load_pem_x509_certificate(fullchain_pem.encode())
        expiration_at = cert_x509.not_valid_after_utc

        driver_cert = driver_dm.Certificate.from_cert_resource(
            resource, pkey_pem, csr_pem, fullchain_pem, expiration_at
        )

        cert.delete()
        driver_cert.save()
        return driver_cert.to_resource_value()

    def list(self, kind: str, **kwargs) -> tp.List[tp.Dict[str, tp.Any]]:
        """Lists all resources by kind."""
        certs = driver_dm.Certificate.objects.get_all()

        return [cert.to_resource_value() for cert in certs]

    def delete(self, resource: models.Resource) -> None:
        """Delete the resource."""
        try:
            self.get(resource)
        except exceptions.ResourceNotFound:
            raise exceptions.ResourceNotFound(resource=resource)

        cert = driver_dm.Certificate.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(resource.uuid),
            }
        )
        acme.revoke_cert(self._get_or_create_acme_client(), cert.fullchain)
        cert.delete()
