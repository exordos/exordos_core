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
import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.user_api.network.dm import models as nm


class TestLBApi:
    @staticmethod
    def _lb_cmp_shallow(
        lb_foo: tp.Dict[str, tp.Any],
        lb_bar: tp.Dict[str, tp.Any],
    ) -> bool:
        return all(
            (lb_foo[key] == lb_bar[key])
            for key in (
                "uuid",
                "name",
            )
        )

    @staticmethod
    def _vhost_cmp_shallow(
        vhost_foo: tp.Dict[str, tp.Any],
        vhost_bar: tp.Dict[str, tp.Any],
    ) -> bool:
        return all(
            (vhost_foo[key] == vhost_bar[key])
            for key in (
                "uuid",
                "name",
                "enabled",
                "protocol",
                "port",
            )
        )

    @staticmethod
    def _backend_pool_cmp_shallow(
        backend_pool_foo: tp.Dict[str, tp.Any],
        backend_pool_bar: tp.Dict[str, tp.Any],
    ) -> bool:
        return all(
            (backend_pool_foo[key] == backend_pool_bar[key])
            for key in ("uuid", "name", "balance", "endpoints")
        )

    @staticmethod
    def _route_cmp_shallow(
        route_foo: tp.Dict[str, tp.Any],
        route_bar: tp.Dict[str, tp.Any],
    ) -> bool:
        return all(
            (route_foo[key] == route_bar[key])
            for key in ("uuid", "name", "enabled", "condition")
        )

    def test_create_lb(self, user_api_client, auth_user_admin, lb_factory):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])

        lb = lb_factory()
        response = client.post(
            url,
            json=lb,
        )

        assert response.status_code == 201
        output = response.json()

        assert self._lb_cmp_shallow(lb, output)
        assert output["status"] == nm.LBStatus.NEW.value

    def test_list_lbs(self, user_api_client, auth_user_admin, lb_factory):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["network", "lb"])

        count = 3
        lbs = []
        for i in range(count):
            lb = lb_factory(
                name=f"lb_{i}",
            )
            lbs.append(lb)

        for lb in lbs:
            response = client.post(url, json=lb)
            output = response.json()
            assert response.status_code == 201
            assert self._lb_cmp_shallow(lb, output)

        # List all LBs
        response = client.get(url)
        assert response.status_code == 200
        output = response.json()
        assert len(output) == count

    def test_get_lb(self, user_api_client, auth_user_admin, lb_factory):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])

        lb = lb_factory()
        response = client.post(
            url,
            json=lb,
        )

        assert response.status_code == 201
        output = response.json()

        assert self._lb_cmp_shallow(lb, output)
        assert output["status"] == nm.LBStatus.NEW.value

        url = client.build_resource_uri(["network", "lb", lb["uuid"]])
        response = client.get(url)
        assert response.status_code == 200
        output = response.json()
        assert self._lb_cmp_shallow(lb, output)

    def test_update_lb(self, user_api_client, auth_user_admin, lb_factory):
        lb = lb_factory()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])

        response = client.post(url, json=lb)
        assert response.status_code == 201

        update = {"name": "updated-lb"}
        url = client.build_resource_uri(["network", "lb", lb["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "updated-lb"
        assert output["status"] == nm.LBStatus.NEW.value

    def test_delete_lb(self, user_api_client, auth_user_admin, lb_factory):
        lb = lb_factory()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])

        response = client.post(url, json=lb)
        assert response.status_code == 201

        url = client.build_resource_uri(["network", "lb", lb["uuid"]])
        response = client.delete(url)

        assert response.status_code == 204

        url = client.build_resource_uri(["network", "lb", lb["uuid"]])
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_creates_vhost(
        self, user_api_client, auth_user_admin, lb_factory_with_model, vhost_factory
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        url = client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"])

        # check vhost_requires_domains_for_http
        with pytest.raises(bazooka_exc.BadRequestError) as exc_info:
            vhost = vhost_factory(lb_model, domains=[])
            client.post(
                url,
                json=vhost,
            )
        assert "L7 protocols have to get at least one value" in str(
            exc_info.value.cause.response.text
        )

        vhost = vhost_factory(lb_model, domains=["example.com"])

        vhost_response = client.post(
            url,
            json=vhost,
        )

        assert vhost_response.status_code == 201
        output = vhost_response.json()
        assert self._vhost_cmp_shallow(vhost, output)

    def test_creates_https_vhost_with_cert(
        self,
        user_api_client,
        auth_user_admin,
        lb_factory_with_model,
        vhost_factory,
        self_signed_cert,
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        cert_crt, cert_key = self_signed_cert("127.0.0.1")
        cert = nm.CertKind(kind="raw", crt=cert_crt, key=cert_key)

        url = client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"])

        # check vhost_requires_cert_for_https
        with pytest.raises(bazooka_exc.BadRequestError) as exc_info:
            vhost = vhost_factory(
                lb_model,
                protocol=nm.Protocol.HTTPS,
                port=443,
                domains=["secure.example.com"],
                cert=cert,
            )
            vhost.pop("cert")
            client.post(
                url,
                json=vhost,
            )
        assert "Certificate is required" in str(exc_info.value.cause.response.text)

        vhost = vhost_factory(
            lb_model,
            protocol=nm.Protocol.HTTPS,
            port=443,
            domains=["secure.example.com"],
            cert=cert,
        )

        vhost_response = client.post(url, json=vhost)
        assert vhost_response.status_code == 201
        output = vhost_response.json()
        assert self._vhost_cmp_shallow(vhost, output)

    def test_creates_backend_pool(
        self,
        user_api_client,
        auth_user_admin,
        lb_factory_with_model,
        backend_pool_factory,
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "backend_pools"]
        )

        # check backend_pool_requires_endpoints
        with pytest.raises(ex_exceptions.ValidateException) as exc_info:
            backend_pool_factory(lb_model, endpoints=[])
        assert "endpoints" in str(exc_info.value.err)

        endpoint = nm.BackendHostKind(kind="host", host="192.168.1.10", port=8080)
        backend_pool = backend_pool_factory(lb_model, endpoints=[endpoint])

        response = client.post(
            url,
            json=backend_pool,
        )

        assert response.status_code == 201
        output = response.json()
        assert self._backend_pool_cmp_shallow(backend_pool, output)

    def test_creates_route_prefix_condition(
        self,
        user_api_client,
        auth_user_admin,
        lb_factory_with_model,
        vhost_factory_with_model,
        backend_pool_factory,
        route_factory,
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        # Create backend pool
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "backend_pools"]
        )
        endpoint = nm.BackendHostKind(kind="host", host="192.168.1.10", port=8080)
        backend_pool = backend_pool_factory(lb_model, endpoints=[endpoint])

        response = client.post(
            url,
            json=backend_pool,
        )

        assert response.status_code == 201

        # Create vhost
        url = client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"])
        vhost, vhost_model = vhost_factory_with_model(
            lb_model,
            protocol=nm.Protocol.HTTP,
            port=80,
            domains=["example.com"],
        )
        response = client.post(
            url,
            json=vhost,
        )

        assert response.status_code == 201

        # Create route with prefix condition
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "vhosts", vhost["uuid"], "routes"]
        )
        condition = nm.RoutePrefixConditionKind(
            value="/api", actions=[nm.RuleRedirectKind(url="https://example.com")]
        )
        route = route_factory(
            vhost_model,
            condition=condition,
        )
        response = client.post(
            url,
            json=route,
        )

        assert response.status_code == 201
        output = response.json()
        assert self._route_cmp_shallow(route, output)

    def test_creates_route_exact_condition(
        self,
        user_api_client,
        auth_user_admin,
        lb_factory_with_model,
        vhost_factory_with_model,
        backend_pool_factory,
        route_factory,
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        # Create backend pool
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "backend_pools"]
        )
        endpoint = nm.BackendHostKind(kind="host", host="192.168.1.10", port=8080)
        backend_pool = backend_pool_factory(lb_model, endpoints=[endpoint])

        response = client.post(
            url,
            json=backend_pool,
        )

        assert response.status_code == 201

        # Create vhost
        url = client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"])
        vhost, vhost_model = vhost_factory_with_model(
            lb_model,
            protocol=nm.Protocol.HTTP,
            port=80,
            domains=["example.com"],
        )
        response = client.post(
            url,
            json=vhost,
        )

        assert response.status_code == 201

        # Create route with exact condition
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "vhosts", vhost["uuid"], "routes"]
        )
        condition = nm.RouteExactConditionKind(
            value="/login",
            actions=[nm.RuleRedirectKind(url="https://example.com/login", code=301)],
        )
        route = route_factory(
            vhost_model,
            condition=condition,
        )
        response = client.post(
            url,
            json=route,
        )

        assert response.status_code == 201
        output = response.json()
        assert self._route_cmp_shallow(route, output)

    def test_creates_route_regex_condition(
        self,
        user_api_client,
        auth_user_admin,
        lb_factory_with_model,
        vhost_factory_with_model,
        backend_pool_factory,
        route_factory,
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        # Create backend pool
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "backend_pools"]
        )
        endpoint = nm.BackendHostKind(kind="host", host="192.168.1.10", port=8080)
        backend_pool = backend_pool_factory(lb_model, endpoints=[endpoint])

        response = client.post(
            url,
            json=backend_pool,
        )

        assert response.status_code == 201

        # Create vhost
        url = client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"])
        vhost, vhost_model = vhost_factory_with_model(
            lb_model,
            protocol=nm.Protocol.HTTP,
            port=80,
            domains=["example.com"],
        )
        response = client.post(
            url,
            json=vhost,
        )

        assert response.status_code == 201

        # Create route with regex condition
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "vhosts", vhost["uuid"], "routes"]
        )
        modifier = nm.ModifierInsertHeaderKind(
            name="X-Forwarded-For", value="192.168.1.10"
        )
        condition = nm.RouteRegexConditionKind(
            value=r"^[a-z_][a-z0-9_]*$",
            modifiers=[modifier],
            actions=[nm.RuleStaticDownloadKind(url="https://example.com/web.tar.gz")],
        )
        route = route_factory(
            vhost_model,
            condition=condition,
        )
        response = client.post(
            url,
            json=route,
        )

        assert response.status_code == 201
        output = response.json()
        assert self._route_cmp_shallow(route, output)

    def test_check_delete_backend_pool(
        self,
        user_api_client,
        auth_user_admin,
        lb_factory_with_model,
        vhost_factory_with_model,
        backend_pool_factory_with_model,
        route_factory,
    ):
        lb, lb_model = lb_factory_with_model()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "lb"])
        response = client.post(url, json=lb)
        assert response.status_code == 201

        # Create backend pool
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "backend_pools"]
        )
        endpoint = nm.BackendHostKind(kind="host", host="192.168.1.10", port=8080)
        backend_pool, backend_pool_model = backend_pool_factory_with_model(
            lb_model, endpoints=[endpoint]
        )

        response = client.post(
            url,
            json=backend_pool,
        )

        assert response.status_code == 201

        # Create vhost
        url = client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"])
        vhost, vhost_model = vhost_factory_with_model(
            lb_model,
            protocol=nm.Protocol.HTTP,
            port=80,
            domains=["example.com"],
        )
        response = client.post(
            url,
            json=vhost,
        )

        assert response.status_code == 201

        # Create route with action backend pool
        url = client.build_collection_uri(
            ["network", "lb", lb["uuid"], "vhosts", vhost["uuid"], "routes"]
        )
        modifier = nm.ModifierInsertHeaderKind(
            name="X-Forwarded-For", value="192.168.1.10"
        )
        condition = nm.RouteRegexConditionKind(
            value=r"^[a-z_][a-z0-9_]*$",
            modifiers=[modifier],
            actions=[nm.RuleHTTPBackendKind(pool=backend_pool_model)],
        )
        route = route_factory(
            vhost_model,
            condition=condition,
        )
        response = client.post(
            url,
            json=route,
        )

        assert response.status_code == 201

        url = client.build_resource_uri(
            ["network", "lb", lb["uuid"], "backend_pools", backend_pool["uuid"]]
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc_info:
            client.delete(url)
        assert "Backend pool in use" in str(exc_info.value.cause.response.text)
