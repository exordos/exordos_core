#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

import datetime
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from gcl_sdk.agents.universal.dm import models
import pytest
from restalchemy.storage import exceptions as ra_exc

from exordos_core.agent.universal.drivers.secret.backend import cert as backend
from exordos_core.agent.universal.drivers.secret.dm import models as storage
from exordos_core.common import exceptions
from exordos_core.common import spiffe
from exordos_core.secret.dm import models as secrets
from exordos_core.user_api.secret.api.controllers import CertificatesController

IDENTITY = "spiffe://example.org/services/test"


def resource(project=None, **changes):
    value = dict(
        uuid=str(uuid.uuid4()),
        project_id=str(project or uuid.uuid4()),
        name="test",
        description="",
        domains=[],
        email="admin@example.org",
        method={"kind": "spiffe", "spiffe_id": IDENTITY, "validity_days": 30},
        expiration_threshold=14,
        overcome_threshold=False,
    )
    value.update(changes)
    return models.Resource.from_value(value, "certificate", frozenset(value))


@pytest.fixture
def issuer():
    authorities = {}

    def lookup(filters):
        # Scope is encoded in a deterministic UUID; keep real model instances.
        for entry in authorities.values():
            if filters == {"uuid": backend.dm_filters.EQ(entry.uuid)}:
                return entry
        raise ra_exc.RecordNotFound(model="SPIFFEAuthority", filters=filters)

    def insert(entry):
        assert entry.uuid not in authorities
        authorities[entry.uuid] = entry

    with (
        patch.object(storage.SPIFFEAuthority, "objects") as objects,
        patch.object(storage.SPIFFEAuthority, "insert", insert),
    ):
        objects.get_one.side_effect = lookup
        yield authorities


def test_issue_svid_and_renew_with_same_authority(issuer):
    target = resource()
    first = backend.CertBotBackendClient._spiffe_certificate(target)
    second = backend.CertBotBackendClient._spiffe_certificate(target)
    assert len(issuer) == 1
    leaf, ca = x509.load_pem_x509_certificates(first.fullchain.encode())
    renewed, renewed_ca = x509.load_pem_x509_certificates(second.fullchain.encode())
    leaf.verify_directly_issued_by(ca)
    renewed.verify_directly_issued_by(ca)
    assert renewed_ca == ca
    assert leaf.serial_number != renewed.serial_number
    assert first.pkey != second.pkey
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert san.critical
    assert san.value.get_values_for_type(x509.UniformResourceIdentifier) == [IDENTITY]
    assert not leaf.subject
    assert not leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    usage = leaf.extensions.get_extension_for_class(x509.KeyUsage)
    assert usage.critical and usage.value.digital_signature
    assert not usage.value.key_cert_sign and not usage.value.crl_sign
    assert set(
        leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    ) == {
        ExtendedKeyUsageOID.CLIENT_AUTH,
        ExtendedKeyUsageOID.SERVER_AUTH,
    }
    assert leaf.not_valid_after_utc - datetime.datetime.now(
        datetime.timezone.utc
    ) > datetime.timedelta(days=29)
    csr = x509.load_pem_x509_csr(first.csr.encode())
    assert csr.is_signature_valid
    assert (
        csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        == san.value
    )
    value = first.to_resource_value()
    assert "ca_key" not in value and "key" in value
    assert next(iter(issuer.values())).key != value["key"]


def test_authorities_are_shared_only_within_project_and_domain(issuer):
    project = uuid.uuid4()
    for name in ("one", "two"):
        backend.CertBotBackendClient._spiffe_certificate(
            resource(
                project,
                method={"kind": "spiffe", "spiffe_id": f"spiffe://example.org/{name}"},
            )
        )
    assert len(issuer) == 1
    backend.CertBotBackendClient._spiffe_certificate(resource())
    assert len(issuer) == 2
    backend.CertBotBackendClient._spiffe_certificate(
        resource(
            project, method={"kind": "spiffe", "spiffe_id": "spiffe://other.org/test"}
        )
    )
    assert len(issuer) == 3
    assert len({a.cert for a in issuer.values()}) == 3


@pytest.mark.parametrize(
    "identity",
    [
        "",
        "https://example.org/test",
        "spiffe://example.org",
        "spiffe://example.org/",
        "spiffe://example.org/a/",
        "spiffe://example.org/a//b",
        "spiffe://example.org/a/../b",
        "spiffe://example.org/./a",
        "spiffe://example.org/a%2fb",
        "spiffe://user@example.org/a",
        "spiffe://example.org:7776/a",
        "spiffe://example.org/a?x",
        "spiffe://example.org/a#x",
        "spiffe://EXAMPLE.org/a",
        "spiffe://example.org/ю",
        "spiffe://" + "a" * 256 + "/b",
        "spiffe://example.org/" + "a" * 2048,
        "spiffe://example.org/a\nb",
    ],
)
def test_reject_invalid_identity(identity):
    with pytest.raises(ValueError):
        spiffe.trust_domain(identity)


@pytest.mark.parametrize(
    "identity",
    [
        IDENTITY,
        "spiffe://example.org/tenant/UUID_1",
        "spiffe://" + "a" * 255 + "/x",
        "spiffe://example.org/" + "a" * (2048 - 21),
    ],
)
def test_accept_identity(identity):
    assert spiffe.trust_domain(identity)


def test_reject_foreign_or_expiring_authority():
    ca = spiffe.create_authority("example.org")
    with pytest.raises(ValueError, match="trust domain"):
        spiffe.issue_certificate(ca.key, ca.cert, "spiffe://other.org/test", 30)
    other = spiffe.create_authority("example.org")
    with pytest.raises(ValueError, match="do not match"):
        spiffe.issue_certificate(other.key, ca.cert, IDENTITY, 30)
    root = x509.load_pem_x509_certificate(ca.cert.encode())
    key = serialization.load_pem_private_key(ca.key, password=None)
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(root.subject)
        .issuer_name(root.subject)
        .public_key(root.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
    )
    for ext in root.extensions:
        builder = builder.add_extension(ext.value, ext.critical)
    near_expiry = (
        builder.sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    with pytest.raises(ValueError, match="lifetime"):
        spiffe.issue_certificate(ca.key, near_expiry, IDENTITY, 30)


@pytest.mark.parametrize(
    "change",
    [
        {"domains": ["example.org"]},
        {"key": "forbidden"},
        {"method": {"kind": "spiffe", "spiffe_id": "spiffe://example.org/"}},
    ],
)
def test_api_rejects_invalid_spiffe_request(change):
    value = resource(**change).value
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(value)


def test_method_roundtrip_and_target_fields():
    instance = secrets.Certificate(
        uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="test",
        email="admin@example.org",
        domains=[],
        method=secrets.SPIFFECertificateMethod(spiffe_id=IDENTITY),
    )
    instance.validate()
    value = instance.dump_to_simple_view()
    assert value["method"] == {
        "kind": "spiffe",
        "spiffe_id": IDENTITY,
        "validity_days": 30,
    }
    assert "method" in instance.get_resource_target_fields()
    assert not {"cert", "key"} & instance.get_resource_target_fields()
    CertificatesController._validate_certificate(
        {k: v for k, v in value.items() if k not in ("key", "cert")}
    )


def test_backend_renews_only_near_expiry_and_does_not_revoke_ca(issuer):
    target = resource()
    stored = backend.CertBotBackendClient._spiffe_certificate(target)
    with (
        patch.object(backend.acme, "get_or_create_client_private_key"),
        patch.object(backend.acme, "create_cert") as acme_issue,
        patch.object(backend.acme, "renew_cert") as acme_renew,
        patch.object(backend.acme, "revoke_cert") as acme_revoke,
        patch.object(storage.Certificate, "objects") as objects,
        patch.object(storage.Certificate, "update"),
        patch.object(storage.Certificate, "delete"),
    ):
        client = backend.CertBotBackendClient(MagicMock(), "a@example.org", "/unused")
        objects.get_one.return_value = stored
        original_key = stored.pkey
        unchanged = client.update(target)
        assert unchanged["key"] == original_key
        assert stored.pkey == original_key
        stored.expiration_at = datetime.datetime.now(
            datetime.timezone.utc
        ) + datetime.timedelta(days=2)
        renewed = client.update(target)
        assert renewed["key"] != original_key
        assert renewed["overcome_threshold"] is False
        assert len(issuer) == 1
        # Identity changes reissue immediately, even well before expiration.
        changed = resource(
            uuid=str(target.uuid),
            project_id=target.value["project_id"],
            method={
                "kind": "spiffe",
                "spiffe_id": "spiffe://example.org/changed",
                "validity_days": 30,
            },
        )
        result = client.update(changed)
        assert x509.load_pem_x509_certificate(
            result["cert"].encode()
        ).extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.UniformResourceIdentifier) == [
            "spiffe://example.org/changed"
        ]
        client.delete(changed)
        assert len(issuer) == 1
        acme_issue.assert_not_called()
        acme_renew.assert_not_called()
        acme_revoke.assert_not_called()


@pytest.mark.parametrize("foreign_client", [False, True])
def test_svids_authenticate_with_openssl_mtls(tmp_path, foreign_client):
    import ssl

    ca = spiffe.create_authority("example.org")
    server = spiffe.issue_certificate(
        ca.key, ca.cert, "spiffe://example.org/server", 30
    )
    client_ca = spiffe.create_authority("example.org") if foreign_client else ca
    client = spiffe.issue_certificate(
        client_ca.key, client_ca.cert, "spiffe://example.org/client", 30
    )

    def context(issued, server_side):
        prefix = "server" if server_side else "client"
        cert_path, key_path = tmp_path / (prefix + ".crt"), tmp_path / (prefix + ".key")
        cert_path.write_text(issued.cert)
        key_path.write_bytes(issued.key)
        ctx = ssl.SSLContext(
            ssl.PROTOCOL_TLS_SERVER if server_side else ssl.PROTOCOL_TLS_CLIENT
        )
        ctx.load_cert_chain(cert_path, key_path)
        ctx.load_verify_locations(cadata=ca.cert)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    incoming, outgoing = (
        [ssl.MemoryBIO(), ssl.MemoryBIO()],
        [ssl.MemoryBIO(), ssl.MemoryBIO()],
    )
    peers = [
        context(server, True).wrap_bio(incoming[0], outgoing[0], server_side=True),
        context(client, False).wrap_bio(
            incoming[1], outgoing[1], server_hostname="unused"
        ),
    ]

    def handshake():
        done = [False, False]
        for _ in range(20):
            for i, peer in enumerate(peers):
                if not done[i]:
                    try:
                        peer.do_handshake()
                        done[i] = True
                    except ssl.SSLWantReadError:
                        pass
                data = outgoing[i].read()
                if data:
                    incoming[1 - i].write(data)
            if all(done):
                return
        raise AssertionError("TLS handshake did not finish")

    if foreign_client:
        with pytest.raises(ssl.SSLCertVerificationError):
            handshake()
    else:
        handshake()
        assert peers[0].getpeercert()["subjectAltName"] == (
            ("URI", "spiffe://example.org/client"),
        )
        assert peers[1].getpeercert()["subjectAltName"] == (
            ("URI", "spiffe://example.org/server"),
        )


@pytest.mark.parametrize(
    "domain",
    ["example.org/path", "example.org/", "user@example.org", "example.org:443"],
)
def test_ca_rejects_non_domain_input(domain):
    with pytest.raises(ValueError):
        spiffe.create_authority(domain)


@pytest.mark.parametrize("days", [0, 366, True, 1.5])
def test_rejects_invalid_lifetime(days):
    ca = spiffe.create_authority("example.org")
    with pytest.raises(ValueError):
        spiffe.issue_certificate(ca.key, ca.cert, IDENTITY, days)


@pytest.mark.parametrize("days", [1, 2, 7, 14, 15, 30, 365])
def test_short_lifetimes_validate_and_renew_before_expiration(issuer, days):
    target = resource(
        method={"kind": "spiffe", "spiffe_id": IDENTITY, "validity_days": days}
    )
    CertificatesController._validate_certificate(target.value)
    instance = secrets.Certificate(
        uuid=target.uuid,
        project_id=uuid.UUID(target.value["project_id"]),
        name="short-lived",
        email="admin@example.org",
        domains=[],
        method=secrets.SPIFFECertificateMethod(spiffe_id=IDENTITY, validity_days=days),
    )
    instance.validate()
    stored = backend.CertBotBackendClient._spiffe_certificate(target)
    assert not stored.is_under_threshold()
    window = min(14, days / 2)
    stored.expiration_at = datetime.datetime.now(
        datetime.timezone.utc
    ) + datetime.timedelta(days=window + 0.1)
    assert not stored.is_under_threshold()
    stored.expiration_at = datetime.datetime.now(
        datetime.timezone.utc
    ) + datetime.timedelta(days=window - 0.1)
    assert stored.is_under_threshold()


def test_boolean_lifetime_is_rejected_at_model_and_api_boundary():
    with pytest.raises(ValueError, match="boolean"):
        secrets.SPIFFECertificateMethod(spiffe_id=IDENTITY, validity_days=True)
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(
            resource(
                method={"kind": "spiffe", "spiffe_id": IDENTITY, "validity_days": True},
                expiration_threshold=0,
            ).value
        )


def test_spiffe_to_dns_requires_domains_at_api_and_model_boundaries():
    current = secrets.Certificate(
        uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="test",
        domains=[],
        email="admin@example.org",
        method=secrets.SPIFFECertificateMethod(spiffe_id=IDENTITY),
    )
    with pytest.raises(exceptions.ValidateException, match="at least one domain"):
        CertificatesController._validate_certificate(
            {"method": {"kind": "dns_core"}}, current
        )
    CertificatesController._validate_certificate(
        {"method": {"kind": "dns_core"}, "domains": ["example.org"]}, current
    )
    with pytest.raises(ValueError, match="at least one domain"):
        secrets.Certificate(
            uuid=current.uuid,
            project_id=current.project_id,
            name="test",
            domains=[],
            email="admin@example.org",
            method=secrets.DNSCoreCertificateMethod(),
        )


@pytest.mark.parametrize("persist_fails", [False, True])
def test_spiffe_to_dns_replaces_row_without_deleting_old_certificate(
    issuer, persist_fails
):
    target = resource()
    stored = backend.CertBotBackendClient._spiffe_certificate(target)
    ca = spiffe.create_authority("example.org")
    replacement = spiffe.issue_certificate(ca.key, ca.cert, IDENTITY, 30)
    changed = resource(
        uuid=str(target.uuid),
        project_id=target.value["project_id"],
        method={"kind": "dns_core"},
        domains=["example.org"],
    )
    with (
        patch.object(backend.acme, "get_or_create_client_private_key"),
        patch.object(backend.CertBotBackendClient, "_get_or_create_acme_client"),
        patch.object(
            backend.acme,
            "create_cert",
            return_value=(replacement.key, replacement.csr, replacement.cert),
        ),
        patch.object(storage.Certificate, "objects") as objects,
        patch.object(
            storage.Certificate,
            "update",
            side_effect=RuntimeError("persist failed") if persist_fails else None,
        ) as update,
        patch.object(storage.Certificate, "delete") as delete,
        patch.object(storage.Certificate, "save") as save,
    ):
        objects.get_one.return_value = stored
        client = backend.CertBotBackendClient(MagicMock(), "a@example.org", "/unused")
        if persist_fails:
            with pytest.raises(RuntimeError, match="persist failed"):
                client.update(changed)
        else:
            assert client.update(changed)["cert"] == replacement.cert
        update.assert_called_once()
        delete.assert_not_called()
        save.assert_not_called()
