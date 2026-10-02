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

"""The published documents are built here, so building them has to work.

They used to be committed, which meant a route or a model that broke the
build broke it for whoever regenerated them next -- possibly a release
later. Now the site is built from these, so this runs on every change.
"""

import pytest
from restalchemy.common import contexts as ra_contexts

from exordos_core.common import openapi
from exordos_core.elements.dm import models as element_models
from exordos_core.elements.dm import utils as element_utils


@pytest.fixture(scope="module", params=openapi.APIS, ids=lambda api: api.name)
def specification(request):
    return request.param, openapi.build(request.param)


class TestBuildSpecification:
    def test_describes_the_api(self, specification):
        api, spec = specification

        assert spec["openapi"] == openapi.OPENAPI_VERSION
        assert spec["paths"]
        assert spec["servers"][0]["url"] == api.url
        assert spec["info"]["version"] == openapi.PUBLISHED_VERSION

    def test_every_reference_resolves(self, specification):
        _, spec = specification
        schemas = spec["components"]["schemas"]

        dangling = {
            name for name in element_utils.iter_schema_refs(spec) if name not in schemas
        }

        assert not dangling

    def test_user_api_is_described_in_full(self):
        """A caller's rights decide what it is shown; the document is whole.

        A field permission can be a rule rather than a constant, and then
        the enforcer decides whether the field is in the document at all.
        `custom_props` on a user is one: with an enforcer that refuses, the
        published document would quietly stop mentioning it.
        """
        spec = openapi.build(openapi.USER_API)

        user = spec["components"]["schemas"]["User_Get"]
        assert "custom_props" in user["properties"]

    def test_leaves_no_context_behind(self):
        openapi.build(openapi.STATUS_API)

        with pytest.raises(ra_contexts.ContextIsNotExistsInStorage):
            ra_contexts.get_context()

    def test_leaves_the_element_engine_alone(self):
        loader = element_models.element_engine.load_from_database
        elements_loader = element_models.element_engine.load_elements

        openapi.build(openapi.STATUS_API)

        assert element_models.element_engine.load_from_database == loader
        assert element_models.element_engine.load_elements == elements_loader
