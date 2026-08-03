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

"""Who owns a tagged row, and who is allowed to say so."""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.common import constants as c
from exordos_core.common.dm import models as common_models
from exordos_core.user_api.api import controllers as core_controllers


class Recording:
    """The controller half of the stack, standing in for the real base."""

    def __init__(self):
        self.created = None

    def create(self, **kwargs):
        self.created = kwargs
        return kwargs


class Controller(core_controllers.OwnedTagsControllerMixin, Recording):
    pass


USER = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = sys_uuid.UUID("22222222-2222-2222-2222-222222222222")


def _controller(user_uuid=USER):
    controller = Controller()
    info = {"user_info": {"uuid": str(user_uuid)}} if user_uuid else {}
    context = mock.Mock()
    context.iam_context.introspection_info.return_value = info
    patcher = mock.patch.object(
        core_controllers.contexts, "get_context", return_value=context
    )
    return controller, patcher


class TestOwnerStampOnCreate:
    def test_a_created_row_carries_its_creator(self):
        controller, patcher = _controller()

        with patcher:
            controller.create(name="web")

        assert controller.created["tags"] == [c.owner_user_tag(USER)]

    def test_the_client_keeps_its_own_tags(self):
        controller, patcher = _controller()

        with patcher:
            controller.create(name="web", tags=["env:prod"])

        assert controller.created["tags"] == ["env:prod", c.owner_user_tag(USER)]

    def test_an_owner_the_client_asks_for_is_dropped(self):
        # Otherwise a tenant could hand its row to somebody else's
        # reconciler and have it removed.
        controller, patcher = _controller()

        with patcher:
            controller.create(name="web", tags=[c.owner_user_tag(OTHER), "env:prod"])

        assert controller.created["tags"] == ["env:prod", c.owner_user_tag(USER)]

    def test_a_caller_without_a_subject_leaves_the_row_unowned(self):
        controller, patcher = _controller(user_uuid=None)

        with patcher:
            controller.create(name="web", tags=["env:prod"])

        assert controller.created["tags"] == ["env:prod"]


class TestOwnerSurvivesUpdate:
    """What `ModelWithReservedTags.update_dm` keeps of a client's list.

    The merge is tested on its own rather than on a model instance: a
    unit test that builds one is at the mercy of whatever built a model
    before it (`Model.__new__` without `pour()` writes into the
    class-level properties, which are shared process-wide). The mixin
    itself is covered against the real Record in the functional tests.
    """

    def test_an_update_cannot_take_a_row_over(self):
        kept = common_models.merge_tags(
            stored=["env:prod", c.owner_user_tag(USER)],
            incoming=["env:stage", c.owner_user_tag(OTHER)],
        )

        assert kept == [c.owner_user_tag(USER), "env:stage"]

    def test_an_update_cannot_orphan_a_row(self):
        kept = common_models.merge_tags(
            stored=["env:prod", c.owner_user_tag(USER)],
            incoming=[],
        )

        assert kept == [c.owner_user_tag(USER)]

    def test_an_unowned_row_stays_unowned(self):
        # Rows that predate the tags carry none, and nothing retro-assigns
        # them to whoever edits them next.
        kept = common_models.merge_tags(stored=[], incoming=["env:prod"])

        assert kept == ["env:prod"]


class TestRecordName:
    """A zone's own wildcard is a name the zone can hold."""

    def test_the_zones_own_wildcard_is_a_name(self):
        from exordos_core.user_api.dns.dm import models as dns_models

        assert dns_models.RecordName().validate("*")

    def test_the_names_it_always_took_are_still_names(self):
        from exordos_core.user_api.dns.dm import models as dns_models

        name = dns_models.RecordName()

        assert name.validate("@")
        assert name.validate("www")
        assert name.validate("*.www")

    def test_a_name_it_never_took_is_still_refused(self):
        from exordos_core.user_api.dns.dm import models as dns_models

        assert not dns_models.RecordName().validate("*a")


class TestRecordsAreFilterableByTag:
    """The resource a realm's mirror asks `?q=tags:"owner:user:…"` of.

    Runs in the environment that has the language; the functional test of
    the same thing skips where the released library has none.
    """

    def test_the_expression_resolves_to_a_tag_filter(self):
        pytest.importorskip("restalchemy.api.filter_lang")
        import webob

        from restalchemy.dm import filters as dm_filters
        from exordos_core.user_api.dns.api import controllers as dns_controllers

        controller = dns_controllers.RecordController(mock.Mock())
        query = 'q=tags:"%s"' % c.owner_user_tag(USER)

        filters = controller._prepare_query_filter(
            webob.Request.blank("/?%s" % query).params,
        )

        assert filters == {"tags": dm_filters.ContainsAll([c.owner_user_tag(USER)])}

    def test_the_resource_parses_query_parameters_at_all(self):
        # `process_filters` is what the language needs, and what turns an
        # unknown parameter into a 400 instead of a query nobody meant.
        from exordos_core.user_api.dns.api import controllers as dns_controllers

        assert dns_controllers.RecordController.__resource__.is_process_filters()
