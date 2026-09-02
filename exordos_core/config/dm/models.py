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

import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import orm

from exordos_core.common import constants as c
from exordos_core.common.dm import models as cm
from exordos_core.common.dm import targets as ct
from exordos_core.config import constants as cc


class AbstractContentor(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    def render(self) -> str:
        return ""


class TextBodyConfig(AbstractContentor):
    KIND = "text"

    content = properties.property(types.String(), required=True, default="")

    @classmethod
    def from_text(cls, text: str) -> "TextBodyConfig":
        return cls(content=text)

    def render(self) -> str:
        return self.content


class TemplateBodyConfig(AbstractContentor):
    KIND = "template"

    template = properties.property(types.String(), required=True, default="")
    variables = properties.property(types.Dict(), default=dict)

    def render(self) -> str:
        # TODO(akremenetsky): Will be added later
        raise NotImplementedError()


class OnChangeNoAction(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "no_action"


class OnChangeShell(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "shell"

    command = properties.property(
        types.String(max_length=262144), required=True, default=""
    )

    @classmethod
    def from_command(cls, command: str) -> "OnChangeShell":
        return cls(command=command)


class Config(
    cm.ModelWithFullAsset,
    models.ModelWithTags,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
    ua_models.TargetResourceSQLStorableMixin,
):
    __tablename__ = "config_configs"

    path = properties.property(
        types.String(min_length=1, max_length=255),
        required=True,
    )
    status = properties.property(
        types.Enum([s.value for s in cc.ConfigStatus]),
        default=cc.ConfigStatus.NEW.value,
    )
    target = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(ct.NodeTarget),
            types_dynamic.KindModelType(ct.NodeSetTarget),
        ),
        required=True,
    )
    body = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(TextBodyConfig),
            types_dynamic.KindModelType(TemplateBodyConfig),
        ),
        required=True,
    )
    on_change = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(OnChangeNoAction),
            types_dynamic.KindModelType(OnChangeShell),
        ),
        default=OnChangeNoAction,
    )
    mode = properties.property(
        cc.FileModeType(),
        default="0644",
    )
    owner = properties.property(
        types.String(max_length=128),
        default="root",
    )
    group = properties.property(
        types.String(max_length=128),
        default="root",
    )

    def target_nodes(self) -> tp.List[sys_uuid.UUID]:
        return self.target.target_nodes()

    def target_owners(self) -> tp.List[sys_uuid.UUID]:
        return self.target.owners()

    def render(self, node: sys_uuid.UUID) -> ua_models.TargetResource:
        # FIXME(akremenetsky): The `node` parameter will
        # be used in the future for node sets to make a
        # particular rendering for each node.
        # `content = self.body.render(node=node)`
        content = self.body.render()
        render_uuid = sys_uuid.uuid5(node, self.path)

        config_view = self.dump_to_simple_view(skip=("uuid",))
        render = Render.restore_from_simple_view(
            uuid=render_uuid,
            content=content,
            **{
                k: v
                for k, v in config_view.items()
                if k in Render.properties.properties
            },
        )

        resource = render.to_ua_resource("render", master=self.uuid)
        resource.status = cc.ConfigStatus.IN_PROGRESS.value
        return resource

    @classmethod
    def get_new_configs(cls, limit: int = c.DEFAULT_SQL_LIMIT) -> tp.List["Config"]:
        return cls.get_new_entities(cls.__tablename__, cc.CONFIG_KIND, limit=limit)

    @classmethod
    def get_updated_configs(cls, limit: int = c.DEFAULT_SQL_LIMIT) -> tp.List["Config"]:
        return cls.get_updated_entities(cls.__tablename__, cc.CONFIG_KIND, limit=limit)

    @classmethod
    def get_deleted_config_renders(
        cls, limit: int = c.DEFAULT_SQL_LIMIT
    ) -> tp.List[ua_models.TargetResource]:
        return cls.get_deleted_target_resources(
            cls.__tablename__, cc.CONFIG_KIND, limit=limit
        )


class Render(
    models.ModelWithUUID,
    ua_models.TargetResourceMixin,
):
    content = properties.property(types.String(), required=True)
    path = properties.property(
        types.String(min_length=1, max_length=512),
        required=True,
    )
    mode = properties.property(
        cc.FileModeType(),
        default="0644",
    )
    owner = properties.property(
        types.String(max_length=128),
        default="root",
    )
    group = properties.property(
        types.String(max_length=128),
        default="root",
    )
    on_change = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(OnChangeNoAction),
            types_dynamic.KindModelType(OnChangeShell),
        ),
        default=OnChangeNoAction,
    )
