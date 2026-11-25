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
from restalchemy.storage.sql import engines
from restalchemy.storage.sql import utils as sql_utils

from exordos_core.common import constants as c
from exordos_core.common.dm import targets
from exordos_core.compute.dm import models as node_models
from exordos_core.config import constants as cc
from exordos_core.config.dm import models
from exordos_core.config.endpoints import LinkService

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
        engine = engines.engine_factory.get_engine()
        with engine.session_manager() as session:
            rows = session.execute(
                """
                SELECT outdated.uuid FROM ua_outdated_resources_view AS outdated
                JOIN ua_target_resources AS render ON render.res_uuid = outdated.target_resource
                JOIN config_configs AS config ON config.uuid = render.master
                WHERE outdated.kind = %s LIMIT %s
                """,
                (cc.RENDER_KIND, limit),
            ).fetchall()
        renders = (
            ua_models.OutdatedResource.objects.get_all(
                filters={"uuid": dm_filters.In(row["uuid"] for row in rows)}
            )
            if rows
            else ()
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
        config_filter = {"uuid": dm_filters.In(tuple(str(cfg) for cfg in config_uuids))}
        configs = models.Config.objects.get_all(
            filters=config_filter,
            order_by={"uuid": "asc"},
        )
        resources = ua_models.TargetResource.objects.get_all(
            filters={
                **config_filter,
                "kind": dm_filters.EQ(cc.CONFIG_KIND),
            },
            order_by={"uuid": "asc"},
        )

        by_uuid = {resource.uuid: resource for resource in resources}
        return [
            (config, by_uuid[config.uuid])
            for config in configs
            if config.uuid in by_uuid
        ]

    def _actualize_new_config(
        self,
        config: models.Config,
        target_nodes: tp.List[node_models.Node],
    ) -> None:
        if isinstance(config.target, targets.NodeSetTarget):
            owner_alive = (
                node_models.NodeSet.objects.get_one_or_none(
                    filters={"uuid": dm_filters.EQ(config.target.node_set)}
                )
                is not None
            )
        else:
            owner_alive = config.target.are_owners_alive()
        if not owner_alive:
            LOG.error("Config %s has no owners, delete it.", config.uuid)
            config.delete()
            return

        # FIXME(akremenetsky): Should we set status `IN_PROGRESS` here?
        # Let's wait at least one node to be created
        if len(target_nodes) == 0:
            return

        config_resource = config.to_ua_resource(cc.CONFIG_KIND)
        config_resource.insert()

        self._sync_renders(config, {node.uuid for node in target_nodes}, ())

        config.status = cc.ConfigStatus.IN_PROGRESS.value
        config.save()

        # TODO(akremenetsky): Improve this snippet in the future
        config_resource.tracked_at = config.updated_at
        config_resource.status = config.status
        config_resource.update()
        LOG.debug("Config resource %s created", config_resource.uuid)

    def _config_targets(self, configs, endpoint_configs):
        endpoint_configs = set(endpoint_configs)
        node_sets = {
            config.target.node_set
            for config in configs
            if isinstance(config.target, targets.NodeSetTarget)
        }
        members_by_set = collections.defaultdict(set)
        if node_sets:
            for node in node_models.Node.objects.get_all(
                filters={"node_set": dm_filters.In(node_sets)}
            ):
                members_by_set[node.node_set].add(node.uuid)
        wanted = {
            config.uuid: (
                members_by_set[config.target.node_set].copy()
                if isinstance(config.target, targets.NodeSetTarget)
                else set(config.target_nodes())
            )
            for config in configs
        }
        candidates = set().union(*wanted.values())
        if not candidates:
            return wanted
        agents = {
            agent.uuid: agent
            for agent in ua_models.UniversalAgent.objects.get_all(
                filters={"uuid": dm_filters.In(candidates)}
            )
            if agent.uuid == agent.node
        }
        for config_id, members in wanted.items():
            members.intersection_update(agents)
            if config_id in endpoint_configs:
                wanted[config_id] = {
                    node
                    for node in members
                    if LinkService.supports_endpoints(agents[node])
                }
        return wanted

    def _actualize_new_configs(
        self,
        configs: tp.Optional[tp.List[models.Config]] = None,
        *,
        endpoint_configs=(),
    ) -> None:
        """Actualize new configs."""
        configs = configs or self._get_new_configs()

        if len(configs) == 0:
            return

        wanted = self._config_targets(configs, endpoint_configs)
        target_nodes = {node for members in wanted.values() for node in members}
        nodes = {
            n.uuid: n
            for n in node_models.Node.objects.get_all(
                filters={
                    "uuid": dm_filters.In(str(u) for u in target_nodes),
                }
            )
        }

        for config in configs:
            target_nodes = tuple(
                nodes[n] for n in sorted(wanted[config.uuid]) if n in nodes
            )
            try:
                with sql_utils.savepoint():
                    self._actualize_new_config(config, target_nodes)
            except Exception:
                LOG.exception("Error actualizing config %s", config.uuid)

    def _actualize_changed_configs(self, endpoint_configs=()) -> None:
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
        self._actualize_new_configs(changed_configs, endpoint_configs=endpoint_configs)

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

    def _endpoint_config_ids(self):
        engine = engines.engine_factory.get_engine()
        with engine.session_manager() as session:
            rows = session.execute(
                "SELECT DISTINCT config FROM em_endpoint_uses"
            ).fetchall()
        return {row["config"] for row in rows}

    def _actualize_config_members(self, endpoint_configs) -> None:
        engine = engines.engine_factory.get_engine()
        with engine.session_manager() as session:
            rows = session.execute("""
                SELECT uuid FROM config_configs WHERE target->>'kind' = 'node_set'
                """).fetchall()
        candidates = {row["uuid"] for row in rows}.union(endpoint_configs)
        if not candidates:
            return
        configs = self._get_outdated_configs(candidates)
        if not configs:
            return
        wanted = self._config_targets(
            [config for config, _ in configs], endpoint_configs
        )
        live = {
            node.uuid
            for node in node_models.Node.objects.get_all(
                filters={"uuid": dm_filters.In(set().union(*wanted.values()))}
            )
        }
        renders = collections.defaultdict(list)
        for render in ua_models.TargetResource.objects.get_all(
            filters={
                "kind": dm_filters.EQ(cc.RENDER_KIND),
                "master": dm_filters.In(wanted),
            }
        ):
            renders[render.master].append(render)
        for config, master in configs:
            try:
                with sql_utils.savepoint():
                    desired = wanted[config.uuid] & live
                    current = renders[config.uuid]
                    if self._sync_renders(config, desired, current):
                        retained = [
                            render for render in current if render.agent in desired
                        ]
                        status = (
                            cc.ConfigStatus.ACTIVE
                            if retained
                            and len(retained) == len(desired)
                            and all(
                                render.status == cc.ConfigStatus.ACTIVE
                                for render in retained
                            )
                            else cc.ConfigStatus.IN_PROGRESS
                        )
                        if config.status == status:
                            continue
                        config.status = status.value
                        config.update()
                        master.status = config.status
                        master.tracked_at = config.updated_at
                        master.update()
            except Exception:
                LOG.exception("Error updating delivery of config %s", config.uuid)

    @staticmethod
    def _sync_renders(config, wanted, renders):
        removed = False
        for render in renders:
            if render.agent not in wanted:
                render.delete()
                removed = True
        missing = wanted - {render.agent for render in renders}
        for node in sorted(missing):
            render = config.render(node=node)
            render.agent = node
            render.insert()
        return removed or bool(missing)

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
            endpoint_configs = self._endpoint_config_ids()
            try:
                self._actualize_new_configs(endpoint_configs=endpoint_configs)
            except Exception:
                LOG.exception("Error actualizing new configs")

            try:
                self._actualize_changed_configs(endpoint_configs)
            except Exception:
                LOG.exception("Error actualizing changed configs")

            try:
                self._actualize_config_members(endpoint_configs)
            except Exception:
                LOG.exception("Error updating config delivery")

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
