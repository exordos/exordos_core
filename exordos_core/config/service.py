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

import collections
import datetime
import logging
import typing as tp
import uuid as sys_uuid

from gcl_looper.services import basic
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters
from restalchemy.storage.sql import utils as sql_utils

from exordos_core.common import constants as c
from exordos_core.compute.dm import models as node_models
from exordos_core.config import constants as cc
from exordos_core.config.dm import models

LOG = logging.getLogger(__name__)
ORPHAN_CFG_ITERATION_FREQUENCY = 10
DEF_OUTDATE_MIN_PERIOD = datetime.timedelta(minutes=10)


class ConfigServiceBuilder(basic.BasicService):
    def _get_new_configs(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[models.Config]:
        return models.Config.get_new_configs(limit=limit)

    def _get_changed_configs(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[models.Config]:
        return models.Config.get_updated_configs(limit=limit)

    def _get_deleted_configs(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[ua_models.TargetResource]:
        return models.Config.get_deleted_config_renders(limit=limit)

    def _get_outdated_renders(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.Dict[
        sys_uuid.UUID,
        tp.List[tp.Tuple[ua_models.TargetResource, ua_models.Resource]],
    ]:
        renders = ua_models.OutdatedResource.objects.get_all(
            filters={"kind": dm_filters.EQ(cc.RENDER_KIND)},
            limit=limit,
        )
        render_map = collections.defaultdict(list)
        for render in renders:
            render_map[render.target_resource.master].append(
                (render.target_resource, render.actual_resource)
            )

        return render_map

    def _get_outdated_configs(
        self, config_uuids: tp.Collection[sys_uuid.UUID]
    ) -> tp.List[tp.Tuple[models.Config, ua_models.TargetResource]]:
        configs = models.Config.objects.get_all(
            filters={"uuid": dm_filters.In(str(cfg) for cfg in config_uuids)},
            order_by={"uuid": "asc"},
        )
        resources = ua_models.TargetResource.objects.get_all(
            filters={
                "uuid": dm_filters.In(str(cfg) for cfg in config_uuids),
                "kind": dm_filters.EQ(cc.CONFIG_KIND),
            },
            order_by={"uuid": "asc"},
        )

        return list(zip(configs, resources))

    def _actualize_new_config(
        self,
        config: models.Config,
        target_nodes: tp.List[node_models.Node],
    ) -> None:
        # Validate the owners exist
        # FIXME(akremenetsky): Only nodes as owners are supported for now.
        # It will be update when sets will appear.

        # FIXME(akremenetsky): Seems the config may be deleted since its
        # owners are absent. May be it will be better to control this
        # behavior via an additional option in the target model but for
        # now just delete this config.
        if not config.target.are_owners_alive():
            LOG.error("Config %s has no owners, delete it.", config.uuid)
            config.delete()
            return

        # FIXME(akremenetsky): Should we set status `IN_PROGRESS` here?
        # Let's wait at least one node to be created
        if len(target_nodes) == 0:
            return

        config_resource = config.to_ua_resource(cc.CONFIG_KIND)
        config_resource.insert()

        # Make renders for this config
        for node in target_nodes:
            render = config.render(node=node.uuid)

            # Hack for scheduler
            render.agent = node.uuid

            render.insert()

        config.status = cc.ConfigStatus.IN_PROGRESS.value
        config.save()

        # TODO(akremenetsky): Improve this snippet in the future
        config_resource.tracked_at = config.updated_at
        config_resource.status = config.status
        config_resource.update()
        LOG.debug("Config resource %s created", config_resource.uuid)

    def _actualize_new_configs(
        self, configs: tp.Optional[tp.List[models.Config]] = None
    ) -> None:
        """Actualize new configs."""
        configs = configs or self._get_new_configs()

        if len(configs) == 0:
            return

        # Collect all target nodes
        target_nodes = set()
        for config in configs:
            target_nodes |= {n for n in config.target_nodes()}

        nodes = {
            n.uuid: n
            for n in node_models.Node.objects.get_all(
                filters={
                    "uuid": dm_filters.In(str(u) for u in target_nodes),
                }
            )
        }

        # Make renders for each new config
        for config in configs:
            # Collect all available nodes for the config
            target_nodes = tuple(nodes[n] for n in config.target_nodes() if n in nodes)
            try:
                with sql_utils.savepoint():
                    self._actualize_new_config(config, target_nodes)
            except Exception:
                LOG.exception("Error actualizing config %s", config.uuid)

    def _actualize_changed_configs(self) -> None:
        """Actualize configs changed by user."""
        changed_configs = self._get_changed_configs()

        if len(changed_configs) == 0:
            return

        # The simplest implementation. Update through recreation.
        config_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "uuid": dm_filters.In(str(uc.uuid) for uc in changed_configs),
                "kind": dm_filters.EQ(cc.CONFIG_KIND),
            }
        )
        render_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "master": dm_filters.In(str(uc.uuid) for uc in changed_configs),
                "kind": dm_filters.EQ(cc.RENDER_KIND),
            }
        )

        for cfg in render_resources + config_resources:
            cfg.delete()
            LOG.debug("Outdated resource (config/render) %s deleted", cfg.uuid)

        # Now they are new configs
        self._actualize_new_configs(changed_configs)

    def _actualize_outdated_config(
        self,
        config: models.Config,
        config_resource: ua_models.TargetResource,
        renders: tp.List[tp.Tuple[ua_models.TargetResource, ua_models.Resource]],
    ) -> None:
        """Actualize outdated config."""
        if len(renders) == 0:
            return

        # Update target renders with actual information from the DP.
        for target_render, actual_render in renders:
            target_render.full_hash = actual_render.full_hash

            # `ACTIVE` only if the hash is the same
            if (
                actual_render.status == cc.ConfigStatus.ACTIVE
                and target_render.hash == actual_render.hash
            ):
                target_render.status = actual_render.status
            elif (
                actual_render.status != cc.ConfigStatus.ACTIVE
                and target_render.status != actual_render.status
            ):
                target_render.status = actual_render.status
            target_render.update()
            LOG.debug("Outdated render %s actualized", target_render.uuid)

        # Actualize status if needed.
        status = None
        if all(r.status == cc.ConfigStatus.ACTIVE for r, _ in renders):
            status = cc.ConfigStatus.ACTIVE
        elif any(r.status == cc.ConfigStatus.NEW for r, _ in renders):
            status = cc.ConfigStatus.NEW
        elif any(r.status == cc.ConfigStatus.IN_PROGRESS for r, _ in renders):
            status = cc.ConfigStatus.IN_PROGRESS

        if status is not None and config.status != status:
            config.status = status.value
            config.update()
            config_resource.tracked_at = config.updated_at
            config_resource.status = config.status
            config_resource.update()

    def _actualize_outdated_configs(self) -> None:
        """Actualize outdated configs.

        It means some changes occurred in the system and the configs
        are outdated now. For instance, their status is incorrect.
        """
        render_map = self._get_outdated_renders()

        if len(render_map) == 0:
            return

        configs = self._get_outdated_configs(tuple(render_map.keys()))

        for config, config_resource in configs:
            renders = render_map[config.uuid]
            try:
                self._actualize_outdated_config(config, config_resource, renders)
            except Exception:
                LOG.exception("Error actualizing config %s", config.uuid)

    def _actualize_deleted_configs(self) -> None:
        """Actualize configs deleted by user."""
        deleted_config_resources = self._get_deleted_configs()

        if len(deleted_config_resources) == 0:
            return

        render_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "master": dm_filters.In(
                    str(uc.uuid) for uc in deleted_config_resources
                ),
                "kind": dm_filters.EQ(cc.RENDER_KIND),
            }
        )

        for resource in render_resources + deleted_config_resources:
            try:
                resource.delete()
                LOG.debug("Resource(%s) %s deleted", resource.kind, resource.uuid)
            except Exception:
                LOG.exception("Error deleting resource %s", resource.uuid)

    def _handle_orphan_configs(
        self, outdate_min_period: datetime.timedelta = DEF_OUTDATE_MIN_PERIOD
    ) -> None:
        """Delete orphan configs. The configs without nodes."""
        if self._iteration_number % ORPHAN_CFG_ITERATION_FREQUENCY != 0:
            return

        # Take N oldest handled configs check if they are orphan
        outdated_ts = datetime.datetime.now(datetime.timezone.utc) - outdate_min_period
        outdated_configs = models.Config.objects.get_all(
            filters={"updated_at": dm_filters.LT(outdated_ts)},
            limit=30,
            order_by={"updated_at": "asc"},
        )

        if not outdated_configs:
            return

        orphan_configs = set()

        # A config is considered as orphan if all its owners are deleted.
        for config in outdated_configs:
            if not config.target.are_owners_alive():
                orphan_configs.add(config)
                LOG.debug("Orphan config %s has been detected", config.uuid)

        # Delete orphan configs
        for config in orphan_configs:
            try:
                config.delete()
                LOG.info("Orphan config %s has been deleted", config.uuid)
            except Exception:
                LOG.exception("Error deleting orphan config %s", config.uuid)

        # Mark the outdate configs as handled
        for config in set(outdated_configs) - orphan_configs:
            try:
                config.update(force=True)
            except Exception:
                LOG.exception("Error updating config %s", config.uuid)

    def _iteration(self) -> None:
        with contexts.Context().session_manager():
            try:
                self._actualize_new_configs()
            except Exception:
                LOG.exception("Error actualizing new configs")

            try:
                self._actualize_changed_configs()
            except Exception:
                LOG.exception("Error actualizing changed configs")

            try:
                self._actualize_outdated_configs()
            except Exception:
                LOG.exception("Error actualizing outdated configs")

            try:
                self._actualize_deleted_configs()
            except Exception:
                LOG.exception("Error actualizing deleted configs")

            # NOTE(akremenetsky): Current data model implementation takes some
            # advantage but there are some disadvantages as well. One of them
            # a config isn't deleted if the corresponding node(s) is deleted.
            # So we need to detect such cases and clear orphan configs.

            # TODO(akremenetsky): Let's start without this feature so far.
            # It's not critical but we need to add this later.
            # self._handle_orphan_configs()
