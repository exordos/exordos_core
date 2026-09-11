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
import typing as tp
import uuid as sys_uuid

from gcl_iam.tests.functional import clients as iam_clients
from gcl_sdk.agents.universal.clients.backend import exceptions as backend_exc
from gcl_sdk.agents.universal.dm import models as ua_models
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.agent.universal.drivers.secret.backend import cert as cert_backend
from exordos_core.agent.universal.drivers.secret.dm import models as driver_models
from exordos_core.common import constants as c
from exordos_core.secret import constants as sc
from exordos_core.secret.dm import models as secret_models
from exordos_core.tests.functional import stubs


class FakeCertBackendClient:
    def __init__(self):
        self._certs: dict[sys_uuid.UUID, dict] = {}

    def list(self, kind: str, **kwargs):
        result = []
        for uuid, value in self._certs.items():
            entry = dict(value)
            entry["uuid"] = str(uuid)
            result.append(entry)
        return result

    def create(self, resource: ua_models.Resource):
        if resource.uuid in self._certs:
            raise backend_exc.ResourceAlreadyExists(resource=resource)
        value = dict(resource.value)
        value["key"] = "fake-private-key"
        value["cert"] = "fake-certificate"
        value["expiration_at"] = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)
        ).strftime(c.DEFAULT_DATETIME_FORMAT)
        value["overcome_threshold"] = False
        value["status"] = "ACTIVE"
        self._certs[resource.uuid] = value
        return value

    def update(self, resource: ua_models.Resource):
        if resource.uuid not in self._certs:
            raise backend_exc.ResourceNotFound(resource=resource)
        value = dict(resource.value)
        value["key"] = self._certs[resource.uuid].get("key", "fake-private-key")
        value["cert"] = self._certs[resource.uuid].get("cert", "fake-certificate")
        value["expiration_at"] = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)
        ).strftime(c.DEFAULT_DATETIME_FORMAT)
        value["overcome_threshold"] = False
        value["status"] = "ACTIVE"
        self._certs[resource.uuid] = value
        return value

    def delete(self, resource: ua_models.Resource):
        self._certs.pop(resource.uuid, None)

    def get(self, resource: ua_models.Resource):
        if resource.uuid not in self._certs:
            raise backend_exc.ResourceNotFound(resource=resource)
        return dict(self._certs[resource.uuid])


class TestSecretServiceBuilder:
    def test_no_secrets(
        self,
        secret_agent_service,
        secret_builder,
    ):
        secret_builder._iteration()
        secret_agent_service._iteration()

    def test_create_secret(
        self,
        default_node: tp.Dict[str, tp.Any],
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        secret_agent_service,
        secret_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/secrets"])
        secret = secret_factory(value="my-opaque-value")

        response = client.post(url, json=secret)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        secret_builder._iteration()
        universal_scheduler._iteration()
        secret_agent_service._iteration()

        assert len(stubs.TargetResource.objects.get_all()) == 1
        secrets = stubs.Secret.objects.get_all()
        assert len(secrets) == 1
        assert secrets[0].status == "IN_PROGRESS"

        secret_builder._iteration()
        secret_agent_service._iteration()

        secrets = stubs.Secret.objects.get_all()
        assert len(secrets) == 1
        assert secrets[0].status == "ACTIVE"

        # The value the user supplied reached the data plane.
        stored = stubs.StorageSecret.objects.get_all()
        assert len(stored) == 1
        assert stored[0].value == "my-opaque-value"

        url = client.build_resource_uri(["secret/secrets", str(secrets[0].uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_secret_without_value_is_not_delivered(
        self,
        default_node: tp.Dict[str, tp.Any],
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        secret_agent_service,
        secret_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/secrets"])
        secret = secret_factory(value=None)

        response = client.post(url, json=secret)
        assert response.status_code == 201

        secret_builder._iteration()
        universal_scheduler._iteration()
        secret_agent_service._iteration()

        # Nothing to deliver, so the secret waits in NEW and the data
        # plane never hears about it.
        assert len(stubs.TargetResource.objects.get_all()) == 0
        assert len(stubs.StorageSecret.objects.get_all()) == 0
        assert stubs.Secret.objects.get_one().status == "NEW"

        # Setting a value releases it.
        secret_obj = stubs.Secret.objects.get_one()
        resource_url = client.build_resource_uri(
            ["secret/secrets", str(secret_obj.uuid)]
        )
        response = client.put(resource_url, json={"value": "late-value"})
        assert response.status_code == 200

        secret_builder._iteration()
        universal_scheduler._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        assert stubs.Secret.objects.get_one().status == "ACTIVE"
        assert stubs.StorageSecret.objects.get_one().value == "late-value"

    def test_delete_secret_without_value(
        self,
        default_node: tp.Dict[str, tp.Any],
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        secret_agent_service,
        secret_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/secrets"])
        secret = secret_factory(value=None)

        response = client.post(url, json=secret)
        assert response.status_code == 201

        secret_builder._iteration()
        universal_scheduler._iteration()

        # A secret that never reached the data plane is still deletable.
        secret_obj = stubs.Secret.objects.get_one()
        resource_url = client.build_resource_uri(
            ["secret/secrets", str(secret_obj.uuid)]
        )
        response = client.delete(resource_url)
        assert response.status_code == 204

        secret_builder._iteration()
        secret_agent_service._iteration()

        assert len(stubs.Secret.objects.get_all()) == 0
        assert len(stubs.TargetResource.objects.get_all()) == 0

    def test_create_secret_with_default_value(
        self,
        default_node: tp.Dict[str, tp.Any],
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        secret_agent_service,
        secret_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/secrets"])
        secret = secret_factory(value=None, default_value="default-value")

        response = client.post(url, json=secret)
        assert response.status_code == 201

        secret_builder._iteration()
        universal_scheduler._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        # The default is what the data plane gets while no value is set.
        assert stubs.Secret.objects.get_one().status == "ACTIVE"
        assert stubs.StorageSecret.objects.get_one().value == "default-value"

        # An explicit value takes over from the default.
        secret_obj = stubs.Secret.objects.get_one()
        resource_url = client.build_resource_uri(
            ["secret/secrets", str(secret_obj.uuid)]
        )
        response = client.put(resource_url, json={"value": "own-value"})
        assert response.status_code == 200

        secret_builder._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        assert stubs.Secret.objects.get_one().status == "ACTIVE"
        assert stubs.StorageSecret.objects.get_one().value == "own-value"

    def test_update_secret_value(
        self,
        default_node: tp.Dict[str, tp.Any],
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        secret_agent_service,
        secret_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(value="old-value")
        url = client.build_collection_uri(["secret/secrets"])
        client.post(url, json=secret)

        secret_builder._iteration()
        universal_scheduler._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        stored = stubs.StorageSecret.objects.get_one()
        assert stored.value == "old-value"

        secret_obj = stubs.Secret.objects.get_one()
        url = client.build_resource_uri(["secret/secrets", str(secret_obj.uuid)])
        response = client.put(url, json={"value": "new-value"})
        assert response.status_code == 200

        assert stubs.Secret.objects.get_one().status == "NEW"

        secret_builder._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        assert stubs.Secret.objects.get_one().status == "ACTIVE"
        assert stubs.StorageSecret.objects.get_one().value == "new-value"

    def test_rename_secret(
        self,
        default_node: tp.Dict[str, tp.Any],
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        secret_agent_service,
        secret_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(name="before", value="same-value")
        url = client.build_collection_uri(["secret/secrets"])
        client.post(url, json=secret)

        secret_builder._iteration()
        universal_scheduler._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        secret_obj = stubs.Secret.objects.get_one()
        assert secret_obj.status == "ACTIVE"

        # Renaming leaves the value alone, but the name is a target
        # field too, so the data plane still has to catch up.
        url = client.build_resource_uri(["secret/secrets", str(secret_obj.uuid)])
        response = client.put(url, json={"name": "after"})
        assert response.status_code == 200

        secret_builder._iteration()
        secret_agent_service._iteration()

        secret_builder._iteration()
        secret_agent_service._iteration()

        assert stubs.Secret.objects.get_one().status == "ACTIVE"
        stored = stubs.StorageSecret.objects.get_one()
        assert stored.meta["name"] == "after"
        assert stored.value == "same-value"


class TestPasswordServiceBuilder:
    def test_no_passwords(
        self,
        password_agent_service,
        password_builder,
    ):
        password_builder._iteration()
        password_agent_service._iteration()

    @pytest.mark.parametrize(
        "secret_method",
        [
            sc.SecretMethod.AUTO_HEX,
            sc.SecretMethod.AUTO_URL_SAFE,
            sc.SecretMethod.MANUAL,
        ],
    )
    def test_create_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        password_agent_service,
        password_builder,
        universal_scheduler,
        secret_method,
    ):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/passwords"])

        if secret_method == sc.SecretMethod.MANUAL:
            password = password_factory(
                method=secret_method, value="my-strong-password-value"
            )
        else:
            password = password_factory(method=secret_method)

        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        password_builder._iteration()
        universal_scheduler._iteration()
        password_agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        passwords = stubs.Password.objects.get_all()

        assert len(target_resources) == 1
        assert len(passwords) == 1
        password = passwords[0]

        assert password.status == "IN_PROGRESS"

        password_builder._iteration()
        password_agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        passwords = stubs.Password.objects.get_all()

        assert len(target_resources) == 1
        assert len(passwords) == 1
        password = passwords[0]

        assert password.status == "ACTIVE"

        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_update_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        password_agent_service,
        password_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()

        url = client.build_collection_uri(["secret/passwords"])
        client.post(url, json=password)

        password_builder._iteration()
        universal_scheduler._iteration()
        password_agent_service._iteration()

        password_builder._iteration()
        password_agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"
        old_value = password.value
        assert len(old_value) == 32

        default_length = 34
        update = {"default_length": default_length}
        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.put(url, json=update)
        assert response.status_code == 200

        password = stubs.Password.objects.get_one()
        assert password.status == "NEW"

        password_builder._iteration()
        password_agent_service._iteration()

        password_builder._iteration()
        password_agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"

        new_value = password.value
        assert old_value != new_value
        assert len(new_value) == default_length

        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_rename_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        password_agent_service,
        password_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory(name="before")
        url = client.build_collection_uri(["secret/passwords"])
        client.post(url, json=password)

        password_builder._iteration()
        universal_scheduler._iteration()
        password_agent_service._iteration()

        password_builder._iteration()
        password_agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"
        old_value = password.value

        # Renaming leaves the generated password alone, but the name is a
        # target field too, so the data plane still has to catch up.
        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.put(url, json={"name": "after"})
        assert response.status_code == 200

        password_builder._iteration()
        password_agent_service._iteration()

        password_builder._iteration()
        password_agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"
        assert password.value == old_value

        stored = stubs.StoragePassword.objects.get_one()
        assert stored.meta["name"] == "after"
        assert stored.value == old_value

    def test_delete_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        password_agent_service,
        password_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()

        url = client.build_collection_uri(["secret/passwords"])
        client.post(url, json=password)

        password_builder._iteration()
        universal_scheduler._iteration()
        password_agent_service._iteration()

        password_builder._iteration()
        password_agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"

        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

        password_builder._iteration()
        password_agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        assert len(target_resources) == 0

        resources = stubs.Resource.objects.get_all()
        assert len(resources) == 0

        passwords = stubs.Password.objects.get_all()
        assert len(passwords) == 0

        storage_passwords = stubs.StoragePassword.objects.get_all()
        assert len(storage_passwords) == 0


class TestCertificateServiceBuilder:
    def test_no_certs(
        self,
        cert_agent_service,
        certificate_builder,
    ):
        cert_agent_service._caps_drivers[0]._client = FakeCertBackendClient()
        certificate_builder._iteration()
        cert_agent_service._iteration()

    def test_create_certificate(
        self,
        default_node: tp.Dict[str, tp.Any],
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        cert_agent_service,
        certificate_builder,
        universal_scheduler,
    ):
        cert_agent_service._caps_drivers[0]._client = FakeCertBackendClient()

        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/certificates"])
        cert = cert_factory()
        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        certificate_builder._iteration()
        universal_scheduler._iteration()
        cert_agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        certificates = stubs.Certificate.objects.get_all()

        assert len(target_resources) == 1
        assert len(certificates) == 1
        certificate = certificates[0]

        assert certificate.status == "IN_PROGRESS"

        certificate_builder._iteration()
        cert_agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        certificates = stubs.Certificate.objects.get_all()

        assert len(target_resources) == 1
        assert len(certificates) == 1
        certificate = certificates[0]

        assert certificate.status == "ACTIVE"
        assert certificate.key == "fake-private-key"
        assert certificate.cert == "fake-certificate"

        url = client.build_resource_uri(["secret/certificates", str(certificate.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_update_certificate(
        self,
        default_node: tp.Dict[str, tp.Any],
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        cert_agent_service,
        certificate_builder,
        universal_scheduler,
    ):
        cert_agent_service._caps_drivers[0]._client = FakeCertBackendClient()

        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        client.post(url, json=cert)

        certificate_builder._iteration()
        universal_scheduler._iteration()
        cert_agent_service._iteration()

        certificate_builder._iteration()
        cert_agent_service._iteration()

        certificate = stubs.Certificate.objects.get_one()
        assert certificate.status == "ACTIVE"

        update = {"name": "updated-cert"}
        url = client.build_resource_uri(["secret/certificates", str(certificate.uuid)])
        response = client.put(url, json=update)
        assert response.status_code == 200

        certificate = stubs.Certificate.objects.get_one()
        assert certificate.status == "NEW"

        certificate_builder._iteration()
        cert_agent_service._iteration()

        certificate_builder._iteration()
        cert_agent_service._iteration()

        certificate = stubs.Certificate.objects.get_one()
        assert certificate.status == "ACTIVE"

        url = client.build_resource_uri(["secret/certificates", str(certificate.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_delete_certificate(
        self,
        default_node: tp.Dict[str, tp.Any],
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        cert_agent_service,
        certificate_builder,
        universal_scheduler,
    ):
        cert_agent_service._caps_drivers[0]._client = FakeCertBackendClient()

        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        client.post(url, json=cert)

        certificate_builder._iteration()
        universal_scheduler._iteration()
        cert_agent_service._iteration()

        certificate_builder._iteration()
        cert_agent_service._iteration()

        certificate = stubs.Certificate.objects.get_one()
        assert certificate.status == "ACTIVE"

        url = client.build_resource_uri(["secret/certificates", str(certificate.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

        certificate_builder._iteration()
        cert_agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        assert len(target_resources) == 0

        resources = stubs.Resource.objects.get_all()
        assert len(resources) == 0

        certificates = stubs.Certificate.objects.get_all()
        assert len(certificates) == 0

        storage_certificates = stubs.StorageCertificate.objects.get_all()
        assert len(storage_certificates) == 0


class TestCertBackendClient:
    """The real ACME backend client, not the fake the builder tests use."""

    @staticmethod
    def _cert_resource(uuid: sys_uuid.UUID, name: str) -> ua_models.TargetResource:
        cert = secret_models.Certificate(
            uuid=uuid,
            name=name,
            project_id=c.ZERO_UUID,
            constructor=secret_models.PlainSecretConstructor(),
            method=secret_models.DNSCoreCertificateMethod(),
            domains=["genesis-core.tech"],
            email="user@genesis-core.tech",
        )
        return cert.to_ua_resource(sc.CERTIFICATE_KIND)

    def test_rename_refreshes_meta_without_reissuing(self, user_api, tmp_path):
        uuid = sys_uuid.uuid4()
        resource = self._cert_resource(uuid, "before")

        driver_cert = driver_models.Certificate.from_cert_resource(
            resource,
            b"pkey-pem",
            b"csr-pem",
            "fullchain-pem",
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90),
        )
        driver_cert.save()

        client = cert_backend.CertBotBackendClient(
            dns_client=None,
            admin_email="admin@genesis-core.tech",
            private_key_path=str(tmp_path / "acme" / "client.key"),
        )

        # The domains are untouched and the cert is nowhere near its
        # expiration threshold, so no ACME call is made. The renamed
        # target field still has to reach the stored value, otherwise the
        # resource stays outdated forever.
        value = client.update(self._cert_resource(uuid, "after"))

        assert value["name"] == "after"
        assert value["cert"] == "fullchain-pem"

        stored = driver_models.Certificate.objects.get_one(
            filters={"uuid": dm_filters.EQ(uuid)}
        )
        assert stored.meta["name"] == "after"
        assert stored.fullchain == "fullchain-pem"

        stored.delete()
