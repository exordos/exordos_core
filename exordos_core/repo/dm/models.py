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


class NginxDriverSpec(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "nginx"
    INVENTORY_FILE = "inventory.json"

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


class BootstrapDriverSpec(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "bootstrap"

    manifests_dir = properties.property(
        ra_types.String(max_length=2048),
        required=True,
    )

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
        new_element = driver.get_element(element.name, element.version)
        element.specification = new_element.specification
        element.inventory = new_element.inventory
        element.manifest = new_element.manifest

    def refresh(self) -> None:
        """Trigger repository refresh by setting next_refresh to current time."""
        if self.refresh_rate:
            self.next_refresh = datetime.datetime.now(datetime.timezone.utc)
            self.update()

    def upload(
        self,
        name: str,
        version: str,
        manifest: dict,
        description: str = "",
    ) -> "RepoElement":
        """Upload element to repository.

        Args:
            name: Element name
            version: Element version
            manifest: Element manifest dict
            description: Element description (optional)

        Returns:
            Created RepoElement instance

        Raises:
            ValueError: If upload is not supported by driver
        """
        driver = self.load_driver()

        # Check if driver supports upload
        if not driver.can_upload_element(name, version):
            raise ValueError("Upload is not supported by this repository driver")

        # Create element
        element = RepoElement(
            name=name,
            version=version,
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
            name,
            version,
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
        self.installation_state = RepoElementInstallationState.UNINSTALLED.value
        self.element = None
        self.update()
        return self

    def upgrade(self, target: str) -> "RepoElement":
        if self.element is None:
            raise ValueError("Element must be installed to upgrade")

        target_element = RepoElement.objects.get_one(
            filters={"uuid": ra_filters.EQ(target)}
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
