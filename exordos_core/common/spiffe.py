#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

"""X.509-SVID issuance independent of application-specific authorization."""

from dataclasses import dataclass
import datetime
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID
from cryptography.x509.oid import NameOID


LINK_TRUST_DOMAIN = "link.exordos.internal"


def trust_domain(spiffe_id: str) -> str:
    """Validate a canonical workload SPIFFE ID and return its trust domain."""
    if not isinstance(spiffe_id, str) or len(spiffe_id) > 2048:
        raise ValueError("SPIFFE ID must be a string of at most 2048 ASCII characters")
    match = re.fullmatch(
        r"spiffe://([a-z0-9._-]{1,255})((?:/[a-zA-Z0-9._-]+)+)", spiffe_id
    )
    if not match or any(part in (".", "..") for part in match[2].split("/")[1:]):
        raise ValueError(
            "SPIFFE ID requires a canonical trust domain and workload path"
        )
    return match[1]


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _key_usage(ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not ca,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


@dataclass(frozen=True)
class IssuedCertificate:
    """PEM output; private keys must only be delivered to the intended workload."""

    key: bytes
    cert: str
    csr: bytes
    expiration_at: datetime.datetime


def create_authority(domain: str) -> IssuedCertificate:
    """Create a self-signed, leaf-only CA for a canonical trust domain."""
    if trust_domain(f"spiffe://{domain}/authority") != domain:
        raise ValueError("SPIFFE authority requires a trust domain without a path")
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Exordos SPIFFE CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"spiffe://{domain}")]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return IssuedCertificate(
        _key_pem(key),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        b"",
        cert.not_valid_after_utc,
    )


def issue_certificate(
    authority_key: bytes, authority_cert: str, spiffe_id: str, validity_days: int
) -> IssuedCertificate:
    """Issue a fresh key and SVID under the supplied authority."""
    if type(validity_days) is not int or not 1 <= validity_days <= 365:
        raise ValueError("SPIFFE validity_days must be between 1 and 365")
    return issue_svid(
        authority_key, authority_cert, spiffe_id, datetime.timedelta(days=validity_days)
    )


def issue_svid(
    authority_key: bytes,
    authority_cert: str,
    spiffe_id: str,
    lifetime: datetime.timedelta,
) -> IssuedCertificate:
    """Generate a key and issue an X.509-SVID with the requested lifetime."""
    domain = trust_domain(spiffe_id)
    if not datetime.timedelta(0) < lifetime <= datetime.timedelta(days=365):
        raise ValueError("Invalid SPIFFE certificate lifetime")
    ca = x509.load_pem_x509_certificate(authority_cert.encode())
    ca_key = serialization.load_pem_private_key(authority_key, password=None)
    ca_ids = ca.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.UniformResourceIdentifier)
    if (
        ca_ids != [f"spiffe://{domain}"]
        or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    ):
        raise ValueError("SPIFFE authority does not match the requested trust domain")
    encoding, form = (
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if ca_key.public_key().public_bytes(encoding, form) != ca.public_key().public_bytes(
        encoding, form
    ):
        raise ValueError("SPIFFE authority key and certificate do not match")
    now = datetime.datetime.now(datetime.timezone.utc)
    expiration = now + lifetime
    if ca.not_valid_before_utc > now or ca.not_valid_after_utc <= expiration:
        raise ValueError(
            "SPIFFE authority is not valid for the requested certificate lifetime"
        )
    key = ec.generate_private_key(ec.SECP256R1())
    san = x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id)])
    subject = x509.Name([])
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .add_extension(san, critical=True)
        .sign(key, hashes.SHA256())
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(expiration)
        .add_extension(san, critical=True)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(ca=False), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return IssuedCertificate(
        _key_pem(key),
        cert.public_bytes(serialization.Encoding.PEM).decode() + authority_cert,
        csr.public_bytes(serialization.Encoding.PEM),
        cert.not_valid_after_utc,
    )
