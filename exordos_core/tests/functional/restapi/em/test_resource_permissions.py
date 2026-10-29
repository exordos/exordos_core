# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from bazooka import exceptions
import pytest
from restalchemy.dm import filters

from exordos_core.elements.dm import models


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_element_reader_cannot_mutate_nested_targets(
    user_api_client, auth_test1_user, auth_user_admin, method
):
    provider = models.Element(name="core", version="1.0.0")
    provider.insert()
    element = models.Element(name="application", version="1.0.0")
    element.insert()
    target = models.Resource(
        element=element,
        name="network",
        resource_link_prefix="$core.network.networks",
        value={"name": "original"},
    )
    target.insert()
    reader = user_api_client(auth_test1_user, permissions=["em.element.read"])
    path = reader.build_collection_uri(
        ["em", "elements", str(element.uuid), "resources"]
    )
    item = path.rstrip("/") + "/" + str(target.uuid)
    assert reader.get(item).status_code == 200
    url = path if method == "post" else item
    data = {} if method == "delete" else {"json": {"value": {"name": "unauthorized"}}}
    if method == "post":
        data["json"].update(
            name="other", resource_link_prefix=target.resource_link_prefix
        )
    with pytest.raises(exceptions.ForbiddenError):
        getattr(reader, method)(url, **data)
    stored = models.Resource.objects.get_one(filters={"uuid": filters.EQ(target.uuid)})
    assert stored.value == {"name": "original"}
    admin = user_api_client(auth_user_admin)
    assert admin.put(item, json={"value": {"name": "authorized"}}).status_code == 200
