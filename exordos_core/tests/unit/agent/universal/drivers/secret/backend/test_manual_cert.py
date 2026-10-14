#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import uuid

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from gcl_sdk.agents.universal.dm import models
import pytest
from restalchemy.storage import exceptions as ra_exc
import yaml

from exordos_core.agent.universal.drivers.secret.backend import cert as backend
from exordos_core.agent.universal.drivers.secret.dm import models as storage
from exordos_core.common import exceptions
from exordos_core.elements.dm import utils
from exordos_core.secret.builders import service
from exordos_core.secret.dm import models as secrets
from exordos_core.user_api.secret.api.controllers import CertificatesController


@pytest.fixture
def example():
    path = (
        Path(__file__).resolve().parents[8]
        / "exordos/manifests/examples/certificates.yaml"
    )
    manifest = yaml.safe_load(path.read_text())
    utils.validate_manifest(
        utils.mutate_manifest(manifest, utils.load_full_manifest_schema()),
        utils.load_full_manifest_schema(),
    )
    value = manifest["resources"]["$core.secret.certificates"]["manual_cert"]
    value["project_id"] = uuid.UUID(value["project_id"])
    value["method"] = secrets.ManualCertificateMethod()
    return value


def resource(value):
    instance = service.Certificate(**value)
    fields = instance.get_resource_target_fields()
    view = instance.dump_to_simple_view()
    return models.Resource.from_value(view, "certificate", frozenset(fields))


@pytest.mark.parametrize("change_domains", [False, True])
def test_manual_example_provisions_and_updates_without_acme(example, change_domains):
    CertificatesController._validate_certificate(example)
    target = resource(example)
    assert {"cert", "key"} <= service.Certificate(
        **example
    ).get_resource_target_fields()
    with (
        patch.object(backend.acme, "get_or_create_client_private_key"),
        patch.object(backend.acme, "create_cert") as issue,
        patch.object(backend.acme, "renew_cert") as renew,
        patch.object(backend.acme, "revoke_cert") as revoke,
        patch.object(storage.Certificate, "objects") as objects,
        patch.object(storage.Certificate, "save") as save,
        patch.object(storage.Certificate, "update") as persist,
        patch.object(storage.Certificate, "delete") as delete,
    ):
        client = backend.CertBotBackendClient(
            MagicMock(), "admin@example.com", "/unused"
        )
        objects.get_one.side_effect = ra_exc.RecordNotFound(
            model="Certificate", filters={}
        )
        created = client.create(target)
        save.assert_called_once()
        persist.assert_not_called()
        delete.assert_not_called()
        assert created["overcome_threshold"] is False
        assert created["cert"] == example["cert"]
        assert created["key"] == example["key"]
        assert created["expiration_at"].startswith("2036-01-01")
        objects.get_one.side_effect = None
        objects.get_one.return_value = client._manual_certificate(target)
        new_key = ec.generate_private_key(ec.SECP256R1())
        leaf = x509.load_pem_x509_certificate(example["cert"].encode())
        replacement = (
            x509.CertificateBuilder()
            .subject_name(leaf.subject)
            .issuer_name(leaf.issuer)
            .public_key(new_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2026, 1, 1, tzinfo=timezone.utc))
            .not_valid_after(datetime(2037, 1, 1, tzinfo=timezone.utc))
            .sign(new_key, hashes.SHA256())
        )
        changed = dict(
            example,
            name="replacement",
            domains=["new.example.com"] if change_domains else example["domains"],
            cert=replacement.public_bytes(serialization.Encoding.PEM).decode(),
            key=new_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        )
        updated = client.update(resource(changed))
        persist.assert_called_once()
        save.assert_called_once()
        delete.assert_not_called()
        assert updated["name"] == "replacement"
        assert updated["domains"] == changed["domains"]
        assert updated["cert"] == changed["cert"]
        assert updated["key"] == changed["key"]
        assert updated["expiration_at"].startswith("2037-01-01")
        objects.get_one.return_value = client._manual_certificate(resource(changed))
        client.delete(target)
        delete.assert_called_once()
        issue.assert_not_called()
        renew.assert_not_called()
        revoke.assert_not_called()


@pytest.mark.parametrize("field", ["cert", "key"])
def test_manual_requires_both_pem_fields(example, field):
    example.pop(field)
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(example)


def test_manual_rejects_mismatched_key(example):
    key = ec.generate_private_key(ec.SECP256R1())
    example["key"] = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(example)


def test_manual_rejects_invalid_pem(example):
    example["cert"] = "invalid PEM"
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(example)


def test_dns_core_rejects_imported_pem(example):
    example["method"] = {"kind": "dns_core"}
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(example)


def test_manual_partial_update_uses_existing_pair(example):
    current = secrets.Certificate(**example)
    CertificatesController._validate_certificate({"name": "renamed"}, current)
    CertificatesController._validate_certificate({"cert": example["cert"]}, current)


@pytest.mark.parametrize("method", [None, "manual", 123, [], True])
@pytest.mark.parametrize("updating", [False, True])
def test_certificate_rejects_invalid_method_type(example, method, updating):
    current = secrets.Certificate(**example) if updating else None
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate({"method": method}, current)


@pytest.mark.parametrize("field", ["key", "cert"])
@pytest.mark.parametrize("value", [123, True, ["PEM"], {"pem": "value"}])
@pytest.mark.parametrize("updating", [False, True])
def test_manual_rejects_invalid_pem_type(example, field, value, updating):
    current = secrets.Certificate(**example) if updating else None
    values = {field: value} if updating else dict(example, **{field: value})
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(values, current)


@pytest.mark.parametrize("field", ["key", "cert"])
@pytest.mark.parametrize("value", [None, "invalid PEM"])
@pytest.mark.parametrize("from_manifest", [False, True])
def test_manual_model_rejects_invalid_pair(example, field, value, from_manifest):
    example[field] = value
    if from_manifest:
        example["method"] = {"kind": "manual"}
    with pytest.raises(ValueError):
        if from_manifest:
            secrets.Certificate.restore_from_simple_view(**example)
        else:
            secrets.Certificate(**example)


def test_manual_model_rejects_mismatched_pair(example):
    key = ec.generate_private_key(ec.SECP256R1())
    example["key"] = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with pytest.raises(ValueError, match="do not match"):
        secrets.Certificate(**example)


def test_manual_model_update_rejects_invalid_pair_before_storage(example):
    current = secrets.Certificate(**example)
    current.cert = "invalid PEM"
    with patch.object(secrets.Certificate, "_get_engine") as engine:
        with pytest.raises(ra_exc.UnknownStorageException):
            current.update()
        engine.assert_not_called()


def test_manual_builder_preserves_pending_replacement(example):
    current = service.Certificate(**example)
    actual = service.Certificate(**dict(example, cert=example["cert"] + "\n"))
    actual.expiration_at = x509.load_pem_x509_certificate(
        example["cert"].encode()
    ).not_valid_after_utc
    with patch.object(service.Certificate, "save"):
        service.CertificateBuilder().actualize_outdated_instance(current, actual)
    assert current.cert == example["cert"]
    assert current.key == example["key"]
    assert current.expiration_at == actual.expiration_at


@pytest.mark.parametrize(
    "suffix",
    ["garbage", "-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----"],
)
def test_manual_rejects_malformed_chain(example, suffix):
    example["cert"] += suffix
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(example)


def test_manual_rejects_unrelated_chain(example):
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = x509.load_pem_x509_certificate(example["cert"].encode())
    unrelated = (
        x509.CertificateBuilder()
        .subject_name(leaf.subject)
        .issuer_name(leaf.issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(leaf.not_valid_before_utc)
        .not_valid_after(leaf.not_valid_after_utc)
        .sign(key, hashes.SHA256())
    )
    example["cert"] += unrelated.public_bytes(serialization.Encoding.PEM).decode()
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate(example)


def test_manual_accepts_valid_chain(example):
    key = serialization.load_pem_private_key(example["key"].encode(), password=None)
    leaf = x509.load_pem_x509_certificate(example["cert"].encode())
    issuer = (
        x509.CertificateBuilder()
        .subject_name(leaf.issuer)
        .issuer_name(leaf.issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(leaf.not_valid_before_utc)
        .not_valid_after(leaf.not_valid_after_utc)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    example["cert"] += issuer.public_bytes(serialization.Encoding.PEM).decode()
    CertificatesController._validate_certificate(example)


def test_manual_reports_unsupported_algorithm_as_validation_error(example):
    with patch.object(
        secrets.serialization,
        "load_pem_private_key",
        side_effect=UnsupportedAlgorithm("unsupported"),
    ):
        with pytest.raises(exceptions.ValidateException):
            CertificatesController._validate_certificate(example)


def test_manual_failed_replacement_does_not_delete_stored_certificate(example):
    target = resource(example)
    with (
        patch.object(backend.acme, "get_or_create_client_private_key"),
        patch.object(storage.Certificate, "objects") as objects,
        patch.object(
            storage.Certificate, "update", side_effect=RuntimeError("write failed")
        ),
        patch.object(storage.Certificate, "delete") as delete,
        patch.object(storage.Certificate, "save") as save,
    ):
        client = backend.CertBotBackendClient(
            MagicMock(), "admin@example.com", "/unused"
        )
        objects.get_one.return_value = client._manual_certificate(target)
        with pytest.raises(RuntimeError, match="write failed"):
            client.update(target)
        delete.assert_not_called()
        save.assert_not_called()
