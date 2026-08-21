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

"""Build the OpenAPI documents this project publishes.

A document describes the code that is loaded and not the data a service
holds, so building one needs neither a database nor a service listening on a
port: walking the route tree of an application object produces the document
that service would answer ``/specifications/{version}`` with. That is what
this module does, and it is why the documents are generated when the site is
built instead of being kept in the tree, where they were a megabyte of
derived text that went stale whenever the generator moved.

Three things reach for request state while the tree is walked, and each is
answered here with what the published document is meant to describe:

  - IAM controllers read the caller's introspection when they are
    constructed;
  - field permissions ask the enforcer whether a field is visible, so a
    document is only as complete as the rights of whoever asked for it. The
    published one is the complete one, so every rule is granted here -- the
    same document an administrator was served when these files were built by
    calling a running service;
  - three element controllers load the element registry from the database
    when they are constructed. The document is built with an empty registry,
    which is what the committed documents were built with as well: they came
    from a service started against a freshly migrated database.
"""

import contextlib
import dataclasses
import importlib
import json
import typing as tp

from restalchemy.api import applications
from restalchemy.api import constants as api_constants
from restalchemy.api import contexts as api_contexts
from restalchemy.common import contexts as common_contexts
import webob

OPENAPI_VERSION = "3.0.3"

# What the published documents carry as the API version. The real one moves
# with every release while the API it describes does not, and readers of the
# site are looking at whatever is deployed rather than at a release.
PUBLISHED_VERSION = "latest"


@dataclasses.dataclass(frozen=True)
class Api:
    """One of the four APIs this project serves."""

    name: str
    module: str
    # The default bind port of the matching `ec-*-api` command, which is
    # where a local installation answers and therefore what makes the "Try
    # it out" button in the published document work.
    port: int

    @property
    def filename(self) -> str:
        return f"openapi_{self.name}.yaml"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


USER_API = Api("user", "exordos_core.user_api.api.app", 11010)
BOOT_API = Api("boot", "exordos_core.boot_api.api.app", 11013)
ORCH_API = Api("orch", "exordos_core.orch_api.api.app", 11011)
STATUS_API = Api("status", "exordos_core.status_api.api.app", 11012)

APIS = (USER_API, BOOT_API, ORCH_API, STATUS_API)


class _GrantEveryRule:
    """An enforcer that answers yes.

    Field visibility is decided by asking the enforcer about the caller, so
    an enforcer is needed to build a document at all. This one describes the
    API in full rather than the part some particular caller may see.
    """

    def enforce(self, rule, do_raise=False, exc=None):
        return True

    def enforce_raw(self, rule, do_raise=False, exc=None):
        return True


class _SpecIamContext:
    """The IAM half of a context, holding what building a document reads."""

    def __init__(self):
        self.enforcer = _GrantEveryRule()

    def introspection_info(self) -> dict:
        # No project: controllers read `project_id` from here to scope what
        # a caller may touch, and a document is not scoped to anything.
        return {}


@contextlib.contextmanager
def _spec_build_environment() -> tp.Iterator[None]:
    """Stand in for what a served request would have provided.

    Controllers are constructed to be asked for their resource, and some of
    them do request-time work while being constructed. Both substitutions
    are undone on the way out so that nothing here is visible to whatever
    runs next in this process.
    """
    from exordos_core.elements.dm import models as element_models

    context = common_contexts.ContextWithStorage()
    context.iam_context = _SpecIamContext()

    element_engine = element_models.element_engine
    load_from_database = element_engine.load_from_database
    element_engine.load_from_database = lambda: None
    try:
        with context.context_manager():
            yield
    finally:
        element_engine.load_from_database = load_from_database


def _build_request(application: tp.Any) -> webob.Request:
    """A request asking to read, which is what serving the document is.

    Field visibility is decided per method, so the request the document is
    built from has to name one.
    """
    request = webob.Request.blank("/")
    request.application = application
    request.api_context = api_contexts.RequestContext(request)
    request.api_context.set_active_method(api_constants.GET)
    return request


def build(api: Api, version: str = OPENAPI_VERSION) -> dict:
    """Build the OpenAPI document of a single API."""
    module = importlib.import_module(api.module)

    with _spec_build_environment():
        application = applications.OpenApiApplication(
            route_class=module.get_api_application(),
            openapi_engine=module.get_openapi_engine(),
        )
        specification = application.openapi_engine.build_openapi_specification(
            version=version,
            request=_build_request(application),
        )

    # The servers block is built from the request being answered, and there
    # is no request here worth describing.
    specification["servers"][0]["url"] = api.url
    specification["info"]["version"] = PUBLISHED_VERSION

    # A served document is JSON by the time anyone reads it, and the builder
    # leaves types behind that only survive in this process -- `components`
    # is a defaultdict. Round-tripping hands every caller the document a
    # service would have handed them.
    return json.loads(json.dumps(specification))


def build_all(version: str = OPENAPI_VERSION) -> tp.Dict[Api, dict]:
    """Build the OpenAPI document of every API this project serves."""
    return {api: build(api, version=version) for api in APIS}
