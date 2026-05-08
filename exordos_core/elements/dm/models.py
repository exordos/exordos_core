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

import enum
from functools import partial
import logging
import re
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal import utils as sdk_utils
from gcl_sdk.agents.universal.dm import models as sdk_models
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.paas.dm import services as srv_models
from restalchemy.dm import filters as ra_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types as ra_types
from restalchemy.dm import types_dynamic as ra_types_dyn
from restalchemy.storage.sql import engines
from restalchemy.storage.sql import orm

from exordos_core.common import exceptions
from exordos_core.common import utils as cm_utils
from exordos_core.common.dm import models as cm
from exordos_core.common.dm import targets as ct
from exordos_core.elements import constants as cc
from exordos_core.elements.dm import utils
from exordos_core.vs.dm import models as vs_models

LOG = logging.getLogger(__name__)


class Status(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"


class AlwaysActiveStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"


class InstallTypes(str, enum.Enum):
    MANUAL = "MANUAL"
    AUTO_AS_DEPENDENCY = "AUTO_AS_DEPENDENCY"


class LinkResolver:
    """
    Resolves and transforms resource links by parsing link patterns
    and retrieving appropriate resources from the element engine.
    """

    def __init__(self, engine, element, full_link: str):
        super().__init__()
        self._element_engine = engine
        self._element = element
        self._full_link = full_link
        link = self._extract_resource_link(self._full_link)
        self._resource = (
            self._element_engine.get_resource_by_link(
                element=self._element,
                link=link,
            )
            if "." in link
            else self._element_engine.get_element(link)
        )
        self._discarded_part = self._full_link[len(link) :]

    @staticmethod
    def _extract_resource_link(full_link):
        full_link = full_link.split(":", 1)[0]

        parts = full_link.split(".")
        result_parts = []

        for part in parts:
            if part.startswith("$"):
                # When we encounter a part with a $, we add all the preceding
                #     parts.
                result_parts = parts[: parts.index(part) + 1]

        return ".".join(result_parts) if result_parts else None

    @property
    def full_link_original(self):
        """Returns resource.original.link + discarded part"""
        return self._resource.original.link + self._discarded_part

    @property
    def full_link(self):
        """Returns resource.link + discarded part"""
        return self._resource.link + self._discarded_part


class Manifest(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    models.DumpToSimpleViewMixin,
):
    __tablename__ = "em_manifests"

    STATUS = AlwaysActiveStatus

    status = properties.property(
        ra_types.Enum([s.value for s in Status]),
        default=STATUS.ACTIVE.value,
    )
    version = properties.property(
        ra_types.String(min_length=5, max_length=64),
        read_only=True,
        required=True,
    )
    schema_version = properties.property(
        ra_types.Integer(min_value=1, max_value=1),
        read_only=True,
        default=1,
    )
    api_version = properties.property(
        ra_types.AllowNone(ra_types.String(max_length=16, min_length=1)),
        default=None,
    )
    project_id = properties.property(
        ra_types.UUID(),
        read_only=True,
        default=utils.get_project_id(),
    )
    requirements = properties.property(
        ra_types.Dict(),
        mutable=True,
        default=dict,
    )
    resources = properties.property(
        ra_types.Dict(),
        mutable=True,
        default=dict,
    )
    exports = properties.property(
        ra_types.Dict(),
        mutable=True,
        default=dict,
    )
    imports = properties.property(
        ra_types.Dict(),
        mutable=True,
        default=dict,
    )
    openapi_spec = properties.property(
        ra_types.AllowNone(ra_types.String()),
        default=None,
    )

    def install(self) -> "Manifest":
        """
        Create an element from the manifest if it does not exist.

        """
        if Element.objects.get_one_or_none(filters={"name": ra_filters.EQ(self.name)}):
            raise ValueError(f"Element '{self.name}' already exists.")

        element_engine.load_from_database()

        element = Element(
            uuid=self.uuid,
            name=self.name,
            version=self.version,
            api_version=self.api_version,
            description=self.description,
            project_id=utils.get_project_id(),
            manifest=self,
        )
        element.save()
        element_engine.add_element(element)

        return self.apply_element(element)

    def upgrade(self) -> "Manifest":
        element = Element.objects.get_one_or_none(
            filters={"name": ra_filters.EQ(self.name)}
        )
        if not element:
            raise ValueError(
                f"Element '{self.name}' does not exist, please install it first."
            )

        element_engine.load_from_database()

        element.version = self.version
        element.api_version = self.api_version
        element.description = self.description
        element.save()
        return self.apply_element(element)

    def apply_requirements(self, element: "Element"):
        element.requirements = self.requirements
        element.save()

    def apply_imports(self, element: "Element"):
        existing_imports = {
            i.name: i
            for i in Import.objects.get_all(
                filters={"element": ra_filters.EQ(element.uuid)}
            )
        }

        for import_name, import_data in self.imports.items():
            import_from_element = element_engine.get_element(
                link=import_data["element"],
            )
            from_resource = element_engine.get_resource_by_export_link(
                manifest=self,
                from_element=import_from_element,
                link=import_data["link"],
            )
            import_kwargs = dict(
                name=import_name,
                element=element,
                from_element=import_from_element,
                from_resource=from_resource,
            )

            if "kind" in import_data:
                import_kwargs["kind"] = import_data["kind"]

            if import_model := existing_imports.pop(import_name, None):
                for k, v in import_kwargs.items():
                    setattr(import_model, k, v)
                import_model.save()
            else:
                import_model = Import(
                    uuid=cm_utils.get_or_create_uuid_from_dict(import_data),
                    **import_kwargs,
                )

                import_model.insert()
                resource = ImportedResource(
                    element=import_model.element,
                    resource=import_model.from_resource,
                    import_name=import_model.name,
                )
                element_engine.add_resource(resource)

        for imp in existing_imports.values():
            resource = ImportedResource(
                element=imp.element,
                resource=imp.from_resource,
                import_name=imp.name,
            )
            element_engine.delete_resource(resource)
            imp.delete()

    def apply_exports(self, element: "Element"):
        existing_exports = {
            i.name: i
            for i in Export.objects.get_all(
                filters={"element": ra_filters.EQ(element.uuid)}
            )
        }

        for export_name, export_data in self.exports.items():
            export_kwargs = dict(
                name=export_name,
                element=element,
                link=export_data["link"],
            )
            if "kind" in export_data:
                export_kwargs["kind"] = export_data["kind"]

            if export_model := existing_exports.pop(export_name, None):
                for k, v in export_kwargs.items():
                    setattr(export_model, k, v)
                export_model.save()
            else:
                export_model = Export(
                    uuid=cm_utils.get_or_create_uuid_from_dict(export_data),
                    **export_kwargs,
                )

                export_model.insert()
                element_engine.add_resource_by_export(export_model)

        for exp in existing_exports.values():
            element_engine.delete_resource_by_export(exp)
            exp.delete()

    def apply_resources(self, element: "Element"):
        existing_resources = {
            (i.resource_link_prefix, i.name): i
            for i in Resource.objects.get_all(
                filters={"element": ra_filters.EQ(element.uuid)}
            )
        }

        for resource_link_prefix, resources in self.resources.items():
            link_resolver = LinkResolver(
                element=element,
                engine=element_engine,
                full_link=resource_link_prefix,
            )
            for resource_name, resource_value in resources.items():
                resolved_link_prefix = link_resolver.full_link_original
                res_kwargs = dict(
                    element=element,
                    resource_link_prefix=resolved_link_prefix,
                    value=resource_value,
                )
                res_key = (resolved_link_prefix, resource_name)
                if resource := existing_resources.pop(res_key, None):
                    for k, v in res_kwargs.items():
                        setattr(resource, k, v)
                    resource.save()
                else:
                    resource = Resource(
                        uuid=cm_utils.get_or_create_uuid_from_dict(resource_value),
                        name=resource_name,
                        **res_kwargs,
                    )

                    # NOTE(efrolov): check that the element providing this
                    #   resource is installed
                    # TODO(akremenetsky): Temporarily disabled this check
                    # resource.get_provider_element()

                    resource.insert()
                    element_engine.add_resource(resource)

        for res in existing_resources.values():
            element_engine.delete_resource(res)
            res.delete()

    def apply_element(self, element: "Element") -> "Manifest":
        """
        Create or update imports, exports, resources from element.
        """

        self.apply_requirements(element)
        self.apply_imports(element)
        self.apply_resources(element)
        self.apply_exports(element)
        return self

    def uninstall(self) -> "Manifest":
        element_engine.load_from_database()

        elements = Element.objects.get_all(
            filters={
                "name": ra_filters.EQ(self.name),
                "version": ra_filters.EQ(self.version),
            }
        )
        for element in elements:
            element.delete()
            element_engine.remove_element(element)
        return self

    def view(self) -> dict:
        element_engine.load_schemas()

        manifest = self.dump_to_simple_view()
        full_schema = element_engine.full_schema

        utils.mutate_manifest(manifest, full_schema)

        return manifest

    def validate_schema_base(self) -> "Manifest":
        element_engine.load_schemas()
        utils.validate_manifest(self.dump_to_simple_view(), element_engine.base_schema)
        return self

    def validate_schema_full(self) -> "Manifest":
        element_engine.load_schemas()
        utils.validate_manifest(self.view(), element_engine.full_schema)
        return self


class Element(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    models.CustomPropertiesMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "em_elements"
    __custom_properties__ = {
        "link": ra_types.String(),
    }

    manifest = relationships.relationship(
        Manifest,
        prefetch=True,
        required=False,
    )
    STATUSES = Status
    INSTALL_TYPES = InstallTypes

    status = properties.property(
        ra_types.Enum([s.value for s in STATUSES]),
        default=STATUSES.NEW.value,
    )

    version = properties.property(
        ra_types.String(min_length=5, max_length=64),
        required=True,
    )

    api_version = properties.property(
        ra_types.AllowNone(ra_types.String(max_length=16, min_length=1)),
        default=None,
    )

    install_type = properties.property(
        ra_types.Enum([s.value for s in INSTALL_TYPES]),
        default=INSTALL_TYPES.MANUAL.value,
    )
    profile = relationships.relationship(vs_models.Profile, prefetch=True)
    project_id = properties.property(
        ra_types.UUID(),
        read_only=True,
        default=utils.get_project_id(),
    )
    requirements = properties.property(
        ra_types.Dict(),
        mutable=True,
        default=dict,
    )

    @property
    def link(self):
        return f"${self.name}"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._requirements = []

    def delete(self, session=None):
        for resource in Resource.objects.get_all(
            filters={"element": ra_filters.EQ(self)}
        ):
            resource.delete(session=session)
        super().delete(session=session)

    @property
    def original(self):
        return self

    def imports(self) -> tp.List["Import"]:
        return Import.objects.get_all(filters={"element": ra_filters.EQ(self)})

    def exports(self) -> tp.List["Export"]:
        return Export.objects.get_all(filters={"element": ra_filters.EQ(self)})

    def resources(self) -> tp.List["Resource"]:
        return Resource.objects.get_all(filters={"element": ra_filters.EQ(self)})


class ElementIncorrectStatusesView(
    models.ModelWithUUID,
    orm.SQLStorableMixin,
):
    __tablename__ = "em_incorrect_statuses_view"

    STATUS = Status

    name = properties.property(
        ra_types.String(min_length=1, max_length=255),
        read_only=True,
    )
    api_status = properties.property(
        ra_types.Enum([s.value for s in Status]),
        read_only=True,
    )
    actual_status = properties.property(
        ra_types.Enum([s.value for s in Status]),
        read_only=True,
    )

    def actualize_status(self):
        engine = engines.engine_factory.get_engine()
        with engine.session_manager() as s:
            s.execute(
                f"""
                UPDATE {Element.__tablename__}
                SET status = %s
                WHERE uuid = %s;
                """,
                (
                    self.actual_status,
                    self.uuid,
                ),
            )


class Requirement(
    models.ModelWithUUID,
    orm.SQLStorableMixin,
):
    __tablename__ = "em_requirements"

    element = relationships.relationship(
        Element,
        prefetch=True,
        required=True,
    )
    name = properties.property(
        ra_types.String(min_length=1, max_length=64),
        required=True,
    )
    from_version = properties.property(
        ra_types.AllowNone(ra_types.String(min_length=5, max_length=64)),
        default=None,
    )
    to_version = properties.property(
        ra_types.AllowNone(ra_types.String(min_length=5, max_length=64)),
        default=None,
    )


class Resource(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    models.CustomPropertiesMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "em_resources"
    __custom_properties__ = {
        # "project_id": ra_types.UUID(),
        # "target_state": ra_types.Dict(),
        # "target_hash": ra_types.String(min_length=32, max_length=32),
        # "actual_hash": ra_types.String(min_length=32, max_length=32),
        "link": ra_types.String(min_length=2, max_length=256),
        "kind": ra_types.String(min_length=2, max_length=256),
    }
    __allowed_methods_from_manifest__ = [
        "get_uri",
        "to_str",
        "index",
        "join",
    ]

    element = relationships.relationship(
        Element,
        prefetch=True,
        required=True,
    )
    name = properties.property(
        ra_types.String(min_length=1, max_length=255),
        read_only=True,
    )
    status = properties.property(
        ra_types.Enum([s.value for s in Status]),
        default=Status.NEW.value,
    )
    resource_link_prefix = properties.property(
        ra_types.String(min_length=1, max_length=256),
        required=True,
    )
    value = properties.property(
        ra_types.Dict(),
        required=True,
        mutable=True,
    )
    target_resource = relationships.relationship(
        sdk_models.TargetResource,
        default=None,
        prefetch=True,
    )
    actual_resource = relationships.relationship(
        sdk_models.Resource,
        default=None,
        prefetch=True,
    )
    full_hash = properties.property(
        ra_types.String(max_length=256),
        default="",
    )

    __inline_vars_regex__ = re.compile(r"[\\]{0,1}\{(.*?)}")

    def get_uri(self):
        version_prefix = ""
        if self.element.api_version:
            version_prefix = f"/{self.element.api_version}"
        parts = self.link.split(".")
        return f"{version_prefix}/{'/'.join(parts[1:-1])}/{self.uuid}"

    def to_str(self, field: str) -> str:
        if not self.actual_resource or not self.actual_resource.value:
            return ""
        return str(self.actual_resource.value[field])

    def index(self, field: str, idx: tp.Union[str, int] = 0) -> tp.Optional[str]:
        if not self.actual_resource or not self.actual_resource.value:
            return None
        try:
            return self.actual_resource.value[field][int(idx)]
        except (TypeError, IndexError):
            return None

    def join(self, field: str, separator: str = ",") -> str:
        if not self.actual_resource or not self.actual_resource.value:
            return ""
        return separator.join(self.actual_resource.value[field])

    def get_parameter_value(self, parameter: str):
        parts = parameter.split(":")
        resource_name = parts[0][1:]
        if resource_name != self.name:
            raise ValueError(
                f"Resource name `{resource_name}` does not match the"
                f" current resource name `{self.name}`"
            )
        resource_parameter_path = parts[1:]
        if len(resource_parameter_path) == 0:
            return self.actual_resource.value
        elif len(resource_parameter_path) == 1:
            if match := re.match(
                r"^(\w+)(?:\s*\(([^)]*)\))?$",
                resource_parameter_path[0],
            ):
                func_name = match.group(1)
                if func_name in self.__allowed_methods_from_manifest__:
                    func = getattr(self, func_name)
                    if match.group(2):
                        result = [x.strip() for x in match.group(2).split(",")]
                        return func(*result)
                    return func()

        result_value = self.get_actual_state_safe()
        for part in resource_parameter_path:
            result_value = result_value[part]
        return result_value

    def get_actual_state_safe(self):
        if self.actual_resource is None:
            return self.render_target_state()
        return self.actual_resource.value

    # Support inplace vars with f"" syntax
    def _fstring_replacement_callback(self, match, engine):
        # Just remove escape syntax
        if match.group(0).startswith("\\{") and match.group(0)[-1] == "}":
            return match.group(0)[1:]
        var = match.group(1)
        link = utils.ResourceLink(var)
        try:
            resource = engine.get_resource_by_link(
                element=self.element,
                link=link.location,
            )
            value = resource.get_parameter_value(
                parameter=link.parameter,
            )
            return str(value)
        except ValueError as e:
            raise ValueError(
                f"Can't render value `{var}` for resource `{repr(self)}` by reason: {e}"
            )

    def _render_value(self, value, engine):
        if value.startswith("$"):
            link = utils.ResourceLink(value)
            try:
                resource = engine.get_resource_by_link(
                    element=self.element,
                    link=link.location,
                )
                return resource.get_parameter_value(
                    parameter=link.parameter,
                )
            except ValueError as e:
                raise ValueError(
                    f"Can't render value `{value}` for resource"
                    f" `{repr(self)}` by reason: {e}"
                )
        elif value.startswith('f"'):
            return re.sub(
                self.__inline_vars_regex__,
                partial(self._fstring_replacement_callback, engine=engine),
                value[2 : value.rfind('"')],
            )

        return value

    def _recursive_render(self, data, engine):
        if isinstance(data, dict):
            result = {}
            for key, value in data.items():
                if isinstance(
                    value,
                    (
                        dict,
                        list,
                    ),
                ):
                    value = self._recursive_render(value, engine)
                    result[key] = value
                elif isinstance(value, str):
                    result[key] = self._render_value(value, engine)
                else:
                    result[key] = value
        elif isinstance(data, list):
            result = []
            for item in data:
                if isinstance(
                    item,
                    (
                        dict,
                        list,
                    ),
                ):
                    item = self._recursive_render(item, engine)
                    result.append(item)
                elif isinstance(item, str):
                    result.append(self._render_value(item, engine))
                else:
                    result.append(item)
        else:
            result = data
        return result

    def render_target_state(self, engine=None):
        engine = engine or element_engine
        res = self._recursive_render(self.value, engine)
        # uuid is mandatory to find already created resources in services
        if "uuid" not in res:
            res["uuid"] = str(self.uuid)
        return res

    @property
    def link(self) -> str:
        return f"{self.resource_link_prefix}.${self.name}"

    def get_provider_element(self):
        namespace_name = self.resource_link_prefix.split(".", 2)[0]
        namespace = element_engine.get_namespace(namespace_name)
        return namespace.element

    @property
    def kind(self):
        parts = [
            p for p in self.resource_link_prefix.split(".")[1:] if not p.startswith("$")
        ]

        provider_element = self.get_provider_element()
        return f"em_{provider_element.name}_{'_'.join(parts)}"

    def calculate_full_hash(self, target_state=None):
        if self.actual_resource is not None:
            return self.actual_resource.full_hash
        target_state = target_state or self.render_target_state()
        return sdk_utils.calculate_hash(target_state)

    def _find_actual_resource(self) -> sdk_models.Resource | None:
        if self.actual_resource is None:
            for actual_resource in sdk_models.Resource.objects.get_all(
                filters={
                    "uuid": ra_filters.EQ(self.uuid),
                    "kind": ra_filters.EQ(self.kind),
                },
            ):
                return actual_resource

        return self.actual_resource

    def actualize(self):
        try:
            target_state = self.render_target_state()
        except KeyError as e:
            LOG.debug(
                "Target state is not available for resource %s by reason: %r",
                self,
                str(e),
            )
            return
        self.actual_resource = self._find_actual_resource()
        hash = sdk_utils.calculate_hash(target_state)
        self.full_hash = self.calculate_full_hash(target_state)
        if self.target_resource is None:
            res_uuid = sdk_models.TargetResource.gen_res_uuid(self.uuid, self.kind)
            target_resource = sdk_models.TargetResource(
                uuid=self.uuid,
                kind=self.kind,
                res_uuid=res_uuid,
                value=target_state,
                hash=hash,
                full_hash=self.full_hash,
                tracked_at=self.updated_at,
            )
            target_resource.insert()
            self.target_resource = target_resource
            self.update()
            LOG.debug("Target resource %s has been created.", target_resource)
        elif self.target_resource.hash != hash:
            self.target_resource.value = target_state
            self.target_resource.calculate_hash()
            self.target_resource.full_hash = self.full_hash
            self.target_resource.tracked_at = self.updated_at
            self.target_resource.update()
            LOG.debug(
                "Target resource %s has been updated.",
                self.target_resource,
            )
        elif self.target_resource.full_hash != self.full_hash:
            self.target_resource.full_hash = self.full_hash
            self.target_resource.update()
            LOG.debug(
                "Target resource %s full hash has been updated.",
                self.target_resource,
            )
        elif self.target_resource.tracked_at != self.updated_at:
            self.target_resource.tracked_at = self.updated_at
            self.target_resource.update()
            LOG.debug(
                "Target resource %s tracked_at has been updated.",
                self.target_resource,
            )
        else:
            LOG.debug(
                "Target resource %s is actual state.",
                self.target_resource,
            )
        self.update()

    def delete(self, session=None):
        super().delete(session=session)
        for ts in sdk_models.TargetResource.objects.get_all(
            filters={
                "uuid": ra_filters.EQ(self.uuid),
                "kind": ra_filters.EQ(self.kind),
            }
        ):
            ts.delete(session=session)

    @property
    def original(self):
        return self


class ExportEnum(str, enum.Enum):
    RESOURCE = "resource"


class Export(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "em_exports"

    element = relationships.relationship(
        Element,
        required=True,
    )
    name = properties.property(
        ra_types.String(min_length=1, max_length=255),
        read_only=True,
    )
    kind = properties.property(
        ra_types.Enum([s.value for s in ExportEnum]),
        default=ExportEnum.RESOURCE.value,
    )
    link = properties.property(
        ra_types.String(min_length=2, max_length=255),
        required=True,
    )


class ImportEnum(str, enum.Enum):
    RESOURCE = "resource"


class Import(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    models.CustomPropertiesMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "em_imports"

    __custom_properties__ = {
        "link": ra_types.String(min_length=2, max_length=256),
    }

    element = relationships.relationship(
        Element,
        required=True,
    )
    from_element = relationships.relationship(
        Element,
        required=True,
    )
    from_resource = relationships.relationship(
        Resource,
        required=True,
    )
    name = properties.property(
        ra_types.String(min_length=1, max_length=255),
        read_only=True,
    )
    kind = properties.property(
        ra_types.Enum([s.value for s in ImportEnum]),
        default=ImportEnum.RESOURCE.value,
    )

    @property
    def link(self):
        return f"{self.element.link}.imports.${self.name}"


class ImportedResource:
    def __init__(self, element, resource, import_name):
        super().__init__()
        self._element = element
        self._resource = resource
        self._import_name = import_name

    def __getattr__(self, name):
        return getattr(self._resource, name)

    def get_parameter_value(self, parameter):
        return type(self._resource).get_parameter_value(self, parameter)

    @property
    def element(self):
        return self._element

    @property
    def name(self):
        return self._import_name

    @property
    def link(self) -> str:
        return f"{self.element.link}.imports.${self.name}"


class OutdatedResources(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "em_outdated_resources_view"

    em_resource = relationships.relationship(
        Resource,
        default=None,
        prefetch=True,
    )
    target_resource = relationships.relationship(
        sdk_models.TargetResource,
        default=None,
        prefetch=True,
    )


class ResourceIncorrectStatusesView(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "em_incorrect_resource_statuses_view"

    current_status = properties.property(
        ra_types.String(),
        required=True,
    )
    actual_status = properties.property(
        ra_types.AllowNone(ra_types.String()),
        default=None,
    )

    def actualize_status(self, session):
        new_status = Status.NEW
        if self.actual_status == Status.ACTIVE:
            new_status = Status.ACTIVE
        elif self.actual_status is not None:
            new_status = Status.IN_PROGRESS

        session.execute(
            f'UPDATE {Resource.__tablename__} SET status=%s WHERE "uuid"=%s;',
            (new_status, self.uuid),
        )


class Namespace:
    def __init__(self, element):
        super().__init__()
        self._element = element
        # NOTE(efrolov): map of resources by link string
        self._namespace_resources = {}

    @property
    def element(self):
        return self._element

    def add_resource(self, resource: Resource | ImportedResource) -> None:
        if resource.link in self._namespace_resources:
            raise ValueError(
                f"Resource with link string '{resource.link}' in element '{resource.element.name}' already exists."
            )
        self._namespace_resources[resource.link] = resource

    def get_resources(self):
        return list(self._namespace_resources.values())

    def delete_resource(self, resource: Resource | ImportedResource) -> None:
        if resource.link not in self._namespace_resources:
            raise ValueError(
                f"Resource with link string '{resource.link}' in element '{resource.element.name}' does not exist."
            )
        del self._namespace_resources[resource.link]

    def get_resource_by_link(self, link):
        clear_link = utils.clear_parameters(link)
        if clear_link not in self._namespace_resources:
            raise ValueError(
                f"Resource with link string '{clear_link}' does not exist."
            )
        return self._namespace_resources[clear_link]


class ElementEngine:
    def __init__(self):
        super().__init__()
        self._namespaces: tp.Dict[str, Namespace] = {}
        self._resource_exports: tp.Dict[str, Resource] = {}
        self.base_schema: tp.Dict[str, tp.Any] = {}
        self.full_schema: tp.Dict[str, tp.Any] = {}

    def load_schemas(self) -> None:
        if not self.base_schema:
            self.base_schema = utils.load_base_manifest_schema()
        if not self.full_schema:
            self.full_schema = utils.load_full_manifest_schema()

    def get_namespace(self, name: str) -> Namespace:
        try:
            return self._namespaces[name]
        except KeyError:
            raise exceptions.NamespaceNotFound(name=name)

    def load_from_database(self) -> None:
        self._namespaces = {}
        self._resource_exports = {}
        for element in Element.objects.get_all():
            self.add_element(element)

        for import_ in Import.objects.get_all():
            if import_.kind == ImportEnum.RESOURCE.value:
                resource = ImportedResource(
                    element=import_.element,
                    resource=import_.from_resource,
                    import_name=import_.name,
                )
                self.add_resource(resource)
            else:
                raise ValueError(
                    f"Unsupported import type '{import_.kind}' for import "
                    f"'{import_.name}'. Only '{ImportEnum.RESOURCE.value}' "
                    f"imports are currently supported."
                )

        for resource in Resource.objects.get_all():
            self.add_resource(resource)

        for export in Export.objects.get_all():
            if export.kind == ExportEnum.RESOURCE.value:
                self.add_resource_by_export(export)
            else:
                raise ValueError(
                    f"Unsupported export type '{export.kind}' for export "
                    f"'{export.name}'. Only '{ExportEnum.RESOURCE.value}' "
                    f"exports are currently supported."
                )

    def add_resource(self, resource: Resource | ImportedResource) -> None:
        element = resource.element
        if element.link not in self._namespaces:
            ValueError(
                f"The element '{element}' is unknown. Please add the element"
                " before adding resources to it."
            )
        namespace = self._namespaces[resource.element.link]
        namespace.add_resource(resource)

    def delete_resource(self, resource: Resource | ImportedResource) -> None:
        namespace = self._namespaces[resource.element.link]
        namespace.delete_resource(resource)

    def get_resources(self) -> tp.List["Resource"]:
        result = []
        for namespace in self._namespaces.values():
            result.extend(namespace.get_resources())
        return result

    def get_resource_by_link(self, element: "Element", link: str) -> "Resource":
        if element.link not in self._namespaces:
            raise ValueError(
                f"Can't load element {element}. Element"
                f" {self._namespaces[element.link].element} is not found."
            )
        namespace = self._namespaces[element.link]
        return namespace.get_resource_by_link(link)

    def add_element(self, element: "Element") -> None:
        if element.link in self._namespaces:
            raise ValueError(
                f"Can't load element {element}. Element"
                f" {self._namespaces[element.link].element} already exists"
                " with the same UUID."
            )
        self._namespaces[element.link] = Namespace(element)

    def get_element(self, link: str) -> "Element":
        return self.get_namespace(name=link).element

    def remove_element(self, element: "Element") -> None:
        if element.link not in self._namespaces:
            raise ValueError(f"Can't remove element {element}. Element does not exist.")

        del self._namespaces[element.link]

    def add_resource_by_export(self, export: "Export") -> None:
        element = export.element
        resource = self.get_resource_by_link(
            element=element,
            link=export.link,
        )

        if export.link in self._resource_exports:
            raise ValueError(
                f"Resource export with link '{export.link}' already exists."
            )
        self._resource_exports[export.link] = resource

    def delete_resource_by_export(self, export: "Export") -> None:
        del self._resource_exports[export.link]

    def get_resource_by_export_link(
        self, manifest: "Manifest", from_element: "Element", link: str
    ) -> "Resource":
        # Implement check element here for export resources
        if link not in self._resource_exports:
            raise ValueError(
                f"Resource {link} in manifest {manifest.name} ({manifest.version}) is not in export list "
                f"in element {from_element.name}"
            )
        return self._resource_exports[link]


element_engine = ElementEngine()


class ServiceTarget(srv_models.ServiceTarget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Check for Service existence
        if not Service.objects.get_one_or_none(
            filters={"uuid": ra_filters.EQ(self.service)}
        ):
            raise ValueError(
                "Service %s does not exist. Please create it first." % self.service
            )

    @classmethod
    def from_service(cls, service: sys_uuid.UUID) -> "ServiceTarget":
        return cls(service=service)

    def target_services(self) -> tp.List[sys_uuid.UUID]:
        return [self.service]

    def owners(self) -> tp.List[sys_uuid.UUID]:
        """It's the simplest case with an ordinary service target.

        In that case, the owner and target is the service itself.
        If owners are deleted, the service will be deleted as well.
        """
        return [self.service]

    def _fetch_services(self) -> tp.List["Service"]:
        return Service.objects.get_all(filters={"uuid": str(self.service)})

    def are_owners_alive(self) -> bool:
        return bool(self._fetch_services())

    def get_dp_obj(self):
        service = Service.objects.get_one(filters={"uuid": ra_filters.EQ(self.service)})
        return srv_models.ServiceDPTarget(
            service=self.service, service_name=service.name
        )


class Service(
    cm.ModelWithFullAsset,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
    ua_models.TargetResourceSQLStorableMixin,
):
    __tablename__ = "em_services"

    name = properties.property(
        ra_types.BaseCompiledRegExpType(re.compile(r"^[A-Za-z0-9_-]{0,100}$")),
        default="",
    )
    path = properties.property(
        ra_types.String(min_length=1, max_length=255),
        required=True,
    )
    status = properties.property(
        ra_types.Enum([s.value for s in cc.ServiceStatus]),
        default=cc.ServiceStatus.NEW.value,
    )
    target_status = properties.property(
        ra_types.Enum([s.value for s in srv_models.ServiceTargetStatus]),
        default=srv_models.ServiceTargetStatus.enabled.value,
    )
    target = properties.property(
        ra_types_dyn.KindModelSelectorType(
            ra_types_dyn.KindModelType(ct.NodeTarget),
            ra_types_dyn.KindModelType(ct.NodeSetTarget),
        ),
        required=True,
    )
    user = properties.property(
        ra_types.String(min_length=1, max_length=255),
        default="root",
    )
    group = properties.property(
        ra_types.AllowNone(ra_types.String(min_length=1, max_length=255)),
        default=None,
    )
    service_type = properties.property(
        ra_types_dyn.KindModelSelectorType(
            ra_types_dyn.KindModelType(srv_models.ServiceTypeSimple),
            ra_types_dyn.KindModelType(srv_models.ServiceTypeOneshot),
            ra_types_dyn.KindModelType(srv_models.ServiceTypeMonopoly),
            ra_types_dyn.KindModelType(srv_models.ServiceTypeMonopolyOneshot),
        ),
        required=True,
    )
    before = properties.property(
        ra_types.TypedList(
            ra_types_dyn.KindModelSelectorType(
                ra_types_dyn.KindModelType(srv_models.CmdShell),
                ra_types_dyn.KindModelType(ServiceTarget),
            ),
        ),
        default=[],
    )
    after = properties.property(
        ra_types.TypedList(
            ra_types_dyn.KindModelSelectorType(
                ra_types_dyn.KindModelType(srv_models.CmdShell),
                ra_types_dyn.KindModelType(ServiceTarget),
            ),
        ),
        default=[],
    )

    def target_nodes(self) -> tp.List[sys_uuid.UUID]:
        return self.target.target_nodes()

    def target_owners(self) -> tp.List[sys_uuid.UUID]:
        return self.target.owners()
