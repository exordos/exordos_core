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

"""A writable `local_dir` route guarded by an `auth_request` modifier: the
element repository an LB serves."""

import types

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.user_api.network.dm import models as nm

AUTH_PATH = "/v1/repo/upload_auth/repo"


@pytest.fixture
def lb(
    user_api_client,
    auth_user_admin,
    lb_factory_with_model,
    vhost_factory_with_model,
    backend_pool_factory_with_model,
):
    client = user_api_client(auth_user_admin)
    lb, lb_model = lb_factory_with_model()
    client.post(client.build_collection_uri(["network", "lb"]), json=lb)

    pool, pool_model = backend_pool_factory_with_model(
        lb_model, endpoints=[nm.BackendHostKind(host="192.168.100.2", port=11010)]
    )
    client.post(
        client.build_collection_uri(["network", "lb", lb["uuid"], "backend_pools"]),
        json=pool,
    )

    vhost, vhost_model = vhost_factory_with_model(
        lb_model, protocol=nm.Protocol.HTTP, port=8080, domains=["repo.example.com"]
    )
    client.post(
        client.build_collection_uri(["network", "lb", lb["uuid"], "vhosts"]),
        json=vhost,
    )

    return types.SimpleNamespace(
        client=client,
        uuid=lb["uuid"],
        pool=pool,
        pool_model=pool_model,
        vhost=vhost,
        vhost_model=vhost_model,
    )


def _route(lb, route_factory, dav_methods=("PUT", "DELETE"), auth=1):
    condition = nm.RoutePrefixConditionKind(
        value="/repo/",
        actions=[
            nm.RuleStaticKind(path="/var/www/repo", dav_methods=list(dav_methods))
        ],
        modifiers=[
            nm.ModifierAuthRequestKind(pool=lb.pool_model, path=AUTH_PATH)
            for _ in range(auth)
        ],
    )
    url = lb.client.build_collection_uri(
        ["network", "lb", lb.uuid, "vhosts", lb.vhost["uuid"], "routes"]
    )
    return url, route_factory(lb.vhost_model, condition=condition)


def _error(call, *args, **kwargs):
    with pytest.raises(bazooka_exc.BadRequestError) as exc_info:
        call(*args, **kwargs)
    return exc_info.value.cause.response.text


class TestRepoRoute:
    def test_creates_writable_dir_behind_auth_request(self, lb, route_factory):
        url, route = _route(lb, route_factory)

        response = lb.client.post(url, json=route)

        assert response.status_code == 201
        cond = response.json()["condition"]
        assert cond["actions"][0]["dav_methods"] == ["PUT", "DELETE"]
        assert cond["modifiers"] == [
            {"kind": "auth_request", "pool": lb.pool["uuid"], "path": AUTH_PATH}
        ]

    def test_writable_dir_needs_auth_request(self, lb, route_factory):
        url, route = _route(lb, route_factory, auth=0)

        assert "needs an `auth_request`" in _error(lb.client.post, url, json=route)

    def test_only_one_auth_request(self, lb, route_factory):
        url, route = _route(lb, route_factory, auth=2)

        assert "only one `auth_request`" in _error(lb.client.post, url, json=route)

    @pytest.mark.parametrize("method", ["MOVE", "COPY", "MKCOL"])
    def test_only_put_and_delete_are_offered(self, lb, route_factory, method):
        url, route = _route(lb, route_factory)
        route["condition"]["actions"][0]["dav_methods"] = [method]

        with pytest.raises(bazooka_exc.BadRequestError):
            lb.client.post(url, json=route)

    def test_auth_request_path_is_checked(self, lb, route_factory):
        url, route = _route(lb, route_factory)
        route["condition"]["modifiers"][0]["path"] = "/v1/repo/x; return 200"

        with pytest.raises(bazooka_exc.BadRequestError):
            lb.client.post(url, json=route)

    def test_pool_used_by_auth_request_stays(self, lb, route_factory):
        url, route = _route(lb, route_factory)
        lb.client.post(url, json=route)

        pool_url = lb.client.build_resource_uri(
            ["network", "lb", lb.uuid, "backend_pools", lb.pool["uuid"]]
        )
        assert "Backend pool in use" in _error(lb.client.delete, pool_url)
