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

import datetime
import logging
import typing as tp

from gcl_looper.services.oslo import base as oslo_base
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder

from exordos_core.repo.dm import models

LOG = logging.getLogger(__name__)

# Process repository refresh check every N iterations
REFRESH_CHECK_ITERATION_INTERVAL = 10


class Repository(models.Repository, ua_models.InstanceMixin):
    @classmethod
    def get_resource_kind(cls) -> str:
        return "repo_proxy_repository"


class RepoProxyBuilderService(
    sdk_builder.UniversalBuilderService,
    oslo_base.OsloConfigurableService,
):
    def __init__(
        self,
        iter_min_period: int = 3,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            Repository,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def _check_refresh(self) -> None:
        """Check all repositories for refresh and force update if needed."""
        # Get current time
        now = datetime.datetime.now(datetime.timezone.utc)

        # Fetch all repositories
        repositories = models.Repository.objects.get_all()

        # Check each repository for refresh
        for repo in repositories:
            # Skip repositories without refresh rate
            if not repo.refresh_rate:
                continue

            if repo.next_refresh and repo.next_refresh <= now:
                LOG.info(
                    "Repository %s needs refresh, forcing update",
                    repo.name,
                )
                repo.update(force=True)

    def post_create_instance_resource(
        self,
        instance: Repository,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = tuple(),
    ) -> None:
        """The hook is performed after saving instance resource.

        The hook is called only for new instances.
        """
        super().post_create_instance_resource(instance, resource, derivatives)

        # Save the elements to the database
        i = -1
        for i, element in enumerate(instance.iter_elements_in_inventory()):
            # In COPY mode, actualize element to download full data
            if instance.sync_mode == models.SyncMode.COPY:
                instance.actualize_element(element)
            else:
                element.save()

        LOG.info(
            "Saved %d elements to the database for repository %s",
            i + 1,
            instance.repo_uri,
        )

        instance.status = models.RepositoryStatus.ACTIVE.value

        # Set next_refresh timestamp if refresh_rate is configured
        if instance.refresh_rate:
            now = datetime.datetime.now(datetime.timezone.utc)
            instance.next_refresh = now + datetime.timedelta(
                seconds=instance.refresh_rate
            )
            instance.update()

    def _refresh_repository(self, instance: models.Repository) -> None:
        """Actualize elements in the repository."""
        # Get existing elements for this repository
        existing_elements = {
            (elem.name, elem.version): elem
            for elem in models.RepoElement.objects.get_all(
                filters={"repository": instance.uuid},
            )
        }

        # Create a set of current inventory elements
        inventory_elements = {
            (e.name, e.version): e for e in instance.iter_elements_in_inventory()
        }
        new_elements_count = 0
        for key in inventory_elements.keys() - existing_elements.keys():
            element = inventory_elements[key]

            # In COPY mode, actualize element to download full data
            if instance.sync_mode == models.SyncMode.COPY:
                instance.actualize_element(element)
            else:
                element.save()
            new_elements_count += 1

        if new_elements_count:
            LOG.info(
                "Added %d new elements to repository %s during refresh",
                new_elements_count,
                instance.repo_uri,
            )

        # Remove old elements that are no longer in inventory,
        # but keep installed ones
        deleted_elements_count = 0
        for key in existing_elements.keys() - inventory_elements.keys():
            element = existing_elements[key]

            # Skip deletion if element is installed
            if (
                element.installation_state
                == models.RepoElementInstallationState.INSTALLED
            ):
                LOG.debug(
                    "Skipping deletion of installed element %s:%s from repository %s",
                    element.name,
                    element.version,
                    instance.repo_uri,
                )
                continue

            element.delete()
            deleted_elements_count += 1

        if deleted_elements_count:
            LOG.info(
                "Removed %d old elements from repository %s during refresh",
                deleted_elements_count,
                instance.repo_uri,
            )

        # Update next_refresh timestamp
        if instance.refresh_rate:
            now = datetime.datetime.now(datetime.timezone.utc)
            instance.next_refresh = now + datetime.timedelta(
                seconds=instance.refresh_rate
            )
            instance.update()

    def post_update_instance_resource(
        self,
        instance: models.Repository,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = tuple(),
    ) -> None:
        """Handle repository refresh if next_refresh time has passed."""
        super().post_update_instance_resource(instance, resource, derivatives)

        # Check if refresh is needed
        if not instance.refresh_rate:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        if instance.next_refresh and instance.next_refresh <= now:
            LOG.info(
                "Refreshing repository %s",
                instance.name,
            )
            self._refresh_repository(instance)

    def can_actualize_outdated_instance_resource(
        self, instance: ua_models.InstanceMixin
    ) -> bool:
        """The hook to check if the instance can be actualized.

        If the hook returns `False`, the code related to the instance:
        - `actualize_outdated_instance`
        - `actualize_outdated_instance_derivatives`
        will be skipped for the current iteration. The
        `can_actualize_outdated_instance_resource` will be called again on
        the next iteration until it returns `True`.
        """
        if isinstance(instance, ua_models.ReadinessMixin):
            return instance.is_ready_to_actualize()

        return True

    def prepare_iteration(self) -> dict[str, tp.Any]:
        """Check repositories for refresh and force update if needed."""
        # Only process refresh logic every Nth iteration
        if self._iteration_number % REFRESH_CHECK_ITERATION_INTERVAL != 0:
            return {}

        self._check_refresh()
        return {}
