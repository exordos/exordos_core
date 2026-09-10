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

from gcl_iam import exceptions as iam_exc
import pytest

from exordos_core.common import constants as c
from exordos_core.common.api import controllers as common_controllers
from exordos_core.common.dm import models as common_models


class Recording:
    """The controller half of the stack, standing in for the real base."""

    def __init__(self):
        self.created = None

    def create(self, **kwargs):
        self.created = kwargs
        return kwargs


class Controller(common_controllers.OwnedTagsControllerMixin, Recording):
    pass


USER = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = sys_uuid.UUID("22222222-2222-2222-2222-222222222222")


def _controller(user_uuid=USER):
    controller = Controller()
    info = {"user_info": {"uuid": str(user_uuid)}} if user_uuid else {}
    context = mock.Mock()
    context.iam_context.introspection_info.return_value = info
    patcher = mock.patch.object(
        common_models.contexts, "get_context", return_value=context
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

    def test_a_malformed_list_is_the_models_to_refuse(self):
        # Sorting the list happens before the model has looked at it; a
        # member that is not a tag has to reach it to be answered with a
        # bad request instead of raising here.
        controller, patcher = _controller()

        with patcher:
            controller.create(name="web", tags=[None, 1])

        assert controller.created["tags"] == [None, 1, c.owner_user_tag(USER)]

    def test_something_that_is_not_a_list_is_handed_over_untouched(self):
        controller, patcher = _controller()

        with patcher:
            controller.create(name="web", tags="env:prod")

        assert controller.created["tags"] == "env:prod"


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

    def test_the_merge_alone_assigns_nobody(self):
        # Rows that predate the tags carry none; who they end up with is
        # `written_tags`, one layer up, not the merge.
        kept = common_models.merge_tags(stored=[], incoming=["env:prod"])

        assert kept == ["env:prod"]

    @pytest.mark.parametrize("incoming", ["env:prod", 5, {"env": "prod"}])
    def test_a_value_that_is_not_a_list_is_handed_over_untouched(self, incoming):
        # A string would otherwise be sorted letter by letter into tags
        # the model finds nothing wrong with, and a number would raise
        # here instead of being answered with a bad request.
        kept = common_models.merge_tags(
            stored=[c.owner_user_tag(USER)],
            incoming=incoming,
        )

        assert kept is incoming

    def test_no_list_at_all_cannot_orphan_a_row_either(self):
        # `tags: null` is a value the model takes, so handing it over
        # would drop the owner rather than be refused.
        kept = common_models.merge_tags(
            stored=["env:prod", c.owner_user_tag(USER)],
            incoming=None,
        )

        assert kept == [c.owner_user_tag(USER)]


class TestClaimOnWrite:
    """Who a row belongs to after somebody has written to it.

    A row nobody owns is the writer's from then on: rows written before
    tags existed are otherwise invisible to the reconciler whose rows
    they are, which recreates them, is answered with a conflict, updates,
    and finds them missing again on the next pass.
    """

    OWNER = c.owner_user_tag(USER)

    def test_a_write_claims_a_row_nobody_owns(self):
        tags = common_models.written_tags(
            stored=[],
            incoming=common_models.KEEP,
            owner=self.OWNER,
        )

        assert tags == [self.OWNER]

    def test_a_claim_needs_no_tags_in_the_request(self):
        # The mirror's update carries the record, not its tags: a row it
        # rewrites is claimed all the same.
        tags = common_models.written_tags(
            stored=["env:prod"],
            incoming=common_models.KEEP,
            owner=self.OWNER,
        )

        assert tags == ["env:prod", self.OWNER]

    def test_a_claim_goes_together_with_what_the_client_sent(self):
        tags = common_models.written_tags(
            stored=["env:prod"],
            incoming=["env:stage"],
            owner=self.OWNER,
        )

        assert tags == ["env:stage", self.OWNER]

    def test_a_write_cannot_claim_a_row_that_has_an_owner(self):
        tags = common_models.written_tags(
            stored=[c.owner_user_tag(OTHER)],
            incoming=["env:prod"],
            owner=self.OWNER,
        )

        assert tags == [c.owner_user_tag(OTHER), "env:prod"]

    def test_a_writer_without_a_subject_claims_nothing(self):
        tags = common_models.written_tags(
            stored=[],
            incoming=common_models.KEEP,
            owner=None,
        )

        assert tags is common_models.KEEP

    def test_a_row_with_nothing_to_change_is_left_alone(self):
        # Nothing to claim and nothing sent: the update must not carry
        # tags it has no reason to write.
        tags = common_models.written_tags(
            stored=[self.OWNER],
            incoming=common_models.KEEP,
            owner=self.OWNER,
        )

        assert tags is common_models.KEEP

    @pytest.mark.parametrize("incoming", ["env:prod", 5, {"env": "prod"}])
    def test_a_value_that_is_not_a_list_is_still_the_models_to_refuse(self, incoming):
        tags = common_models.written_tags(
            stored=[],
            incoming=incoming,
            owner=self.OWNER,
        )

        assert tags is common_models.KEEP


class TestCallerOwnerTag:
    def test_a_write_outside_a_request_belongs_to_nobody(self):
        # A service that writes on its own leaves the row as it found it.
        with mock.patch.object(
            common_models.contexts,
            "get_context",
            side_effect=common_models.contexts.ContextIsNotExistsInStorage(),
        ):
            assert common_models.caller_owner_tag() is None

    def test_a_request_without_an_iam_session_belongs_to_nobody(self):
        context = mock.Mock()
        type(context).iam_context = mock.PropertyMock(
            side_effect=iam_exc.NoIamSessionStored()
        )

        with mock.patch.object(
            common_models.contexts, "get_context", return_value=context
        ):
            assert common_models.caller_owner_tag() is None


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

    def test_a_label_is_letters_digits_hyphen_and_underscore(self):
        # Widening the pattern for `*` must not widen what a label holds.
        from exordos_core.user_api.dns.dm import models as dns_models

        name = dns_models.RecordName()

        assert name.validate("we-b_1")
        for refused in (":", ";", "<", "=", ">", "?", "@", "[", "]", "^"):
            assert not name.validate("we%sb" % refused)


class TestRecordsAreFilterableByTag:
    """The resource a realm's mirror asks `?q=tags:"owner:user:…"` of."""

    def test_the_expression_resolves_to_a_tag_filter(self):
        from restalchemy.dm import filters as dm_filters
        import webob

        from exordos_core.user_api.dns.api import controllers as dns_controllers

        # Constructing a controller reads the IAM context off the thread,
        # and this test has no request to have put one there. Everything
        # the expression needs is on the class.
        with mock.patch("gcl_iam.api.controllers.contexts.get_context"):
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
