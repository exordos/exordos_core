#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

"""The Rawstor 0.3 authorization profile layered on generic X.509-SVIDs."""

import re
import uuid

from cryptography import x509

from exordos_core.common import spiffe

TRUST_DOMAIN = "rawstor.local"
AUTHORITY_PROJECT = uuid.UUID(int=0)
PERMISSIONS_OID = x509.ObjectIdentifier("2.25.110097391626234565544022595913142399087")


def permissions(spiffe_id, object_id=None):
    """Validate a workload identity; only per-object clients receive permissions."""
    if spiffe.trust_domain(spiffe_id) != TRUST_DOMAIN:
        raise ValueError("Rawstor certificates use the installation trust domain")
    path = spiffe_id.removeprefix(f"spiffe://{TRUST_DOMAIN}/")
    if re.fullmatch(r"(?:mds|ost)/[0-9a-f-]{36}", path):
        value = path.split("/")[1]
        if not uuid.UUID(value).int or str(uuid.UUID(value)) != value:
            raise ValueError("Rawstor workload IDs must be canonical, non-nil UUIDs")
    elif re.fullmatch(r"admin/[0-9a-f-]{36}", path):
        value = path.split("/")[1]
        if not uuid.UUID(value).int or str(uuid.UUID(value)) != value:
            raise ValueError("Rawstor workload IDs must be canonical, non-nil UUIDs")
    else:
        match = re.fullmatch(r"account/([0-9a-f-]{36})/client/([0-9a-f-]{36})", path)
        if (
            not match
            or not uuid.UUID(match[1]).int
            or str(uuid.UUID(match[1])) != match[1]
            or not uuid.UUID(match[2]).int
            or str(uuid.UUID(match[2])) != match[2]
            or str(uuid.UUID(match[2])) != str(object_id)
        ):
            raise ValueError(
                "Rawstor clients must be scoped to a non-nil account and one volume"
            )
        # Manage is needed for repair, resize and structural reconciliation.
        text = f"rawstor-access\n0\n{object_id}=7\n".encode()
        # The extension value is a DER UTF8String inside X.509's OCTET STRING.
        return (
            x509.UnrecognizedExtension(
                PERMISSIONS_OID, b"\x0c" + bytes([len(text)]) + text
            ),
        )
    if object_id is not None:
        raise ValueError("Only a Rawstor client certificate has object permissions")
    return ()
