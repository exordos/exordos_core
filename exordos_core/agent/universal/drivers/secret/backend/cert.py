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

import datetime
import logging
import typing as tp

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.oid import NameOID
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

INTERNAL_CA_VALIDITY = datetime.timedelta(days=3650)
INTERNAL_CERTIFICATE_VALIDITY = datetime.timedelta(days=90)
INTERNAL_CA_RENEWAL_THRESHOLD = INTERNAL_CERTIFICATE_VALIDITY + datetime.timedelta(
    days=14
)


def _pem_private_key(private_key: rsa.RSAPrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _pem_certificate(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def _internal_ca(resource: models.Resource) -> tuple[bytes, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Exordos Internal"),
            x509.NameAttribute(
                NameOID.COMMON_NAME,
                f"{resource.value['name']} CA",
            ),
        ]
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + INTERNAL_CA_VALIDITY)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )
    return _pem_private_key(private_key), _pem_certificate(certificate)


def _issue_internal_certificate(
    resource: models.Resource,
    ca_key_pem: bytes | None = None,
    ca_cert_pem: str | None = None,
) -> driver_dm.Certificate:
    if ca_cert_pem is not None:
        ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
        now = datetime.datetime.now(datetime.timezone.utc)
        if ca_cert.not_valid_after_utc <= now + INTERNAL_CA_RENEWAL_THRESHOLD:
            ca_key_pem = None
            ca_cert_pem = None
    if ca_key_pem is None or ca_cert_pem is None:
        ca_key_pem, ca_cert_pem = _internal_ca(resource)
    ca_key = tp.cast(
        rsa.RSAPrivateKey,
        serialization.load_pem_private_key(ca_key_pem, password=None),
    )
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    domains = resource.value["domains"]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    san = x509.SubjectAlternativeName([x509.DNSName(domain) for domain in domains])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .add_extension(san, critical=False)
        .sign(private_key, hashes.SHA256())
    )
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + INTERNAL_CERTIFICATE_VALIDITY)
        .add_extension(san, critical=False)
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    certificate_pem = _pem_certificate(certificate)
    return driver_dm.Certificate.from_cert_resource(
        resource,
        _pem_private_key(private_key),
        csr.public_bytes(serialization.Encoding.PEM),
        certificate_pem + ca_cert_pem,
        certificate.not_valid_after_utc,
        ca_key_pem=ca_key_pem,
        ca_cert_pem=ca_cert_pem,
    )


class CertBotBackendClient(base.AbstractBackendClient):
    """Cert bot backend client."""

    DEFAULT_PRIVATE_KEY_PATH = "/etc/exordos_core/certbot/privkey.pem"

    def __init__(
        self,
        dns_client: dns_clients.TinyDNSCoreClient,
        admin_email: str,
        private_key_path: str = DEFAULT_PRIVATE_KEY_PATH,
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

        if isinstance(cert.method, secret_dm.InternalCACertificateMethod):
            driver_cert = _issue_internal_certificate(resource)
            driver_cert.save()
            return driver_cert.to_resource_value()

        # Create cert via DNS
        pkey_pem, csr_pem, fullchain_pem = acme.create_cert(
            self._get_or_create_acme_client(),
            self._dns_client,
            cert.domains,
        )
        cert_x509 = x509.load_pem_x509_certificate(fullchain_pem.encode())
        expiration_at = cert_x509.not_valid_after_utc

        # Build storagable password and save
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

        method = cert["meta"]["method"]["kind"]
        if method == secret_dm.InternalCACertificateMethod.KIND:
            if (
                set(cert["meta"]["domains"]) == set(resource.value["domains"])
                and not cert.is_under_threshold()
            ):
                return cert.to_resource_value()
            ca_key_pem = None if cert.ca_key is None else cert.ca_key.encode()
            driver_cert = _issue_internal_certificate(
                resource,
                ca_key_pem,
                cert.ca_cert,
            )
            cert.delete()
            driver_cert.save()
            return driver_cert.to_resource_value()

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
        if cert["meta"]["method"]["kind"] != secret_dm.InternalCACertificateMethod.KIND:
            acme.revoke_cert(self._get_or_create_acme_client(), cert.fullchain)
        cert.delete()
