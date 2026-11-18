#    Copyright 2026 Genesis Corporation.
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

import uuid

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.elements.dm import models


@pytest.mark.parametrize(
    "params",
    [{}, {"sort_key": "name"}, {"kind": "em_pagination_config_configs"}],
)
def test_resource_pagination(user_api_client, auth_user_admin, params):
    client = user_api_client(auth_user_admin)
    element = models.Element(name="pagination", version="1.0.0")
    element.insert()
    for index in range(5, 0, -1):
        prefix = (
            "$pagination.config.configs" if index <= 3 else "$pagination.em.services"
        )
        models.Resource(
            uuid=uuid.UUID(int=index),
            element=element,
            name=f"resource-{6 - index}",
            resource_link_prefix=prefix,
            value={},
        ).insert()

    url = client.build_collection_uri(["em", "resources"])
    unpaginated = client.get(url, params=params)
    assert unpaginated.status_code == 200
    expected = unpaginated.json()
    assert len(expected) == (3 if "kind" in params else 5)
    assert "X-Pagination-Limit" not in unpaginated.headers
    key = params.get("sort_key", "uuid")
    expected.sort(key=lambda item: (item[key], item["uuid"]))

    query = dict(params, page_limit=2)
    collected = []
    for _ in range(3):
        response = client.get(url, params=query)
        assert response.status_code == 200
        page = response.json()
        assert response.headers["X-Pagination-Limit"] == "2"
        assert len(page) <= 2
        collected.extend(page)
        marker = response.headers.get("X-Pagination-Marker")
        if marker is None:
            assert len(page) < 2
            break
        assert marker == page[-1]["uuid"]
        query["page_marker"] = marker
    assert collected == expected

    empty = client.get(
        url, params=dict(params, page_limit=2, page_marker=expected[-1]["uuid"])
    )
    assert empty.status_code == 200
    assert empty.json() == []
    assert "X-Pagination-Marker" not in empty.headers

    unlimited = client.get(url, params=dict(params, page_limit=0))
    assert unlimited.status_code == 200
    assert (
        sorted(unlimited.json(), key=lambda item: (item[key], item["uuid"])) == expected
    )
    assert "X-Pagination-Limit" not in unlimited.headers


@pytest.mark.parametrize(
    "params",
    [
        {"page_limit": -1},
        {"page_limit": "invalid"},
        {"page_limit": 2, "page_marker": "invalid"},
    ],
)
def test_resource_pagination_rejects_invalid_parameters(
    user_api_client, auth_user_admin, params
):
    client = user_api_client(auth_user_admin)
    url = client.build_collection_uri(["em", "resources"])
    with pytest.raises(bazooka_exc.BadRequestError):
        client.get(url, params=params)


def test_resource_pagination_enforces_read_permission(user_api_client, auth_test1_user):
    client = user_api_client(auth_test1_user)
    url = client.build_collection_uri(["em", "resources"])
    with pytest.raises(bazooka_exc.ForbiddenError):
        client.get(url, params={"page_limit": 2})
