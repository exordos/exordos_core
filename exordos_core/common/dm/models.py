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

from gcl_iam import exceptions as iam_exc
from restalchemy.common import contexts
from restalchemy.dm import models

from exordos_core.common import constants as c


KEEP = object()
"""What a write that carries no tags at all says about them."""


def _is_reserved(tag):
    """Whether a tag is the installation's to write.

    Anything that is not a string is not: sorting the list happens
    before the model has looked at it, and a malformed member has to
    reach the model to be answered with a 400 rather than raise here.
    """
    return isinstance(tag, str) and tag.startswith(c.TAG_RESERVED_PREFIX)


def client_tags(tags):
    """The part of a tag list a client is allowed to decide."""
    return [tag for tag in (tags or []) if not _is_reserved(tag)]


def reserved_tags(tags):
    """The part of a tag list the installation owns."""
    return [tag for tag in (tags or []) if _is_reserved(tag)]


def merge_tags(stored, incoming):
    """The tags a row keeps when a client sends it a new list.

    Something that is not a list at all is handed back as it came:
    sorting it happens before the model has looked at it, and it has to
    reach the model to be answered with a bad request rather than be
    turned into tags here. Except no list at all, which the model takes
    -- so it is read as an empty one and the row keeps its owner.
    """
    if incoming is not None and not isinstance(incoming, list):
        return incoming
    return reserved_tags(stored) + client_tags(incoming)


def caller_owner_tag():
    """The tag standing for whoever is making the current request.

    None when nobody is: a service writing outside a request, or a
    caller whose token carries no subject. Then a row is left as it was
    found, which every reconciler reads as "not mine to remove".
    """
    try:
        iam_context = contexts.get_context().iam_context
    except (contexts.ContextIsNotExistsInStorage, iam_exc.NoIamSessionStored):
        return None
    introspection = iam_context.introspection_info()
    user_uuid = ((introspection or {}).get("user_info") or {}).get("uuid")
    return c.owner_user_tag(user_uuid) if user_uuid else None


def written_tags(stored, incoming, owner):
    """The tags a row carries after somebody has written to it.

    `incoming` is the list the request sent, or `KEEP` when it sent no
    tags at all: the reserved part is the row's and the writer's to
    decide, whether or not the request said anything about tags. `KEEP`
    comes back when the write leaves the tags as they are.

    A row nobody owns is claimed by whoever writes to it. Rows written
    before tags existed are otherwise invisible to the reconciler whose
    rows they are: it does not find them among its own, so it recreates
    them, is answered with a conflict, updates -- and finds them missing
    again on the next pass, forever. Claiming ends that on the first
    write, and gives such a row back the cleanup it had lost.
    """
    if incoming is KEEP:
        tags = list(stored or [])
    else:
        tags = merge_tags(stored, incoming)
        if not isinstance(tags, list):
            # Not a tag list at all: it has to reach the model to be
            # answered with a bad request rather than be claimed here.
            return KEEP
        tags = list(tags)
    if owner and not reserved_tags(tags):
        tags = tags + [owner]
    if incoming is KEEP and tags == list(stored or []):
        return KEEP
    return tags


class ModelWithReservedTags(models.ModelWithTags):
    """Tags whose reserved part survives whatever the client sends.

    Ownership is decided by the first write that finds the row without
    an owner -- its creation, or, for a row that predates tags, the next
    write to touch it -- and every later update keeps it: re-stamping
    from the caller would let an operator who edits a mirrored record
    take it over, and letting the client's list through would let anyone
    drop the tag and orphan the row instead.
    """

    def update_dm(self, values):
        # A row that already carries an owner keeps it, and then who is
        # writing does not matter and is not asked.
        owner = None if reserved_tags(self.tags) else caller_owner_tag()
        tags = written_tags(self.tags, values.get("tags", KEEP), owner)
        if tags is not KEEP:
            values = dict(values, tags=tags)
        return super().update_dm(values)


class ModelWithFullAsset(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    models.ModelWithNameDesc,
):
    pass


class CastToBaseMixin:
    __cast_fields__ = None

    def cast_to_base(self) -> models.SimpleViewMixin:
        # Convert to simple view without relations
        fields = self.__cast_fields__ or tuple(self.properties.properties.keys())
        view = self.dump_to_simple_view(skip=fields)

        # Translate relations into uuid
        for relation in fields:
            value = getattr(self, relation)
            if value is not None:
                view[relation] = value.uuid

        # Find base class
        base_class = None
        for base in self.__class__.__bases__:
            if base != CastToBaseMixin:
                base_class = base
                break
        else:
            raise RuntimeError(f"Failed to find base class for {self.__class__}")

        return base_class.restore_from_simple_view(**view)
