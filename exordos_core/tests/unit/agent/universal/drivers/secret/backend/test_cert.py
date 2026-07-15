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
import typing as tp
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models
import pytest
from restalchemy.storage import exceptions as ra_exc

from exordos_core.agent.universal.drivers.secret.backend.cert import (
    CertBotBackendClient,
)


def _make_resource(
    kind: str,
    uuid: tp.Optional[sys_uuid.UUID] = None,
    value: tp.Optional[dict] = None,
):
    uuid = uuid or sys_uuid.uuid4()
    value = value or {"uuid": str(uuid)}
    return models.Resource.from_value(value, kind, frozenset(value.keys()))


KIND = "certificate"
PKEY_BYTES = b"pkey_data"
CSR_BYTES = b"csr_data"
FULLCHAIN_STR = "fullchain_data"
EXPIRATION_AT = datetime.datetime(2027, 1, 1, tzinfo=datetime.timezone.utc)


class TestCertBotBackendClient:
    @pytest.fixture(autouse=True)
    def _setup_mocks(
        self,
        mock_acme_get_or_create_key,
        mock_acme_get_client,
    ):
        self.mock_dns_client = MagicMock()
        self.client = CertBotBackendClient(
            dns_client=self.mock_dns_client,
            admin_email="admin@example.com",
            private_key_path="/fake/path",
        )

    @pytest.fixture
    def mock_acme_get_or_create_key(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.acme.get_or_create_client_private_key"
        ) as m:
            m.return_value = MagicMock()
            yield m

    @pytest.fixture
    def mock_acme_get_client(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.acme.get_acme_client"
        ) as m:
            m.return_value = MagicMock()
            yield m

    @pytest.fixture
    def mock_cert_objects(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.driver_dm.Certificate.objects"
        ) as m:
            yield m

    @pytest.fixture
    def mock_from_cert_resource(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.driver_dm.Certificate.from_cert_resource"
        ) as m:
            yield m

    @pytest.fixture
    def mock_acme_create_cert(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.acme.create_cert"
        ) as m:
            yield m

    @pytest.fixture
    def mock_acme_renew_cert(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.acme.renew_cert"
        ) as m:
            yield m

    @pytest.fixture
    def mock_acme_revoke_cert(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.acme.revoke_cert"
        ) as m:
            yield m

    @pytest.fixture
    def mock_x509_load(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.x509.load_pem_x509_certificate"
        ) as m:
            cert_mock = MagicMock()
            cert_mock.not_valid_after_utc = EXPIRATION_AT
            m.return_value = cert_mock
            yield m

    @pytest.fixture
    def mock_from_ua_resource(self):
        with patch(
            "exordos_core.agent.universal.drivers.secret.backend.cert.secret_dm.Certificate.from_ua_resource"
        ) as m:
            yield m

    # --- get ---

    def test_get_success(self, mock_cert_objects):
        resource = _make_resource(KIND)
        cert_mock = MagicMock()
        cert_mock.to_resource_value.return_value = {"key": "val"}
        mock_cert_objects.get_one.return_value = cert_mock

        result = self.client.get(resource)

        assert result == {"key": "val"}
        mock_cert_objects.get_one.assert_called_once()

    def test_get_not_found(self, mock_cert_objects):
        resource = _make_resource(KIND)
        mock_cert_objects.get_one.side_effect = ra_exc.RecordNotFound(
            model="Certificate", filters={"uuid": str(resource.uuid)}
        )

        from gcl_sdk.agents.universal.clients.backend import exceptions as client_exc

        with pytest.raises(client_exc.ResourceNotFound):
            self.client.get(resource)

    # --- create ---

    def test_create_success(
        self,
        mock_cert_objects,
        mock_acme_create_cert,
        mock_x509_load,
        mock_from_cert_resource,
        mock_from_ua_resource,
    ):
        resource = _make_resource(KIND)
        mock_cert_objects.get_one.side_effect = ra_exc.RecordNotFound(
            model="Certificate", filters={"uuid": str(resource.uuid)}
        )

        cert_model_mock = MagicMock()
        cert_model_mock.domains = ["example.com"]
        mock_from_ua_resource.return_value = cert_model_mock

        mock_acme_create_cert.return_value = (PKEY_BYTES, CSR_BYTES, FULLCHAIN_STR)

        driver_cert_mock = MagicMock()
        driver_cert_mock.to_resource_value.return_value = {
            "key": "val",
            "status": "ACTIVE",
        }
        mock_from_cert_resource.return_value = driver_cert_mock

        result = self.client.create(resource)

        assert result == {"key": "val", "status": "ACTIVE"}
        mock_acme_create_cert.assert_called_once()
        mock_from_cert_resource.assert_called_once_with(
            resource, PKEY_BYTES, CSR_BYTES, FULLCHAIN_STR, EXPIRATION_AT
        )
        driver_cert_mock.save.assert_called_once()

    def test_create_already_exists(self, mock_cert_objects):
        resource = _make_resource(KIND)
        mock_cert_objects.get_one.return_value = MagicMock()

        from gcl_sdk.agents.universal.clients.backend import exceptions as client_exc

        with pytest.raises(client_exc.ResourceAlreadyExists):
            self.client.create(resource)

    def test_create_acme_fails(
        self,
        mock_cert_objects,
        mock_acme_create_cert,
        mock_from_ua_resource,
    ):
        resource = _make_resource(KIND)
        mock_cert_objects.get_one.side_effect = ra_exc.RecordNotFound(
            model="Certificate", filters={"uuid": str(resource.uuid)}
        )

        cert_model_mock = MagicMock()
        cert_model_mock.domains = ["example.com"]
        mock_from_ua_resource.return_value = cert_model_mock

        mock_acme_create_cert.side_effect = Exception("ACME error")

        with pytest.raises(Exception, match="ACME error"):
            self.client.create(resource)

    # --- update ---

    def test_update_not_found(self, mock_cert_objects):
        resource = _make_resource(KIND)
        mock_cert_objects.get_one.side_effect = ra_exc.RecordNotFound(
            model="Certificate", filters={"uuid": str(resource.uuid)}
        )

        from gcl_sdk.agents.universal.clients.backend import exceptions as client_exc

        with pytest.raises(client_exc.ResourceNotFound):
            self.client.update(resource)

    def test_update_domains_changed(self, mock_cert_objects):
        resource = _make_resource(
            KIND, value={"uuid": str(sys_uuid.uuid4()), "domains": ["new.example.com"]}
        )
        cert_mock = MagicMock()
        cert_mock.__getitem__.side_effect = lambda key: {
            "meta": {"domains": ["old.example.com"]}
        }[key]
        mock_cert_objects.get_one.return_value = cert_mock

        with pytest.raises(NotImplementedError, match="Domains changed"):
            self.client.update(resource)

    def test_update_within_threshold(self, mock_cert_objects):
        resource = _make_resource(
            KIND,
            value={
                "uuid": str(sys_uuid.uuid4()),
                "domains": ["example.com"],
            },
        )
        cert_mock = MagicMock()
        cert_mock.__getitem__.side_effect = lambda key: {
            "meta": {"domains": ["example.com"], "expiration_threshold": 14}
        }[key]
        cert_mock.is_under_threshold.return_value = False
        cert_mock.to_resource_value.return_value = {"key": "val", "status": "ACTIVE"}
        mock_cert_objects.get_one.return_value = cert_mock

        result = self.client.update(resource)

        assert result == {"key": "val", "status": "ACTIVE"}
        cert_mock.to_resource_value.assert_called_once()

    def test_update_renew(
        self,
        mock_cert_objects,
        mock_acme_renew_cert,
        mock_x509_load,
        mock_from_cert_resource,
    ):
        resource = _make_resource(
            KIND,
            value={
                "uuid": str(sys_uuid.uuid4()),
                "domains": ["example.com"],
            },
        )
        cert_mock = MagicMock()
        cert_mock.__getitem__.side_effect = lambda key: {
            "meta": {"domains": ["example.com"], "expiration_threshold": 14}
        }[key]
        cert_mock.is_under_threshold.return_value = True
        cert_mock.pkey = "existing_pkey"
        mock_cert_objects.get_one.return_value = cert_mock

        mock_acme_renew_cert.return_value = (PKEY_BYTES, CSR_BYTES, FULLCHAIN_STR)

        driver_cert_mock = MagicMock()
        driver_cert_mock.to_resource_value.return_value = {
            "key": "new_val",
            "status": "ACTIVE",
        }
        mock_from_cert_resource.return_value = driver_cert_mock

        result = self.client.update(resource)

        assert result == {"key": "new_val", "status": "ACTIVE"}
        mock_acme_renew_cert.assert_called_once_with(
            self.client._get_or_create_acme_client(),
            self.mock_dns_client,
            resource.value["domains"],
            cert_mock.pkey.encode(),
        )
        cert_mock.delete.assert_called_once()
        driver_cert_mock.save.assert_called_once()

    def test_update_renew_acme_fails(
        self,
        mock_cert_objects,
        mock_acme_renew_cert,
    ):
        resource = _make_resource(
            KIND,
            value={
                "uuid": str(sys_uuid.uuid4()),
                "domains": ["example.com"],
            },
        )
        cert_mock = MagicMock()
        cert_mock.__getitem__.side_effect = lambda key: {
            "meta": {"domains": ["example.com"], "expiration_threshold": 14}
        }[key]
        cert_mock.is_under_threshold.return_value = True
        cert_mock.pkey = "existing_pkey"
        mock_cert_objects.get_one.return_value = cert_mock

        mock_acme_renew_cert.side_effect = Exception("ACME renew error")

        with pytest.raises(Exception, match="ACME renew error"):
            self.client.update(resource)

    # --- list ---

    def test_list_success(self, mock_cert_objects):
        cert1 = MagicMock()
        cert1.to_resource_value.return_value = {"uuid": "u1"}
        cert2 = MagicMock()
        cert2.to_resource_value.return_value = {"uuid": "u2"}
        mock_cert_objects.get_all.return_value = [cert1, cert2]

        result = self.client.list(KIND)

        assert result == [{"uuid": "u1"}, {"uuid": "u2"}]
        mock_cert_objects.get_all.assert_called_once()

    def test_list_empty(self, mock_cert_objects):
        mock_cert_objects.get_all.return_value = []

        result = self.client.list(KIND)

        assert result == []

    # --- delete ---

    def test_delete_success(
        self,
        mock_cert_objects,
        mock_acme_revoke_cert,
    ):
        resource = _make_resource(KIND)
        cert_mock = MagicMock()
        cert_mock.fullchain = FULLCHAIN_STR
        cert_mock.to_resource_value.return_value = {"key": "val"}
        mock_cert_objects.get_one.return_value = cert_mock

        self.client.delete(resource)

        mock_acme_revoke_cert.assert_called_once_with(
            self.client._get_or_create_acme_client(),
            FULLCHAIN_STR,
        )
        cert_mock.delete.assert_called_once()

    def test_delete_not_found(self, mock_cert_objects):
        resource = _make_resource(KIND)
        mock_cert_objects.get_one.side_effect = ra_exc.RecordNotFound(
            model="Certificate", filters={"uuid": str(resource.uuid)}
        )

        from gcl_sdk.agents.universal.clients.backend import exceptions as client_exc

        with pytest.raises(client_exc.ResourceNotFound):
            self.client.delete(resource)
