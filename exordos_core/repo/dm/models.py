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

import datetime
import enum
import logging
import typing as tp
from urllib.parse import urljoin

from restalchemy.dm import filters as ra_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types as ra_types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import orm

from exordos_core.common import utils
from exordos_core.repo import constants as rc

LOG = logging.getLogger(__name__)

if tp.TYPE_CHECKING:
    from exordos_core.repo.drivers.base import AbstractProxyRepoDriver


class SyncMode(str, enum.Enum):
    COPY = "copy"
    LAZY = "lazy"


class RepositoryStatus(str, enum.Enum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class RepoElementStatus(str, enum.Enum):
    NEW = "NEW"
    AVAILABLE = "AVAILABLE"
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    ERROR = "ERROR"


class RepoElementInstallationState(str, enum.Enum):
    INSTALLED = "INSTALLED"
    UNINSTALLED = "UNINSTALLED"


class AbstractDriverSpec(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    """Base class for all repository driver specs.

    Provides an interface for redacting sensitive fields (e.g. credentials)
    when exposing driver specs through API responses.

    Subclasses should declare sensitive property names in
    :attr:`SENSITIVE_FIELDS`.
    """

    SENSITIVE_FIELDS: tp.ClassVar[frozenset[str]] = frozenset()

    def sanitize_in_place(self) -> None:
        """Redact sensitive fields on this spec instance in place.

        Each sensitive field that has a non-``None`` value is replaced
        with the string ``"***"`` directly on the model.
        """
        for field in self.SENSITIVE_FIELDS:
            if getattr(self, field, None) is not None:
                setattr(self, field, "***")


class NginxDriverSpec(AbstractDriverSpec):
    KIND = "nginx"
    INVENTORY_FILE = "inventory.json"

    SENSITIVE_FIELDS = frozenset({"username", "password"})

    url = properties.property(
        ra_types.String(max_length=2048),
        required=True,
    )
    username = properties.property(
        ra_types.AllowNone(ra_types.String(max_length=255)),
        default=None,
    )
    password = properties.property(
        ra_types.AllowNone(ra_types.String(max_length=255)),
        default=None,
    )

    @property
    def inventory_path(self) -> str:
        base_url = self.url if self.url.endswith("/") else f"{self.url}/"
        return urljoin(base_url, self.INVENTORY_FILE)


class BootstrapDriverSpec(AbstractDriverSpec):
    KIND = "bootstrap"

    manifests_dir = properties.property(
        ra_types.String(max_length=2048),
        required=True,
    )

    @property
    def inventory_path(self) -> str:
        return ""


class DummyMigrationDriverSpec(AbstractDriverSpec):
    KIND = "dummy_migration"

    @property
    def inventory_path(self) -> str:
        return ""


class DatabaseDriverSpec(AbstractDriverSpec):
    KIND = "database"

    @property
    def inventory_path(self) -> str:
        return ""


class Repository(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """Repository model for managing external repositories.

    Represents external repositories that contain elements. Used for syncing
    external resources into the Exordos platform with different
    sync modes and drivers.

    Attributes:
        status: Current status of the repository
        priority: Priority for repository processing (0-4096, higher = more priority)
        refresh_rate: Refresh interval in seconds (0 = disabled)
        sync_mode: Sync mode (copy or lazy)
        driver_spec: Driver configuration for repository access. Driver is the
        mechanism for interacting with remote repositories. Currently supports only
        Nginx-based remote repositories, but future implementations may include
        local filesystem repositories, S3 storage, SSH-based repositories,
        and other remote repository types.
    """

    __tablename__ = "repo_repositories"
    __driver_map__ = {}

    status = properties.property(
        ra_types.Enum([s.value for s in RepositoryStatus]),
        default=RepositoryStatus.NEW.value,
    )
    priority = properties.property(
        ra_types.Integer(min_value=0, max_value=16384),
        default=8192,
    )
    refresh_rate = properties.property(
        ra_types.Integer(min_value=0),
        default=3600,
    )
    sync_mode = properties.property(
        ra_types.Enum([s.value for s in SyncMode]),
        default=SyncMode.LAZY.value,
    )
    driver_spec = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(NginxDriverSpec),
            types_dynamic.KindModelType(BootstrapDriverSpec),
            types_dynamic.KindModelType(DummyMigrationDriverSpec),
            types_dynamic.KindModelType(DatabaseDriverSpec),
        ),
        default=NginxDriverSpec,
        required=False,
    )
    next_refresh = properties.property(
        ra_types.UTCDateTimeZ(),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    @property
    def repo_uri(self) -> str:
        """Return the URI of the repository."""
        return self.load_driver().repo_uri

    def load_driver(self) -> "AbstractProxyRepoDriver":
        """
        Load the driver for the repository.

        This method will try to load all drivers from the
        ``exordos.repo_proxy.drivers`` entry point group and try to
        instantiate them with the current repository. If a driver is
        successfully loaded, it is stored in a cache for faster access.

        If no driver is found, a ValueError is raised.

        :return: The loaded driver instance
        :raises ValueError: If no driver is found
        """
        driver_key = str(self.driver_spec)

        if driver_key in self.__driver_map__:
            return self.__driver_map__[driver_key]

        ep_group = utils.load_group_from_entry_point(rc.EP_REPO_DRIVERS)
        for e in ep_group:
            try:
                class_ = e.load()
                driver = class_(self)
                self.__driver_map__[driver_key] = driver
                return driver
            except Exception:
                # Just try another driver
                pass

        raise ValueError(f"Driver for spec '{self.driver_spec}' not found")

    def iter_elements_in_inventory(
        self, inventory: dict | None = None
    ) -> tp.Iterator["RepoElement"]:
        """Iterate over elements from the repository inventory.

        This is a generator that yields elements one by one, which is useful
        for processing large inventories without loading everything into memory.

        :return: Iterator of :class:`RepoElement` instances
        """
        if inventory is None:
            driver = self.load_driver()
            inventory = driver.get_inventory()

        for name, versions in inventory.get("elements", {}).items():
            for version in versions:
                element = RepoElement(
                    name=name,
                    version=version,
                    description="",
                    project_id=self.project_id,
                    installation_state=RepoElementInstallationState.UNINSTALLED.value,
                    repository=self,
                )
                yield element

    def actualize_element(self, element: "RepoElement") -> None:
        """Actualize the element."""
        driver = self.load_driver()
        new_element, new_artifacts = driver.get_element(element.name, element.version)
        element.specification = new_element.specification
        element.inventory = new_element.inventory
        element.manifest = new_element.manifest
        element.save()

        # Delete old artifacts before saving new ones to avoid unique
        # constraint violation on (element, urn).
        old_artifacts = RepoArtifact.objects.get_all(
            filters={"element": ra_filters.EQ(element.uuid)}
        )
        for artifact in old_artifacts:
            artifact.delete()

        # Save new artifacts
        for artifact in new_artifacts:
            artifact.element = element
            artifact.save()

    def refresh(self) -> None:
        """Trigger repository refresh by setting next_refresh to current time."""
        if self.refresh_rate:
            self.next_refresh = datetime.datetime.now(datetime.timezone.utc)
            self.update()

    def upload(
        self,
        element_name: str,
        element_version: str,
        manifest: dict,
        description: str = "",
    ) -> "RepoElement":
        """Upload element to repository.

        Args:
            element_name: Element name
            element_version: Element version
            manifest: Element manifest dict
            description: Element description (optional)

        Returns:
            Created RepoElement instance

        Raises:
            ValueError: If upload is not supported by driver
        """
        driver = self.load_driver()

        # Check if driver supports upload
        if not driver.can_upload_element(element_name, element_version):
            raise ValueError("Upload is not supported by this repository driver")

        # Create element
        element = RepoElement(
            name=element_name,
            version=element_version,
            description=description,
            project_id=self.project_id,
            installation_state=RepoElementInstallationState.UNINSTALLED.value,
            repository=self,
            manifest=manifest or {},
        )

        # Upload element to repository
        driver.upload_element(element)

        # Save element to database
        element.save()

        LOG.info(
            "Uploaded element %s:%s to repository %s",
            element_name,
            element_version,
            self.repo_uri,
        )

        return element


class RepoElement(
    models.ModelWithUUID,
    models.ModelWithRequiredNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """Repository Element model for individual items within repositories.

    Represents individual elements (manifests, specifications, configurations)
    that are synced from external repositories. Each element has a name, version
    and other metadata. When repository is in lazy mode, manifest, specification,
    and inventory fields will be empty and will be downloaded only during
    actual element installation.

    Attributes:
        repository: Link to the parent repository
        version: Version string of the element
        status: Current status
            - NEW: Element is new and not processed yet
            - ACTIVE: Element is active and available for use
            - IN_PROGRESS: Element processing is in progress
            - ERROR: Element processing failed
        installation_state: Current installation state
            - INSTALLED: Element is installed
            - UNINSTALLED: Element is not installed
        manifest: YAML manifest file content (large dict)
        specification: YAML specification file content (large dict)
        inventory: YAML inventory file content (large dict)
        dependencies: Computed property that reads manifest["requirements"] and
            converts constraint format (from_version -> >=, to_version -> <=, version -> ==)
        element: UUID of the installed runtime element (optional)
    """

    __tablename__ = "repo_elements"

    repository = relationships.relationship(
        Repository,
        prefetch=True,
        required=True,
    )
    version = properties.property(
        ra_types.String(max_length=255),
        required=True,
    )
    status = properties.property(
        ra_types.Enum([s.value for s in RepoElementStatus]),
        default=RepoElementStatus.NEW.value,
    )
    installation_state = properties.property(
        ra_types.Enum([s.value for s in RepoElementInstallationState]),
        default=RepoElementInstallationState.UNINSTALLED.value,
    )
    manifest = properties.property(
        ra_types.Dict(),
        required=False,
        default=dict,
    )
    specification = properties.property(
        ra_types.Dict(),
        required=False,
        default=dict,
    )
    inventory = properties.property(
        ra_types.Dict(),
        required=False,
        default=dict,
    )
    element = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
    )

    @property
    def dependencies(self) -> dict[str, dict[str, str]]:
        """Compute dependencies from manifest requirements.

        Reads manifest["requirements"] and converts the format from:
        {"core": {"from_version": "0.0.0"}}
        to constraint format:
        {"core": {">=": "0.0.0"}}

        Supported constraint keys:
        - from_version: converted to >=
        - to_version: converted to <
        - version: converted to ==

        Returns:
            Dict mapping element names to constraint dicts

        Raises:
            KeyError: If "requirements" not found in manifest
        """
        if "requirements" not in self.manifest:
            return {}

        requirements = self.manifest["requirements"]
        result = {}

        constraint_mapping = {
            "from_version": ">=",
            "to_version": "<",
            "version": "==",
        }

        for elem_name, constraint_spec in requirements.items():
            if not isinstance(constraint_spec, dict):
                continue

            constraint_dict = {}
            for key, value in constraint_spec.items():
                if key in constraint_mapping:
                    constraint_dict[constraint_mapping[key]] = value

            if constraint_dict:
                result[elem_name] = constraint_dict

        return result

    def install(self) -> "RepoElement":
        if self.installation_state != RepoElementInstallationState.UNINSTALLED:
            raise ValueError("Element must be uninstalled")

        # Check there is no installed element with the same name
        existing = RepoElement.objects.get_all(
            filters={
                "name": ra_filters.EQ(self.name),
                "installation_state": ra_filters.EQ(
                    RepoElementInstallationState.INSTALLED.value
                ),
            }
        )
        if existing:
            raise ValueError("Element with the same name is already installed")

        self.installation_state = RepoElementInstallationState.INSTALLED.value
        self.update()
        return self

    def uninstall(self) -> "RepoElement":
        if self.installation_state != RepoElementInstallationState.INSTALLED:
            raise ValueError("Element must be installed")

        # Check that no other elements depend on this one. The dependency
        # bindings table records transitive dependencies, so if any record
        # references this element as depends_on, it means another installed
        # element still relies on it and uninstallation must be rejected.
        dependents = RepoElementDepsBinding.objects.get_all(
            filters={"depends_on": ra_filters.EQ(self.uuid)}
        )
        if dependents:
            raise ValueError(
                "Element cannot be uninstalled: other elements depend on it"
            )

        self.installation_state = RepoElementInstallationState.UNINSTALLED.value
        self.element = None
        self.update()

        # Remove this element's dependency bindings so that the elements it
        # depended on are no longer blocked from uninstallation.
        own_bindings = RepoElementDepsBinding.objects.get_all(
            filters={"element": ra_filters.EQ(self.uuid)}
        )
        for binding in own_bindings:
            binding.delete()

        return self

    def upgrade(self, target: str) -> "RepoElement":
        if self.element is None:
            raise ValueError("Element must be installed to upgrade")

        target_element = RepoElement.objects.get_one(
            filters={
                "uuid": ra_filters.EQ(target),
                "name": ra_filters.EQ(self.name),
            }
        )
        if (
            target_element.installation_state
            != RepoElementInstallationState.UNINSTALLED
        ):
            raise ValueError("Target element must be uninstalled")
        runtime_element = self.element
        self.installation_state = RepoElementInstallationState.UNINSTALLED.value
        self.update()

        target_element.installation_state = RepoElementInstallationState.INSTALLED.value
        target_element.element = runtime_element
        target_element.update()
        return self

    def edit(self, manifest: dict) -> "RepoElement":
        """Edit element manifest.

        Args:
            manifest: New manifest dict

        Raises:
            ValueError: If manifest name or version does not match element name/version
        """
        # Validate that name and version in manifest match the element
        manifest_name = manifest.get("name")
        manifest_version = manifest.get("version")

        if manifest_name != self.name:
            raise ValueError(
                f"Manifest name '{manifest_name}' does not match element name '{self.name}'"
            )

        if manifest_version != self.version:
            raise ValueError(
                f"Manifest version '{manifest_version}' does not match element version '{self.version}'"
            )

        self.manifest = manifest
        self.update()
        return self

    @classmethod
    def from_inventory(
        cls, repository: "Repository", inventory: dict
    ) -> tuple["RepoElement", list["RepoArtifact"]]:
        """Create RepoElement and RepoArtifacts from inventory dict.

        The inventory format is:

            {
                "name": "core",
                "version": "0.1.12-dev+...",
                "artifacts": ["openapi_user.yaml"],
                "configs": [],
                "images": ["exordos-core.qcow2"],
                "manifests": ["core.yaml"],
                "templates": [],
                "index": {
                    "artifacts": {"<uuid>": "openapi_user.yaml"},
                    "configs": {},
                    "images": {"<uuid>": "exordos-core.qcow2"},
                    "manifests": {"<uuid>": "core.yaml"},
                    "templates": {}
                }
            }

        The returned element has ``project_id`` and ``repository`` unset;
        the caller (driver) is responsible for setting them before saving.

        :param inventory: Inventory dict for a single element
        :return: Tuple of (RepoElement, list of RepoArtifact)
        """
        driver = repository.load_driver()

        element = cls(
            name=inventory["name"],
            version=inventory["version"],
            description=inventory.get("description", ""),
            installation_state=RepoElementInstallationState.UNINSTALLED.value,
            inventory=inventory,
            repository=repository,
            project_id=repository.project_id,
        )

        artifacts: list[RepoArtifact] = []
        for category, entries in inventory.get("index", {}).items():
            for art_key, art_name in entries.items():
                urn = utils.urn(category, art_key)
                uri = driver.artifact_uri(
                    element.name, element.version, category, art_name
                )
                artifacts.append(
                    RepoArtifact(
                        element=element,
                        urn=urn,
                        uri=uri,
                        project_id=repository.project_id,
                    )
                )

        return element, artifacts


class RepoArtifact(
    models.ModelWithUUID,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """Repository Artifact model for individual files within elements.

    Represents artifacts (files, archives, binaries) that belong to a
    repository element. Each artifact is identified by a URN and can be
    accessed via its URI.

    Attributes:
        element: Link to the parent repository element
        urn: Uniform Resource Name identifying the artifact
        uri: Uniform Resource Identifier for artifact access
    """

    __tablename__ = "repo_artifacts"

    element = relationships.relationship(
        RepoElement,
        prefetch=True,
        required=True,
    )
    urn = properties.property(
        ra_types.String(max_length=2048),
        required=True,
    )
    uri = properties.property(
        ra_types.String(max_length=2048),
        required=True,
    )


class RepoElementDepsBinding(
    models.ModelWithUUID,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """Repository element dependencies binding model.

    Stores transitive dependency relationships between repo elements.
    When an element is installed, records are created for each of its
    dependencies (including transitive ones). For example, if C depends
    on B and B depends on A, two records are created for C:
    (C, B) and (C, A).

    Attributes:
        element: The element that has dependencies (FK to repo_elements)
        depends_on: The element that is depended upon (FK to repo_elements)
    """

    __tablename__ = "repo_element_deps_bindings"

    element = relationships.relationship(
        RepoElement,
        prefetch=True,
        required=True,
    )
    depends_on = relationships.relationship(
        RepoElement,
        prefetch=True,
        required=True,
    )
