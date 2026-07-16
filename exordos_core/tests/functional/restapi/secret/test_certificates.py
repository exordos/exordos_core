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

import typing as tp

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.secret.dm import models as secret_models


class TestCertificatesUserApi:
    # Utils

    @staticmethod
    def _secret_cmp_shallow(
        cfg_foo: tp.Dict[str, tp.Any],
        cfg_bar: tp.Dict[str, tp.Any],
    ):
        return all(
            (cfg_foo[key] == cfg_bar[key])
            for key in (
                "uuid",
                "name",
                "method",
                "constructor",
                "email",
                "domains",
            )
        )

    # Tests

    def test_certificates_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["secret/certificates"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_certificates_add(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(cert, output)
        client.delete(
            client.build_resource_uri(["secret/certificates", output["uuid"]])
        )

    def test_internal_ca_certificate_method_is_accepted(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        cert = cert_factory(method=secret_models.InternalCACertificateMethod())
        url = client.build_collection_uri(["secret/certificates"])

        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert output["method"] == {"kind": "internal_ca"}
        client.delete(
            client.build_resource_uri(["secret/certificates", output["uuid"]])
        )

    def test_certificates_add_several(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        uuids = []
        url = client.build_collection_uri(["secret/certificates"])
        for i in range(3):
            cert = cert_factory()
            response = client.post(url, json=cert)
            output = response.json()
            assert response.status_code == 201
            assert self._secret_cmp_shallow(cert, output)
            uuids.append(output["uuid"])

        for uuid in uuids:
            response = client.get(
                client.build_resource_uri(["secret/certificates", uuid])
            )
            assert response.status_code == 200
            client.delete(client.build_resource_uri(["secret/certificates", uuid]))

    def test_certificates_update(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(cert, output)

        update = {"name": "foo-cert"}
        url = client.build_resource_uri(["secret/certificates", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-cert"

        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["name"] == "foo-cert"

        # cleanup
        client.delete(url)

    def test_certificates_update_status_new(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(cert, output)

        # Manually change status
        cert_obj = secret_models.Certificate.objects.get_one(
            filters={"uuid": output["uuid"]}
        )
        cert_obj.status = "IN_PROGRESS"
        cert_obj.update()

        url = client.build_resource_uri(["secret/certificates", output["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["status"] == "IN_PROGRESS"

        update = {"name": "foo-cert"}
        url = client.build_resource_uri(["secret/certificates", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-cert"
        assert output["status"] == "NEW"

        # cleanup
        client.delete(url)

    def test_certificates_delete(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(cert, output)

        url = client.build_resource_uri(["secret/certificates", output["uuid"]])
        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_certificates_update_unable_update_status(
        self,
        cert_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        cert = cert_factory()
        url = client.build_collection_uri(["secret/certificates"])
        response = client.post(url, json=cert)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(cert, output)

        update = {"status": "ACTIVE"}
        url = client.build_resource_uri(["secret/certificates", output["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(url, json=update)

        # cleanup
        client.delete(url)
