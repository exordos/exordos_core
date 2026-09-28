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

AUTH_PATH = (
    "/v1/iam/clients/00000000-0000-0000-0000-000000000000/actions/authorize_repo_upload"
)


@pytest.fixture
def lb(
    request,
    user_api_client,
    auth_user_admin,
    lb_factory_with_model,
    vhost_factory_with_model,
    backend_pool_factory_with_model,
):
    client = user_api_client(auth_user_admin)
    # `core` unless a test asks for another type with indirect parametrization.
    lb, lb_model = lb_factory_with_model(
        type=getattr(request, "param", None) or nm.LBTypeCoreKind()
    )
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


def _route(
    lb, route_factory, dav_methods=("PUT", "DELETE"), auth=1, extra=(), value="/repo/"
):
    condition = nm.RoutePrefixConditionKind(
        value=value,
        actions=[
            nm.RuleStaticKind(path="/var/www/repo", dav_methods=list(dav_methods))
        ],
        modifiers=[
            nm.ModifierAuthRequestKind(pool=lb.pool_model, path=AUTH_PATH)
            for _ in range(auth)
        ]
        + list(extra),
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

    def test_writable_dir_cant_be_rewritten(self, lb, route_factory):
        # nginx rewrites before the auth check, which sees the original URI.
        rewrite = nm.ModifierRewriteUrlKind(regex="^/repo/(.*)$", replacement="/x/$1")
        url, route = _route(lb, route_factory, extra=[rewrite])

        assert "`rewrite_url`" in _error(lb.client.post, url, json=route)

    def test_plain_dir_keeps_its_rewrite(self, lb, route_factory):
        rewrite = nm.ModifierRewriteUrlKind(regex="^/repo/(.*)$", replacement="/x/$1")
        url, route = _route(lb, route_factory, dav_methods=(), extra=[rewrite])

        assert lb.client.post(url, json=route).status_code == 201

    @pytest.mark.parametrize("lb", [nm.LBTypeCoreAgentKind()], indirect=True)
    def test_core_agent_lb_cant_have_a_writable_dir(self, lb, route_factory):
        # Its nginx is shared by the LBs of every project.
        url, route = _route(lb, route_factory)

        assert "`core_agent` LB" in _error(lb.client.post, url, json=route)

    def test_lb_with_a_writable_dir_cant_become_core_agent(self, lb, route_factory):
        url, route = _route(lb, route_factory)
        lb.client.post(url, json=route)

        lb_url = lb.client.build_resource_uri(["network", "lb", lb.uuid])
        update = {"type": {"kind": "core_agent"}}

        assert "`core_agent` LB" in _error(lb.client.put, lb_url, json=update)

    def test_lb_without_a_writable_dir_can_become_core_agent(self, lb):
        lb_url = lb.client.build_resource_uri(["network", "lb", lb.uuid])
        update = {"type": {"kind": "core_agent"}}

        assert lb.client.put(lb_url, json=update).status_code == 200

    def test_writable_dir_needs_a_prefix_ending_in_a_slash(self, lb, route_factory):
        # `location /repo` aliases `/repo<x>/...` to `<path>/<x>/...`.
        url, route = _route(lb, route_factory, value="/repo")

        assert "ending in `/`" in _error(lb.client.post, url, json=route)
