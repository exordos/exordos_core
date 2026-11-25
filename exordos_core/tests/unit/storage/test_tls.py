#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch
import uuid

import pytest

from exordos_core.common import exceptions
from exordos_core.common import rawstor_pki
from exordos_core.common import spiffe
from exordos_core.secret.dm import models as secrets
from exordos_core.storage import tls
from exordos_core.user_api.secret.api.controllers import CertificatesController


def test_rawstor_client_signed_policy_roundtrips(tmp_path):
    pki = pytest.importorskip("rawstor.pki")

    volume, project = uuid.uuid4(), uuid.uuid4()
    identity = f"spiffe://rawstor.local/account/{project}/client/{volume}"
    authority = spiffe.create_authority(rawstor_pki.TRUST_DOMAIN)
    issued = spiffe.issue_certificate(
        authority.key,
        authority.cert,
        identity,
        7,
        extensions=rawstor_pki.permissions(identity, volume),
    )
    path = tmp_path / "cert.pem"
    path.write_text(issued.cert)
    info = pki.show(path)
    assert info.identity == identity
    assert info.access_role == "none"
    assert info.object_permissions == {str(volume): 7}


@pytest.mark.parametrize("kind", ["ost", "mds", "admin"])
def test_infrastructure_id_has_no_client_policy(kind):
    assert (
        rawstor_pki.permissions(f"spiffe://rawstor.local/{kind}/{uuid.uuid4()}") == ()
    )


def test_client_cannot_grant_another_volume():
    with pytest.raises(ValueError):
        rawstor_pki.permissions(
            f"spiffe://rawstor.local/account/{uuid.uuid4()}/client/{uuid.uuid4()}",
            uuid.uuid4(),
        )


def test_public_api_cannot_request_rawstor_profile():
    with pytest.raises(exceptions.ValidateException):
        CertificatesController._validate_certificate({"method": {"kind": "rawstor"}})


def test_pending_certificate_is_created_in_admin_project():
    with (
        patch.object(
            secrets.Certificate, "objects", SimpleNamespace(get_all=lambda **_: [])
        ),
        patch.object(secrets.Certificate, "save") as save,
    ):
        assert tls.credential("admin/" + str(uuid.uuid4()))["pending"] is True
    save.assert_called_once()


def test_renewed_certificate_changes_bundle_without_identity_change():
    identity = "spiffe://rawstor.local/admin/" + str(uuid.uuid4())
    ca = spiffe.create_authority(rawstor_pki.TRUST_DOMAIN)
    method = secrets.RawstorCertificateMethod(spiffe_id=identity, validity_days=7)
    row = SimpleNamespace(method=method)
    with patch.object(
        secrets.Certificate, "objects", SimpleNamespace(get_all=lambda **_: [row])
    ):
        bundles = []
        for _ in range(2):
            issued = spiffe.issue_certificate(ca.key, ca.cert, identity, 7)
            row.cert, row.key, row.expiration_at = (
                issued.cert,
                issued.key.decode(),
                issued.expiration_at,
            )
            bundles.append(
                tls.credential(identity.removeprefix("spiffe://rawstor.local/"))
            )
        assert bundles[0]["key"] != bundles[1]["key"]
        assert bundles[0]["ca"] == bundles[1]["ca"]


def test_mds_identity_matches_topology_witness():
    cluster, agent = uuid.uuid4(), uuid.uuid4()
    with patch.object(tls, "credential", return_value={}) as issue:
        tls.bundle("mds", cluster, agent)
    assert issue.call_args_list[0].args[0] == "mds/" + str(
        uuid.uuid5(cluster, "mds-witness")
    )


def test_rotation_republishes_volume_target_without_a_disk_edit():
    from gcl_sdk.agents.universal.dm import models as ua

    from exordos_core.compute.pool.dm import models as compute

    agent = uuid.uuid4()
    target = SimpleNamespace(
        uuid=uuid.uuid4(),
        agent=agent,
        value={"storage_location": "mds://host:7776/"},
        hash="old",
        update_value=Mock(),
        update=Mock(),
    )
    renewed = SimpleNamespace(hash="new")
    volume = SimpleNamespace(to_ua_resource=Mock(return_value=renewed))
    with (
        patch.object(
            ua.TargetResource, "objects", SimpleNamespace(get_all=lambda **_: [target])
        ),
        patch.object(
            compute.MachineVolume,
            "objects",
            SimpleNamespace(get_all=lambda **_: [volume]),
        ),
    ):
        tls.refresh_targets(("pool_volume",), {agent})
    target.update_value.assert_called_once_with(renewed)
    target.update.assert_called_once()


def test_unreferenced_internal_leaf_is_deleted_but_active_leaf_is_kept():
    from gcl_sdk.agents.universal.dm import models as ua

    rows = []
    for _ in range(2):
        identity = "spiffe://rawstor.local/admin/" + str(uuid.uuid4())
        rows.append(
            SimpleNamespace(
                uuid=uuid.uuid5(tls.NAMESPACE, identity),
                method=SimpleNamespace(KIND="rawstor", spiffe_id=identity),
                delete=Mock(),
            )
        )
    target = SimpleNamespace(
        value={"rawstor_tls": {"control": {"certificate_id": str(rows[0].uuid)}}}
    )
    with (
        patch.object(
            ua.TargetResource, "objects", SimpleNamespace(get_all=lambda **_: [target])
        ),
        patch.object(ua.Resource, "objects", SimpleNamespace(get_all=lambda **_: [])),
        patch.object(
            secrets.Certificate, "objects", SimpleNamespace(get_all=lambda **_: rows)
        ),
    ):
        tls.prune_unused_certificates()
    rows[0].delete.assert_not_called()
    rows[1].delete.assert_called_once()


def test_node_delivers_private_tls_without_exposing_it_in_public_model():
    from gcl_sdk.agents.universal.drivers import rawstor_node
    from gcl_sdk.agents.universal.drivers import rawstor_specs

    from exordos_core.storage.dm import models

    node = models.Node(
        uuid=uuid.uuid4(),
        cluster=uuid.uuid4(),
        agent=uuid.uuid4(),
        driver_spec=rawstor_specs.RawstorNodeDriverSpec(
            endpoint="ost://node:7777", failure_domain_path="host1"
        ),
    )
    credentials = {"service": {"pending": True}, "control": {"pending": True}}
    with patch.object(tls, "bundle", return_value=credentials):
        resource = node.to_ua_resource()
    assert resource.value["driver_spec"]["tls"] == credentials
    actual = rawstor_node.MetaStorageNode.from_ua_resource(resource)
    assert actual.driver_spec.tls == credentials
    restored = models.Node.from_ua_resource(resource)
    assert "tls" not in restored.driver_spec.dump_to_simple_view()


def test_rawstor_issuer_rejects_non_admin_project_before_loading_ca():
    from exordos_core.agent.universal.drivers.secret.backend import cert as backend

    value = {
        "method": {
            "kind": "rawstor",
            "spiffe_id": "spiffe://rawstor.local/admin/" + str(uuid.uuid4()),
        },
        "project_id": str(uuid.uuid4()),
    }
    with pytest.raises(ValueError, match="administrator project"):
        backend.CertBotBackendClient._spiffe_certificate(SimpleNamespace(value=value))


def test_rawstor_profile_uses_generic_authority_and_renews_signed_permissions(tmp_path):
    pki = pytest.importorskip("rawstor.pki")
    from exordos_core.agent.universal.drivers.secret.backend import cert as backend
    from exordos_core.agent.universal.drivers.secret.dm import models as storage

    volume, account = uuid.uuid4(), uuid.uuid4()
    identity = f"spiffe://rawstor.local/account/{account}/client/{volume}"
    authority = spiffe.create_authority(rawstor_pki.TRUST_DOMAIN)
    resource = SimpleNamespace(
        uuid=uuid.uuid4(),
        value={
            "project_id": str(rawstor_pki.AUTHORITY_PROJECT),
            "method": {
                "kind": "rawstor",
                "spiffe_id": identity,
                "validity_days": 7,
                "object_id": str(volume),
            },
            "expiration_threshold": 14,
        },
    )
    with patch.object(
        storage.SPIFFEAuthority,
        "objects",
        SimpleNamespace(
            get_one=lambda **_: SimpleNamespace(
                key=authority.key.decode(), cert=authority.cert
            )
        ),
    ):
        first = backend.CertBotBackendClient._spiffe_certificate(resource)
        renewed = backend.CertBotBackendClient._spiffe_certificate(resource)
    assert first.pkey != renewed.pkey
    for cert in (first, renewed):
        path = tmp_path / "leaf.pem"
        path.write_text(cert.fullchain)
        info = pki.show(path)
        assert info.identity == identity
        assert info.object_permissions == {str(volume): 7}
        assert cert.fullchain.endswith(authority.cert)
        assert not cert.is_under_threshold()


def test_volume_bundle_never_issues_hypervisor_admin_credentials():
    volume, project = uuid.uuid4(), uuid.uuid4()
    with patch.object(tls, "credential", return_value={"key": "volume-key"}) as issue:
        bundle = tls.bundle("volume", volume, uuid.uuid4(), project)
    assert bundle == {"service": {"key": "volume-key"}}
    issue.assert_called_once_with(f"account/{project}/client/{volume}", volume)
