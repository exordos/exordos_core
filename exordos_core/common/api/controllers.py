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

from exordos_core.common.dm import models as common_models


class OwnedTagsControllerMixin:
    """Stamps the caller's identity onto the tags of a created row.

    Mix in ahead of the resource controller of a model based on
    `ModelWithReservedTags`. The tag is what tells a reconciler its own
    rows from everybody else's, so the client cannot write it: reserved
    tags are dropped from the request and the caller's own is added.

    A caller with no subject of its own leaves the row unowned, which
    every reconciler reads as "not mine to remove" -- until a write
    finds it that way and claims it, which the model decides.
    """

    def create(self, **kwargs):
        tags = kwargs.get("tags")
        if tags is not None and not isinstance(tags, list):
            # Not a tag list at all: hand it to the model, which answers
            # a bad request with a bad request rather than raising here.
            return super().create(**kwargs)
        tags = common_models.client_tags(tags)
        owner = common_models.caller_owner_tag()
        if owner:
            tags = tags + [owner]
        kwargs["tags"] = tags
        return super().create(**kwargs)
