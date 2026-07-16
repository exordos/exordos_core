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

import datetime
import types
import uuid as sys_uuid

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding

from exordos_core.agent.universal.drivers.secret.backend import cert


def _resource():
    return types.SimpleNamespace(
        uuid=sys_uuid.uuid4(),
        value={
            "name": "workspace-mail",
            "domains": ["workspace-mail.internal.example"],
            "method": {"kind": "internal_ca"},
            "expiration_threshold": 14,
        },
    )


def _leaf(fullchain):
    leaf_pem, _ = fullchain.split("-----END CERTIFICATE-----", 1)
    return x509.load_pem_x509_certificate(
        f"{leaf_pem}-----END CERTIFICATE-----".encode()
    )


def test_internal_ca_issues_hostname_verified_server_certificate():
    resource = _resource()

    issued = cert._issue_internal_certificate(resource)

    leaf = _leaf(issued.fullchain)
    ca = x509.load_pem_x509_certificate(issued.ca_cert.encode())
    ca.public_key().verify(
        leaf.signature,
        leaf.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf.signature_hash_algorithm,
    )
    assert (
        leaf.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        == resource.value["domains"]
    )
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert issued.ca_key is not None
    assert issued.to_resource_value()["ca_cert"] == issued.ca_cert
    assert "ca_key" not in issued.to_resource_value()


def test_internal_ca_rotation_keeps_ca_and_replaces_leaf():
    resource = _resource()
    first = cert._issue_internal_certificate(resource)

    rotated = cert._issue_internal_certificate(
        resource,
        first.ca_key.encode(),
        first.ca_cert,
    )

    assert rotated.ca_cert == first.ca_cert
    assert rotated.ca_key == first.ca_key
    assert (
        _leaf(rotated.fullchain).serial_number != _leaf(first.fullchain).serial_number
    )
    assert rotated.pkey != first.pkey


def test_internal_ca_rotates_before_a_new_leaf_would_outlive_it(monkeypatch):
    resource = _resource()
    monkeypatch.setattr(cert, "INTERNAL_CA_VALIDITY", datetime.timedelta(days=1))
    first = cert._issue_internal_certificate(resource)

    rotated = cert._issue_internal_certificate(
        resource,
        first.ca_key.encode(),
        first.ca_cert,
    )

    assert rotated.ca_cert != first.ca_cert
    assert rotated.ca_key != first.ca_key
