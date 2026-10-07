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
import datetime
import logging
import os
import tempfile
import time

import bazooka
from bazooka import exceptions as bazooka_exc
from gcl_looper.services import basic
from requests import auth as requests_auth
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.user_api.dns.dm import models as dns_models
from exordos_core.vs.dm import models as vs_models

LOG = logging.getLogger(__name__)

DNS_SYNC_TIMEOUT = 30
DNS_SYNC_POOL_SIZE = 1
FULL_SYNC_INTERVAL = 60
# dnsdist-public.conf re-reads this file and routes the listed domains
# to the "privatedns" pool.
DNSDIST_DOMAINS_FILE = "/etc/dnsdist/publicdns-domains.txt"


class DNSSyncService(basic.BasicService):
    """Publishes realm-scoped logical DNS records through Ecosystem."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = bazooka.Client(default_timeout=DNS_SYNC_TIMEOUT)
        self._executor = futures.ThreadPoolExecutor(max_workers=DNS_SYNC_POOL_SIZE)
        self._pending_future = None
        self._initialized = False
        self._last_full_sync_at = FULL_SYNC_INTERVAL + 1
        self._last_sync_dt = None
        self._dnsdist_domains_content = None

    def _get_variable_value(self, var_uuid):
        variable = vs_models.Variable.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(var_uuid)}
        )
        if variable is None:
            return None
        return variable.value

    def _get_ecosystem_credentials(self):
        endpoint = self._get_variable_value(c.VAR_ECOSYSTEM_ENDPOINT_UUID)
        realm_uuid = self._get_variable_value(c.VAR_REALM_UUID_UUID)
        realm_secret = self._get_variable_value(c.VAR_REALM_SECRET_UUID)
        if not all([endpoint, realm_uuid, realm_secret]):
            return None
        return endpoint, realm_uuid, realm_secret

    @staticmethod
    def _make_basic_auth(realm_uuid, realm_secret):
        return requests_auth.HTTPBasicAuth(realm_uuid, realm_secret)

    def _eco_get_realm(self, endpoint, realm_uuid, auth):
        url = f"{endpoint}/api/ecosystem/v1/realms/{realm_uuid}"
        return self._client.get(url, auth=auth).json()

    def _eco_sync_domain(self, endpoint, realm_uuid, auth, domain, records):
        url = f"{endpoint}/api/ecosystem/v1/realms/{realm_uuid}/actions/sync_dns/invoke"
        self._client.post(
            url,
            auth=auth,
            json={
                "domain": {
                    "name": domain.name,
                    "realm_id": domain.realm_id,
                },
                "records": records,
            },
        )

    def _realm_domains(self):
        domains = dns_models.Domain.objects.get_all(
            filters={"sync_to_ecosystem": dm_filters.EQ(True)}
        )
        return [domain for domain in domains if domain.realm_id is not None]

    def _ensure_realm_domain(self, endpoint, realm_uuid, auth):
        domains = self._realm_domains()
        if domains:
            return True

        realm = self._eco_get_realm(endpoint, realm_uuid, auth)
        realm_id = realm.get("realm_id")
        realm_domain = (realm.get("domain") or "").lower().rstrip(".")
        if not realm_id or not realm_domain:
            LOG.warning("Realm has no realm_id/domain DNS contract")
            return False

        prefix = f"{realm_id}."
        if not realm_domain.startswith(prefix):
            raise ValueError("Ecosystem realm domain does not match realm_id")
        domain_name = realm_domain.removeprefix(prefix)

        domain = dns_models.Domain.objects.get_one_or_none(
            filters={"name": dm_filters.EQ(domain_name)}
        )
        if domain is None:
            legacy_domain = dns_models.Domain.objects.get_one_or_none(
                filters={"name": dm_filters.EQ(realm_domain)}
            )
            if legacy_domain is not None:
                if not (
                    legacy_domain.realm_id is None
                    and not legacy_domain.sync_only
                    and legacy_domain.sync_to_ecosystem
                ):
                    raise ValueError(
                        "Legacy local DNS domain conflicts with the realm DNS contract"
                    )

                legacy_domain.name = domain_name
                legacy_domain.realm_id = realm_id
                legacy_domain.sync_only = True
                legacy_domain.update()
                for record in dns_models.Record.objects.get_all(
                    filters={"domain": dm_filters.EQ(legacy_domain)}
                ):
                    if record.type == "SOA":
                        record.delete(force=True)
                    else:
                        record.update()
                LOG.info("Migrated legacy realm DNS domain to %s", domain_name)
                return True

            domain = dns_models.Domain(
                name=domain_name,
                realm_id=realm_id,
                sync_only=True,
                sync_to_ecosystem=True,
                project_id=c.ZERO_UUID,
            )
            domain.insert()
            return True

        if not (
            domain.realm_id == realm_id
            and domain.sync_only
            and domain.sync_to_ecosystem
        ):
            raise ValueError("Local DNS domain conflicts with the realm DNS contract")
        return True

    @staticmethod
    def _build_record_data(record):
        record_prop = record.properties.properties["record"]
        record_type = record_prop.get_property_type()
        return {
            "uuid": str(record.uuid),
            "type": record.type,
            "ttl": record.ttl,
            "disabled": record.disabled,
            "record": record_type.to_simple_type(record.record),
            "full_name": record.full_name,
        }

    def _domain_records(self, domain):
        records = dns_models.Record.objects.get_all(
            filters={
                "domain": dm_filters.EQ(domain),
                "type": dm_filters.NE("SOA"),
            }
        )
        return [self._build_record_data(record) for record in records]

    def _has_recent_changes(self, since_dt):
        records = dns_models.Record.objects.get_all(
            filters={
                "updated_at": dm_filters.GE(since_dt),
                "type": dm_filters.NE("SOA"),
            }
        )
        return any(record.domain.realm_id is not None for record in records)

    def _sync_all_domains(self, endpoint, realm_uuid, realm_secret):
        auth = self._make_basic_auth(realm_uuid, realm_secret)
        if not self._initialized:
            try:
                initialized = self._ensure_realm_domain(endpoint, realm_uuid, auth)
            except bazooka_exc.ForbiddenError:
                LOG.warning("Not authorized to fetch realm, skipping DNS sync")
                return
            if not initialized:
                return
            self._initialized = True

        now = time.monotonic()
        need_full = (now - self._last_full_sync_at) >= FULL_SYNC_INTERVAL
        if (
            not need_full
            and self._last_sync_dt is not None
            and not self._has_recent_changes(self._last_sync_dt)
        ):
            return

        domains = self._realm_domains()
        for domain in domains:
            records = self._domain_records(domain)
            try:
                self._eco_sync_domain(
                    endpoint,
                    realm_uuid,
                    auth,
                    domain,
                    records,
                )
            except Exception:
                LOG.exception("DNS sync failed for realm domain %s", domain.name)

        if need_full:
            self._last_full_sync_at = now
        self._last_sync_dt = datetime.datetime.now(datetime.timezone.utc)

    def _check_pending_future(self):
        if self._pending_future is not None and self._pending_future.done():
            self._pending_future = None

    def _write_dnsdist_domains(self):
        """Write local DNS domains to the file consumed by dnsdist-public."""
        # Fetch only domain names, not full models
        with dns_models.Domain._get_engine().session_manager() as session:
            # Select domains tagged public
            rows = session.execute(
                "SELECT name FROM dns_domains WHERE tags @> %s::text[]",
                (["public"],),
            ).fetchall()
        names = sorted({row["name"].strip().rstrip(".").lower() for row in rows})
        # Suffix matching already covers subdomains, so keep only top-level ones
        name_set = set(names)
        names = [
            name
            for name in names
            if name
            and not any(
                ".".join(name.split(".")[i:]) in name_set
                for i in range(1, len(name.split(".")))
            )
        ]
        content = "".join(f"{name}\n" for name in names)
        if content == self._dnsdist_domains_content:
            return

        dir_name = os.path.dirname(DNSDIST_DOMAINS_FILE)
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".privatedns-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, DNSDIST_DOMAINS_FILE)
        except Exception:
            os.unlink(tmp_path)
            raise
        self._dnsdist_domains_content = content
        LOG.info("Updated %s: %d domains", DNSDIST_DOMAINS_FILE, len(names))

    def _iteration(self):
        try:
            self._write_dnsdist_domains()
        except Exception:
            LOG.exception("Unable to write %s", DNSDIST_DOMAINS_FILE)

        self._check_pending_future()
        if self._pending_future is not None:
            LOG.debug("Previous DNS sync request still pending, skipping")
            return

        with contexts.Context().session_manager():
            credentials = self._get_ecosystem_credentials()
            if credentials is None:
                LOG.debug("DNS sync variables are not configured, skipping")
                return

        self._pending_future = self._executor.submit(
            self._do_sync,
            *credentials,
        )

    def _do_sync(self, endpoint, realm_uuid, realm_secret):
        try:
            with contexts.Context().session_manager():
                self._sync_all_domains(endpoint, realm_uuid, realm_secret)
        except Exception:
            LOG.exception("DNS sync iteration failed")
