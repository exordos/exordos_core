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

import enum
import typing as tp
from urllib.parse import urljoin

from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types as ra_types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import orm

from exordos_core.common import utils
from exordos_core.repo import constants as rc

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
    ACTIVE = "ACTIVE"
    IN_PROGRESS = "IN_PROGRESS"
    ERROR = "ERROR"
    LAZY_LOADED = "LAZY_LOADED"


class NginxDriver(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
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
        return urljoin(self.url, self.INVENTORY_FILE)


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
        ra_types.Integer(min_value=0, max_value=4096),
        default=2048,
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
            types_dynamic.KindModelType(NginxDriver),
        ),
        default=NginxDriver,
        required=False,
    )

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

    def get_elements_from_inventory(self) -> list["RepoElement"]:
        """Get elements from the repository inventory.

        Pulls the inventory via the configured driver and creates
        :class:`RepoElement` instances with only name, version, and status
        populated. Fields ``manifest``, ``specification``, and ``inventory``
        are left at their defaults (empty dicts) and will be populated later.

        :return: List of lightweight :class:`RepoElement` instances
        """
        driver = self.load_driver()
        inventory = driver.get_inventory()

        elements = []
        for item in inventory.get("elements", []):
            element = RepoElement(
                name=item["name"],
                version=item["version"],
                description=item.get("description", ""),
                project_id=self.project_id,
                status=RepoElementStatus.LAZY_LOADED.value,
                repository=self,
            )
            elements.append(element)

        return elements


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
        status: Current status (NEW, ACTIVE, DISABLED, ERROR, IN_PROGRESS)
        manifest: YAML manifest file content (large dict)
        specification: YAML specification file content (large dict)
        inventory: YAML inventory file content (large dict)
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
