#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

"""Issue credentials as ordinary secrets and render them into agent targets.

The existing secret builder renews leaves. Storage/pool builders republish
changed TLS bundles even when topology, disk size and attachments are unchanged.
"""

import datetime
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from restalchemy.dm import filters

from exordos_core.common import rawstor_pki
from exordos_core.secret.dm import models as secrets

NAMESPACE = uuid.UUID("c714a18f-73ba-4d92-b6d1-f71ac08a1783")


def credential(path, object_id=None):
    identity = f"spiffe://{rawstor_pki.TRUST_DOMAIN}/{path}"
    method = secrets.RawstorCertificateMethod(
        spiffe_id=identity, validity_days=7, object_id=object_id
    )
    method.validate()
    cert_id = uuid.uuid5(NAMESPACE, identity)
    records = secrets.Certificate.objects.get_all(filters={"uuid": filters.EQ(cert_id)})
    if not records:
        secrets.Certificate(
            uuid=cert_id,
            project_id=rawstor_pki.AUTHORITY_PROJECT,
            name="rawstor-" + str(cert_id),
            email="rawstor@example.org",
            domains=[],
            method=method,
        ).save()
        return {"pending": True, "certificate_id": str(cert_id)}
    cert = records[0]
    if cert.method.dump_to_simple_view() != method.dump_to_simple_view():
        raise ValueError(
            "RawStor certificate identity or permissions changed unexpectedly"
        )
    if (
        not cert.key
        or not cert.cert
        or not cert.expiration_at
        or cert.expiration_at <= datetime.datetime.now(datetime.timezone.utc)
    ):
        return {"pending": True, "certificate_id": str(cert_id)}
    chain = x509.load_pem_x509_certificates(cert.cert.encode())
    if len(chain) != 2:
        raise ValueError("RawStor certificate requires the installation CA")
    return {
        "certificate_id": str(cert_id),
        "trust_domain": rawstor_pki.TRUST_DOMAIN,
        "cert": cert.cert,
        "key": cert.key,
        "ca": chain[1].public_bytes(serialization.Encoding.PEM).decode(),
    }


def bundle(kind, resource_uuid, agent, project=None):
    if agent is None:
        return {"service": {"pending": True}, "control": {"pending": True}}
    if kind == "mds":
        identity = "mds/" + str(uuid.uuid5(resource_uuid, "mds-witness"))
    elif kind == "ost":
        identity = "ost/" + str(resource_uuid)
    elif kind == "volume":
        identity = f"account/{project}/client/{resource_uuid}"
    else:
        raise ValueError("Unknown RawStor workload kind")
    return {
        "service": credential(identity, resource_uuid if kind == "volume" else None),
        "control": credential("admin/" + str(agent)),
    }


def refresh_targets(kinds, agent_ids=None, builder_uuid=None):
    """Refresh TLS snapshots without waiting for an unrelated model edit."""
    from gcl_sdk.agents.universal.dm import models as ua

    from exordos_core.compute.pool.dm import models as compute
    from exordos_core.storage.dm import models

    classes = {
        "storage_cluster": models.Cluster,
        "storage_node": models.Node,
        "pool_volume": compute.MachineVolume,
    }
    for kind in kinds:
        for target in ua.TargetResource.objects.get_all(
            filters={"kind": filters.EQ(kind)}
        ):
            if agent_ids is not None and target.agent not in agent_ids:
                continue
            rows = classes[kind].objects.get_all(
                filters={"uuid": filters.EQ(target.uuid)}
            )
            if not rows or (
                builder_uuid is not None and rows[0].builder != builder_uuid
            ):
                continue
            if kind == "pool_volume" and not target.value.get("storage_location"):
                continue
            new = rows[0].to_ua_resource()
            if new.hash != target.hash:
                target.update_value(new)
                target.update()


def prune_unused_certificates():
    """Stop renewing internal leaves after both desired and actual consumers disappear."""
    from gcl_sdk.agents.universal.dm import models as ua

    referenced = set()
    for model in (ua.TargetResource, ua.Resource):
        for resource in model.objects.get_all(
            filters={
                "kind": filters.In(("storage_cluster", "storage_node", "pool_volume"))
            }
        ):
            credentials = resource.value.get("rawstor_tls") or resource.value.get(
                "driver_spec", {}
            ).get("tls", {})
            for leaf in credentials.values():
                if isinstance(leaf, dict) and leaf.get("certificate_id"):
                    referenced.add(leaf["certificate_id"])
    for cert in secrets.Certificate.objects.get_all(
        filters={"project_id": filters.EQ(rawstor_pki.AUTHORITY_PROJECT)}
    ):
        if cert.method.KIND != "rawstor" or str(cert.uuid) in referenced:
            continue
        if cert.uuid == uuid.uuid5(NAMESPACE, cert.method.spiffe_id):
            cert.delete()
