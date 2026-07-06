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

from concurrent import futures
import logging

import bazooka
from bazooka import exceptions as bazooka_exc
from gcl_looper.services import basic
from requests import auth as requests_auth
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.compute.dm import models as compute_models
from exordos_core.config.dm import models as config_models
from exordos_core.elements.dm import models as em_models
from exordos_core.secret.dm import models as secret_models
from exordos_core.user_api.dns.dm import models as dns_models
from exordos_core.user_api.iam.dm import models as iam_models
from exordos_core.user_api.network.dm import models as net_models
from exordos_core.user_api.security.dm import models as security_models
from exordos_core.vs.dm import models as vs_models

LOG = logging.getLogger(__name__)

TELEMETRY_TIMEOUT = 30
TELEMETRY_POOL_SIZE = 1

# Initial retry cadence used until the stand has successfully registered
# with the ecosystem at least once. exordos-bootstrap (which sets the VS
# variables this service depends on) has no systemd ordering guarantee
# relative to ec-gservice, so the very first iteration can race ahead of
# the variables being set. Falling back to the full iter_min_period (can be
# as long as an hour) in that case would leave the stand stuck in
# PROVISIONING for a long time, so we start retrying quickly.
#
# Stands with no network access at all would otherwise keep retrying every
# TELEMETRY_RETRY_PERIOD forever, so on every failed attempt the period is
# doubled (TELEMETRY_RETRY_BACKOFF_FACTOR), up to the caller's steady-state
# period. It resets back to TELEMETRY_RETRY_PERIOD once registration
# succeeds, then relaxes to the steady-state period.
TELEMETRY_RETRY_PERIOD = 30
TELEMETRY_RETRY_BACKOFF_FACTOR = 2


class TelemetryService(basic.BasicService):
    """Periodically collects and sends telemetry data to the ecosystem."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = bazooka.Client(default_timeout=TELEMETRY_TIMEOUT)
        self._executor = futures.ThreadPoolExecutor(max_workers=TELEMETRY_POOL_SIZE)
        self._pending_future = None
        # Steady-state period requested by the caller (e.g. 1 hour). Until
        # the stand registers successfully we back off from
        # TELEMETRY_RETRY_PERIOD towards this value instead; see the
        # comment on TELEMETRY_RETRY_PERIOD above.
        self._steady_period = self._iter_min_period
        self._registered = False
        self._iter_min_period = TELEMETRY_RETRY_PERIOD

    def _get_variable_value(self, var_uuid):
        """Read variable value from ValuesStore by UUID."""
        variable = vs_models.Variable.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(var_uuid)}
        )
        if variable is None:
            return None
        return variable.value

    @staticmethod
    def _safe_collect(data, collector, group_name):
        """Run a collector function and log errors without raising."""
        try:
            collector(data)
        except Exception:
            LOG.exception("Failed to collect %s metrics", group_name)

    @staticmethod
    def _count_entities(data, counts):
        """Collect counts for a list of (key, model_class) pairs."""
        for key, model_class in counts:
            try:
                data[key] = model_class.objects.count()
            except Exception:
                LOG.exception("Failed to count %s", key)

    @staticmethod
    def _collect_compute_nodes(data):
        nodes = compute_models.Node.objects.get_all()
        data["nodes_count"] = len(nodes)
        data["nodes_total_cores"] = sum(n.cores for n in nodes)
        data["nodes_total_ram"] = sum(n.ram for n in nodes)

    @staticmethod
    def _collect_machine_pools(data):
        pools = compute_models.MachinePool.objects.get_all()
        data["machine_pools_count"] = len(pools)
        data["machine_pools_total_cores"] = sum(p.all_cores for p in pools)
        data["machine_pools_total_ram"] = sum(p.all_ram for p in pools)
        data["machine_pools_avail_cores"] = sum(p.avail_cores for p in pools)
        data["machine_pools_avail_ram"] = sum(p.avail_ram for p in pools)
        # Storage aggregates across all pools
        storage_capacity = 0
        storage_provisioned = 0
        storage_available_actual = 0
        for pool in pools:
            for sp in pool.storage_pools:
                storage_capacity += sp.capacity_usable
                storage_provisioned += sp.capacity_provisioned
                storage_available_actual += sp.available_actual
        data["storage_capacity"] = storage_capacity
        data["storage_provisioned"] = storage_provisioned
        data["storage_available_actual"] = storage_available_actual
        # Count by status
        status_counts = {}
        for pool in pools:
            key = "machine_pools_status_%s" % pool.status.lower()
            status_counts[key] = status_counts.get(key, 0) + 1
        data.update(status_counts)

    def _collect_telemetry(self):
        """Collect telemetry data from the system."""
        data = {}

        # Compute: nodes with resource aggregates
        self._safe_collect(data, self._collect_compute_nodes, "compute_nodes")

        # Compute: machine pools with detailed hypervisor stats
        self._safe_collect(data, self._collect_machine_pools, "machine_pools")

        # Compute: simple counts
        self._count_entities(
            data,
            [
                ("node_sets_count", compute_models.NodeSet),
                ("machines_count", compute_models.Machine),
                ("volumes_count", compute_models.Volume),
                ("interfaces_count", compute_models.Interface),
                ("placement_policies_count", compute_models.PlacementPolicy),
            ],
        )

        # Network
        self._count_entities(
            data,
            [
                ("networks_count", compute_models.Network),
                ("subnets_count", compute_models.Subnet),
                ("ports_count", compute_models.Port),
                ("lb_count", net_models.LB),
                ("lb_vhosts_count", net_models.Vhost),
                ("lb_routes_count", net_models.Route),
                ("lb_backend_pools_count", net_models.BackendPool),
            ],
        )

        # IAM
        self._count_entities(
            data,
            [
                ("iam_users_count", iam_models.User),
                ("iam_roles_count", iam_models.Role),
                ("iam_projects_count", iam_models.Project),
                ("iam_organizations_count", iam_models.Organization),
                ("iam_permissions_count", iam_models.Permission),
                ("iam_clients_count", iam_models.IamClient),
                ("iam_idp_count", iam_models.Idp),
                ("iam_role_bindings_count", iam_models.RoleBinding),
            ],
        )

        # Elements
        self._count_entities(
            data,
            [
                ("em_elements_count", em_models.Element),
                ("em_resources_count", em_models.Resource),
                ("em_manifests_count", em_models.Manifest),
            ],
        )

        # DNS
        self._count_entities(
            data,
            [
                ("dns_domains_count", dns_models.Domain),
                ("dns_records_count", dns_models.Record),
            ],
        )

        # Secrets
        self._count_entities(
            data,
            [
                ("secrets_passwords_count", secret_models.Password),
                ("secrets_certificates_count", secret_models.Certificate),
                ("secrets_ssh_keys_count", secret_models.SSHKey),
                ("secrets_rsa_keys_count", secret_models.RSAKey),
            ],
        )

        # Config
        self._count_entities(
            data,
            [
                ("configs_count", config_models.Config),
            ],
        )

        # ValuesStore
        self._count_entities(
            data,
            [
                ("vs_variables_count", vs_models.Variable),
                ("vs_profiles_count", vs_models.Profile),
            ],
        )

        # Security
        self._count_entities(
            data,
            [
                ("security_rules_count", security_models.Rule),
            ],
        )

        return data

    def _register_stand(self, endpoint, realm_uuid, realm_secret):
        """Register stand in ecosystem."""
        self._client.post(
            f"{endpoint}/api/ecosystem/v1/realms/",
            json={"uuid": realm_uuid, "secret": realm_secret},
            headers={"Content-Type": "application/json"},
        )
        LOG.info("Stand registered in ecosystem successfully")

    def _mark_registered(self):
        """Relax the retry cadence once the stand is confirmed registered."""
        self._registered = True
        self._iter_min_period = self._steady_period

    def _backoff_retry(self):
        """Grow the retry cadence towards the steady-state period on failure."""
        if self._registered:
            return
        self._iter_min_period = min(
            self._iter_min_period * TELEMETRY_RETRY_BACKOFF_FACTOR,
            self._steady_period,
        )

    def _send_telemetry(self, endpoint, realm_uuid, realm_secret, data):
        """Send telemetry data to the ecosystem endpoint."""
        url = f"{endpoint}/api/ecosystem/v1/realms/{realm_uuid}/actions/push_telemetry/invoke"
        auth = requests_auth.HTTPBasicAuth(realm_uuid, realm_secret)

        try:
            self._client.post(
                url,
                json={"data": data},
                headers={"Content-Type": "application/json"},
                auth=auth,
            )
            LOG.debug("Telemetry sent successfully")
            self._mark_registered()
        except bazooka_exc.ForbiddenError:
            LOG.warning("Stand is not registered, attempting registration")
            try:
                self._register_stand(endpoint, realm_uuid, realm_secret)
                self._client.post(
                    url,
                    json={"data": data},
                    headers={"Content-Type": "application/json"},
                    auth=auth,
                )
                LOG.debug("Telemetry sent successfully after registration")
                self._mark_registered()
            except Exception:
                LOG.exception("Failed to register stand or send telemetry")
                self._backoff_retry()
        except Exception:
            LOG.exception("Failed to send telemetry")
            self._backoff_retry()

    def _check_pending_future(self):
        """Clear completed future to allow the next submission."""
        if self._pending_future is not None and self._pending_future.done():
            self._pending_future = None

    def _iteration(self):
        self._check_pending_future()

        with contexts.Context().session_manager():
            # Check if telemetry is disabled
            disable_telemetry = self._get_variable_value(c.VAR_DISABLE_TELEMETRY_UUID)
            if disable_telemetry:
                LOG.debug("Telemetry is disabled")
                return

            if self._pending_future is not None:
                LOG.debug("Previous telemetry request still pending, skipping")
                return

            # Read required variables
            ecosystem_endpoint = self._get_variable_value(c.VAR_ECOSYSTEM_ENDPOINT_UUID)
            realm_uuid = self._get_variable_value(c.VAR_REALM_UUID_UUID)
            realm_secret = self._get_variable_value(c.VAR_REALM_SECRET_UUID)

            if not all([ecosystem_endpoint, realm_uuid, realm_secret]):
                self._backoff_retry()
                LOG.info(
                    "Telemetry variables are not configured yet, will retry in %ss",
                    self._iter_min_period,
                )
                return

            # Collect telemetry data synchronously, send asynchronously
            data = self._collect_telemetry()

        self._pending_future = self._executor.submit(
            self._send_telemetry,
            ecosystem_endpoint,
            realm_uuid,
            realm_secret,
            data,
        )
