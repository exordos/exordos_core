#    Copyright 2025-2026 Genesis Corporation.
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

import logging
import time
import uuid as sys_uuid

from gcl_looper.services import basic
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.clients.backend import db as db_back
from gcl_sdk.agents.universal.clients.orch import db as orch_db
from gcl_sdk.agents.universal.drivers import core as ua_core_drivers
from gcl_sdk.agents.universal.services import agent as ua_agent_service
from gcl_sdk.agents.universal.services import scheduler as ua_scheduler_service
from gcl_sdk.audit import opts as audit_opts
from gcl_sdk.audit.services import senders as audit_senders
from gcl_sdk.events import constants as event_c
from gcl_sdk.events.services import senders
from oslo_config import cfg
from restalchemy.dm import filters as dm_filters

from exordos_core.compute import constants as nc
from exordos_core.compute.agents.universal.drivers import pool as ua_pool_drivers
from exordos_core.compute.builders import node as node_builder_svc
from exordos_core.compute.builders import node_set as set_builder_svc
from exordos_core.compute.builders import pool as pool_builder_svc
from exordos_core.compute.builders import volume as volume_builder_svc
from exordos_core.compute.dm import models as compute_models
from exordos_core.compute.node_set.dm import models as node_set_models
from exordos_core.compute.scheduler import service as n_scheduler_service
from exordos_core.compute.scheduler.driver.filters import affinity
from exordos_core.compute.scheduler.driver.filters import available
from exordos_core.compute.scheduler.driver.weighter import relative
from exordos_core.config import service as config_service
from exordos_core.dns_sync import service as dns_sync_service
from exordos_core.elements.builders import service as service_builder_svc
from exordos_core.elements.services import builders as em_builders
from exordos_core.janitor import service as janitor_service
from exordos_core.network import service as n_network_service
from exordos_core.network.lb.builders import iaas as net_lb_iaas
from exordos_core.network.lb.builders import paas as net_lb_paas
from exordos_core.network.lb.dm import models as lb_models
from exordos_core.secret import service as secret_service
from exordos_core.secret.builders import service as password_builder_svc
from exordos_core.telemetry import service as telemetry_service
from exordos_core.vs.builders import service as vs_builder_svc

LOG = logging.getLogger(__name__)
CONF = cfg.CONF
NODE_SET_TF_STORAGE = "/var/lib/exordos/exordos_core/node_set/target_fields.json"
NODE_SET_TARGET_TF_STORAGE = (
    "/var/lib/exordos/exordos_core/target_node_set/target_fields.json"
)


class GeneralService(basic.BasicService):
    def __init__(self, iter_min_period=3, iter_pause=0.1):
        super().__init__(iter_min_period=iter_min_period, iter_pause=iter_pause)

        # TODO(akremenetsky): Form a pipliene from the configuration
        # and entry points
        pool_filters = [
            available.CoresRamAvailableFilter(),
            affinity.DummySoftAntiAffinityFilter(),
        ]
        pool_weighters = [
            relative.RelativeCoreRamWeighter(),
        ]
        machine_filters = [
            available.HWCoresRamAvailableFilter(),
        ]
        machine_weighters = [
            relative.SimpleMachineWeighter(),
        ]

        # The simplest way to enable the nested services
        # It will be reworked in the future
        n_scheduler = n_scheduler_service.SchedulerService(
            pool_filters=pool_filters,
            pool_weighters=pool_weighters,
            machine_filters=machine_filters,
            machine_weighters=machine_weighters,
            iter_min_period=iter_min_period,
        )
        n_network = n_network_service.NetworkService(iter_min_period=iter_min_period)
        node_builder = node_builder_svc.NodeBuilderService(
            iter_min_period=iter_min_period
        )
        volume_builder = volume_builder_svc.VolumeBuilderService(
            iter_min_period=iter_min_period
        )
        pool_driver = ua_pool_drivers.PoolAgentDriver(
            meta_file="/var/lib/exordos/exordos_core/pool_agent_meta.json"
        )
        agent_uuid = sys_uuid.uuid5(ua_utils.system_uuid(), "machine_pool_agent")
        machine_pool_agent = ua_agent_service.UniversalAgentService(
            agent_uuid=agent_uuid,
            orch_client=orch_db.DatabaseOrchClient(),
            caps_drivers=[pool_driver],
            facts_drivers=[],
            iter_min_period=iter_min_period,
            payload_path=None,
        )

        set_builder = set_builder_svc.NodeSetBuilderService(
            instance_model=node_set_models.NodeSet,
            project_id=nc.NODE_SET_PROJECT,
        )
        service_builder = service_builder_svc.ServiceNodeBuilder()
        net_lb_iaas_builder = net_lb_iaas.LBBuilder(
            instance_model=lb_models.IaasLB,
            project_id=nc.NODE_SET_PROJECT,
        )
        net_lb_paas_builder = net_lb_paas.LBBuilder()

        # Infra scheduler
        infra_scheduler = ua_scheduler_service.UniversalAgentSchedulerService(
            capabilities=["set_agent_node", "target_node_set"],
            iter_min_period=iter_min_period,
        )

        # Infra agent
        orch_client = orch_db.DatabaseOrchClient()
        agent_uuid = sys_uuid.uuid5(ua_utils.system_uuid(), "set_agent")

        spec = db_back.ModelSpec(
            kind="set_agent_node",
            model=compute_models.Node,
            filters={"project_id": dm_filters.EQ(str(nc.NODE_SET_PROJECT))},
        )
        db_core_driver = ua_core_drivers.DatabaseCapabilityDriver(
            model_specs=[spec],
            target_fields_storage_path=NODE_SET_TF_STORAGE,
        )

        node_spec = db_back.ModelSpec(
            kind="target_node_set",
            model=compute_models.NodeSet,
            filters={"project_id": dm_filters.EQ(str(nc.NODE_SET_PROJECT))},
        )
        node_db_core_driver = ua_core_drivers.DatabaseCapabilityDriver(
            model_specs=[node_spec],
            target_fields_storage_path=NODE_SET_TARGET_TF_STORAGE,
        )

        caps_drivers = [
            db_core_driver,
            node_db_core_driver,
        ]

        facts_drivers = []

        infra_agent = ua_agent_service.UniversalAgentService(
            agent_uuid=agent_uuid,
            orch_client=orch_client,
            caps_drivers=caps_drivers,
            facts_drivers=facts_drivers,
            iter_min_period=iter_min_period,
            payload_path=None,
        )

        pool_builder_service = pool_builder_svc.PoolBuilderService(
            uuid=sys_uuid.uuid5(ua_utils.system_uuid(), "pool_builder"),
            orch_client=orch_db.DatabaseOrchClient(),
            iter_min_period=iter_min_period,
        )

        # ValuesStore
        vs_builder_service = vs_builder_svc.VSBuilderService(
            iter_min_period=iter_min_period,
        )

        cfg_service = config_service.ConfigServiceBuilder(
            iter_min_period=iter_min_period,
        )
        secret_svc = secret_service.SecretServiceBuilder(
            iter_min_period=iter_min_period,
        )
        password_builder = password_builder_svc.PasswordBuilder(
            iter_min_period=iter_min_period
        )
        # Build the event sender only when event delivery is enabled (the
        # exordos_notification element drops an [events] override into the
        # config dir). When disabled, build_from_config would receive a None
        # event_type_mapping_filepath and crash, so skip it entirely.
        if CONF[event_c.DOMAIN].enabled:
            event_sender = senders.EventSenderService.build_from_config()
        else:
            event_sender = None
        if audit_opts.get_audit_delivery_config().enabled:
            audit_sender = audit_senders.AuditSenderService.build_from_config(
                iter_min_period=iter_min_period,
            )
        else:
            audit_sender = None
        em_builder = em_builders.ElementManagerBuilder(iter_min_period=iter_min_period)
        janitor = janitor_service.ExpiredEmailConfirmationCodeJanitorService(
            iter_min_period=60 * 60,
        )

        # Telemetry
        telemetry = telemetry_service.TelemetryService(
            iter_min_period=60 * 60,
        )

        # DNS Sync
        dns_sync = dns_sync_service.DNSSyncService(
            iter_min_period=iter_min_period * 2,
        )

        self._services = [
            vs_builder_service,
            set_builder,
            infra_scheduler,
            infra_agent,
            n_scheduler,
            n_network,
            pool_builder_service,
            node_builder,
            volume_builder,
            machine_pool_agent,
            cfg_service,
            service_builder,
            net_lb_iaas_builder,
            net_lb_paas_builder,
            secret_svc,
            password_builder,
            em_builder,
            dns_sync,
            # non-essential services should be last
            janitor,
            telemetry,
        ]
        if event_sender is not None:
            self._services.append(event_sender)
        if audit_sender is not None:
            self._services.append(audit_sender)
        self._next_run_times = {id(s): 0 for s in self._services}

    def _setup(self):
        LOG.info("Setup all services")
        for service in self._services:
            service._setup()

    def _iteration(self):
        now = time.monotonic()
        for service in self._services:
            svc_id = id(service)
            if now >= self._next_run_times[svc_id]:
                self._next_run_times[svc_id] = now + service._iter_min_period
                service._loop_iteration()
