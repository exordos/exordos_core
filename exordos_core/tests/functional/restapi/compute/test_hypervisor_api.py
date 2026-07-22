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
import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
from gcl_sdk.agents.universal.api import crypto as ua_crypto
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
import pytest
from restalchemy.dm import filters as dm_filters


class TestHypervisorUserApi:
    # Utils

    @staticmethod
    def _node_cmp_shallow(
        node_foo: tp.Dict[str, tp.Any],
        node_bar: tp.Dict[str, tp.Any],
    ):
        return (
            all(
                (node_foo[key] == node_bar[key])
                for key in (
                    "uuid",
                    "name",
                    "cores",
                    "ram",
                    "status",
                    "node_type",
                )
            )
            and node_foo["disk_spec"] == node_bar["disk_spec"]
        )

    @staticmethod
    def _hypervisor_cmp_shallow(
        hypervisor_foo: tp.Dict[str, tp.Any],
        hypervisor_bar: tp.Dict[str, tp.Any],
    ):
        return (
            all(
                (hypervisor_foo[key] == hypervisor_bar[key])
                for key in (
                    "uuid",
                    "name",
                    "machine_type",
                    "avail_cores",
                    "avail_ram",
                    "all_cores",
                    "all_ram",
                    "cores_ratio",
                    "ram_ratio",
                )
            )
            and hypervisor_foo["driver_spec"] == hypervisor_bar["driver_spec"]
        )

    def test_hypervisors_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_hypervisors_add(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        hypervisor = pool_factory()
        hypervisor.pop("status", None)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])
        response = client.post(url, json=hypervisor)
        output = response.json()

        assert response.status_code == 201
        assert self._hypervisor_cmp_shallow(hypervisor, output)
        assert output["status"] == ua_pool.MachinePoolStatus.DISABLED.value

        client.delete(
            client.build_resource_uri(["compute", "hypervisors", hypervisor["uuid"]])
        )

    def test_hypervisors_add_several(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        hypervisors = []
        for i in range(3):
            hypervisor = pool_factory(
                name=f"hypervisor_{i}",
                driver_spec={
                    "kind": "libvirt",
                    "connection_uri": f"qemu+tcp://10.20.0.{str(i + 1)}/system",
                },
            )
            hypervisor.pop("status", None)
            hypervisors.append(hypervisor)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])

        for hypervisor in hypervisors:
            response = client.post(url, json=hypervisor)
            output = response.json()
            assert response.status_code == 201
            assert self._hypervisor_cmp_shallow(hypervisor, output)

        response = client.get(url)
        output = response.json()
        assert len(output) == len(hypervisors)
        for hypervisor in hypervisors:
            assert any(
                self._hypervisor_cmp_shallow(hypervisor, item) for item in output
            )

        for hypervisor in hypervisors:
            client.delete(
                client.build_resource_uri(
                    ["compute", "hypervisors", hypervisor["uuid"]]
                )
            )

    def test_hypervisors_update(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        hypervisor = pool_factory()
        hypervisor.pop("nodes", None)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])

        response = client.post(url, json=hypervisor)
        assert response.status_code == 201

        update = {"avail_cores": 2, "avail_ram": 2048}
        url = client.build_resource_uri(["compute", "hypervisors", hypervisor["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["avail_cores"] == 2
        assert output["avail_ram"] == 2048

        client.delete(
            client.build_resource_uri(["compute", "hypervisors", hypervisor["uuid"]])
        )

    def test_hypervisors_delete(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        pool_factory: tp.Callable,
    ):
        hypervisor = pool_factory()
        hypervisor.pop("nodes", None)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])
        response = client.post(url, json=hypervisor)
        assert response.status_code == 201

        url = client.build_resource_uri(["compute", "hypervisors", hypervisor["uuid"]])
        response = client.delete(url)

        assert response.status_code == 204

        url = client.build_resource_uri(["compute", "hypervisors", hypervisor["uuid"]])
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_newcomer_no_access(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(auth_user_admin)

        hypervisor = pool_factory()
        hypervisor.pop("status", None)

        hypervisor_uuid = hypervisor["uuid"]
        url = admin_client.build_collection_uri(["compute", "hypervisors"])
        response = admin_client.post(url, json=hypervisor)

        assert response.status_code == 201

        client = user_api_client(auth_test1_user)

        hypervisor = pool_factory(
            driver_spec={
                "kind": "libvirt",
                "connection_uri": "qemu+tcp://10.20.0.2/system",
            },
        )
        hypervisor.pop("status", None)
        url = client.build_collection_uri(["compute", "hypervisors"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(url, json=hypervisor)

        url = client.build_resource_uri(["compute", "hypervisors", hypervisor_uuid])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete(url)

        url = admin_client.build_collection_uri(["compute", "hypervisors"])
        response = admin_client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 1

        admin_client.delete(
            admin_client.build_resource_uri(["compute", "hypervisors", hypervisor_uuid])
        )

    def test_hypervisors_add_different_connection_uris(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])

        hypervisor1 = pool_factory(
            driver_spec={
                "kind": "libvirt",
                "connection_uri": "qemu+tcp://10.20.0.10/system",
            },
        )
        hypervisor1.pop("status", None)
        response = client.post(url, json=hypervisor1)
        assert response.status_code == 201

        hypervisor2 = pool_factory(
            driver_spec={
                "kind": "libvirt",
                "connection_uri": "qemu+tcp://10.20.0.20/system",
            },
        )
        hypervisor2.pop("status", None)
        response = client.post(url, json=hypervisor2)
        assert response.status_code == 201

        for hv in [hypervisor1, hypervisor2]:
            client.delete(
                client.build_resource_uri(["compute", "hypervisors", hv["uuid"]])
            )

    def test_hypervisors_add_same_connection_uri(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])

        hypervisor1 = pool_factory(
            driver_spec={
                "kind": "libvirt",
                "connection_uri": "qemu+tcp://10.20.0.10/system",
            },
        )
        hypervisor1.pop("status", None)
        response = client.post(url, json=hypervisor1)
        assert response.status_code == 201

        hypervisor2 = pool_factory(
            driver_spec={
                "kind": "libvirt",
                "connection_uri": "qemu+tcp://10.20.0.10/system",
            },
        )
        hypervisor2.pop("status", None)
        with pytest.raises(bazooka_exc.ConflictError):
            client.post(url, json=hypervisor2)

        client.delete(
            client.build_resource_uri(["compute", "hypervisors", hypervisor1["uuid"]])
        )

    def test_node_reuses_an_existing_node_key(
        self,
        node_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        # A key may already exist for a node's uuid from another source
        # (e.g. it's also registered as a local hypervisor's node) -
        # registering the Node must reuse it instead of conflicting on
        # insert.
        node_uuid = sys_uuid.uuid4()
        _, private_key = ua_crypto.generate_key_base64()
        existing_key = ua_models.NodeEncryptionKey(
            uuid=node_uuid, private_key=private_key
        )
        existing_key.insert()

        client = user_api_client(auth_user_admin)
        node = node_factory(uuid=node_uuid)
        response = client.post(
            client.build_collection_uri(["compute", "nodes"]), json=node
        )
        assert response.status_code == 201

        key = ua_models.NodeEncryptionKey.objects.get_one(
            filters={"uuid": dm_filters.EQ(node_uuid)}
        )
        assert key.private_key == private_key

        client.delete(client.build_resource_uri(["compute", "nodes", str(node_uuid)]))

    def test_hypervisors_add_local_hyper_provisions_a_node_key(
        self,
        pool_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node_uuid = sys_uuid.uuid4()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "hypervisors"])

        hypervisor = pool_factory(
            driver_spec={
                "kind": "exordos_local_hyper",
                "connection_uri": "qemu:///system",
                "node": str(node_uuid),
            },
        )
        hypervisor.pop("status", None)
        response = client.post(url, json=hypervisor)
        assert response.status_code == 201

        key = ua_models.NodeEncryptionKey.objects.get_one(
            filters={"uuid": dm_filters.EQ(node_uuid)}
        )
        assert key.private_key

        client.delete(
            client.build_resource_uri(["compute", "hypervisors", hypervisor["uuid"]])
        )
