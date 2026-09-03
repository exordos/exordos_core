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
import time

import bazooka
from bazooka import exceptions as bazooka_exc
from gcl_iam import tokens as iam_tokens
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

# What an ecosystem that cannot read the filter expression answers with.
# The current one rejects it as a bad request; one that predates the
# expression takes `q` for an ordinary field filter, fails to find a field
# by that name and answers 500. Any other status -- a gateway that is
# briefly down, a token that stopped working -- says nothing about the
# filter, so it is not remembered as a refusal and not retried without one.
FILTER_UNSUPPORTED_STATUSES = frozenset((400, 500))


class DNSSyncService(basic.BasicService):
    """Periodically syncs local DNS records to the ecosystem."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = bazooka.Client(default_timeout=DNS_SYNC_TIMEOUT)
        self._executor = futures.ThreadPoolExecutor(max_workers=DNS_SYNC_POOL_SIZE)
        self._pending_future = None
        self._initialized = False
        self._last_full_sync_at = FULL_SYNC_INTERVAL + 1  # sync immediately on start
        self._last_fast_sync_dt = None
        self._owner_tag = None
        # The token the cached owner was read out of. A token that
        # changes is a different subject to be, and the tag decides what
        # gets removed, so it is re-read rather than carried over. The
        # cache is not there to save the reading, which is local and
        # cheap, but to keep the log to one line per token.
        self._owner_token = None
        # The endpoints that refused the filter expression, so a mirror
        # talking to an older one asks for it only the first time -- and
        # one that moves to a newer one is not stuck with the answer the
        # old one gave.
        self._filter_lang_refused = set()

    def _get_variable_value(self, var_uuid):
        """Read variable value from ValuesStore by UUID."""
        variable = vs_models.Variable.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(var_uuid)}
        )
        if variable is None:
            return None
        return variable.value

    def _get_ecosystem_credentials(self):
        """Read ecosystem endpoint, stand UUID, secret and token from VS."""
        endpoint = self._get_variable_value(c.VAR_ECOSYSTEM_ENDPOINT_UUID)
        realm_uuid = self._get_variable_value(c.VAR_REALM_UUID_UUID)
        realm_secret = self._get_variable_value(c.VAR_REALM_SECRET_UUID)
        access_token = self._get_variable_value(c.VAR_REALM_ACCESS_TOKEN_UUID)
        if not all([endpoint, realm_uuid, realm_secret, access_token]):
            return None
        return endpoint, realm_uuid, realm_secret, access_token

    def _make_basic_auth(self, realm_uuid, realm_secret):
        return requests_auth.HTTPBasicAuth(realm_uuid, realm_secret)

    @staticmethod
    def _bearer_headers(access_token):
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Ecosystem HTTP helpers
    # ------------------------------------------------------------------

    def _eco_get_realm(self, endpoint, realm_uuid, auth):
        """GET /api/ecosystem/v1/realms/{realm_uuid} -> realm dict."""
        url = f"{endpoint}/api/ecosystem/v1/realms/{realm_uuid}"
        resp = self._client.get(url, auth=auth)
        return resp.json()

    def _realm_owner_tag(self, access_token):
        """The tag the ecosystem stamps on the records this mirror writes.

        Read out of the token this mirror writes with. The ecosystem
        stamps the subject of the request it is serving, and `sub` is
        that subject -- the same uuid its introspection would report,
        for the price of a base64 decode instead of a round trip. The
        signature is the ecosystem's to check; here the token is only
        being read, and one this service could not read is one it could
        not have used either.

        None when the token does not parse or carries no subject, and
        remembered as None so a broken token is reported once rather
        than every minute. Without a tag the mirror still creates and
        updates -- it only stops removing, which is the safe half to
        lose.
        """
        if self._owner_token == access_token:
            return self._owner_tag
        tag = None
        try:
            token_info = iam_tokens.UnverifiedToken(access_token).token_info
        except Exception:
            LOG.exception("The realm access token does not parse")
        else:
            user_uuid = token_info.get("sub")
            if user_uuid:
                tag = c.owner_user_tag(user_uuid)
            else:
                LOG.warning("The realm access token names no subject")
        self._owner_tag = tag
        self._owner_token = access_token
        if tag:
            LOG.info("Records of this realm are owned by %s", tag)
        else:
            LOG.warning("This realm has no owner tag; nothing will be removed")
        return tag

    def _eco_list_domains(self, endpoint, headers):
        """GET /api/core/v1/dns/domains/ -> list of domains."""
        url = f"{endpoint}/api/core/v1/dns/domains/"
        resp = self._client.get(url, headers=headers)
        return resp.json()

    def _eco_create_domain(self, endpoint, headers, name):
        """POST /api/core/v1/dns/domains/"""
        url = f"{endpoint}/api/core/v1/dns/domains/"
        resp = self._client.post(
            url,
            json={"name": name},
            headers=headers,
        )
        return resp.json()

    def _eco_list_records(self, endpoint, headers, eco_domain_uuid, owner_tag=None):
        """GET /api/core/v1/dns/domains/{uuid}/records/

        Asks for this mirror's own records when it knows which those are:
        the rest of the zone is not its business, and a zone is read on
        every reconciliation. An ecosystem that cannot read the filter
        refuses it -- see `FILTER_UNSUPPORTED_STATUSES` for the two ways
        it does -- and then the whole zone comes back and the owner is
        checked here instead: the same answer, read further from the
        index. Any other failure is the caller's to see, and is raised
        rather than turned into a second request that would fail too.
        """
        url = f"{endpoint}/api/core/v1/dns/domains/{eco_domain_uuid}/records/"
        if owner_tag and endpoint not in self._filter_lang_refused:
            try:
                resp = self._client.get(
                    url,
                    headers=headers,
                    params={"q": 'tags:"%s"' % owner_tag},
                )
                return resp.json()
            except bazooka_exc.BaseHTTPException as e:
                if e.code not in FILTER_UNSUPPORTED_STATUSES:
                    raise
                LOG.info(
                    "The ecosystem refused the tag filter with %s; "
                    "reading whole zones from %s",
                    e.code,
                    endpoint,
                )
                self._filter_lang_refused.add(endpoint)
        resp = self._client.get(url, headers=headers)
        return resp.json()

    def _eco_create_record(self, endpoint, headers, eco_domain_uuid, record_data):
        """POST /api/core/v1/dns/domains/{uuid}/records/

        A record can be there without this mirror having seen it: asking
        for its own records hides anything written before records carried
        an owner. Creating what is already there is then an update, not a
        conflict to report every minute -- and the update claims a record
        nobody owns, so the next pass finds it among this mirror's own
        instead of coming back here forever.
        """
        url = f"{endpoint}/api/core/v1/dns/domains/{eco_domain_uuid}/records/"
        try:
            self._client.post(
                url,
                json=record_data,
                headers=headers,
            )
        except bazooka_exc.ConflictError:
            self._eco_update_record(
                endpoint,
                headers,
                eco_domain_uuid,
                record_data["uuid"],
                record_data,
            )

    def _eco_update_record(
        self, endpoint, headers, eco_domain_uuid, record_uuid, record_data
    ):
        """PUT /api/core/v1/dns/domains/{uuid}/records/{rid}"""
        url = (
            f"{endpoint}/api/core/v1"
            f"/dns/domains/{eco_domain_uuid}/records/{record_uuid}"
        )
        self._client.put(
            url,
            json=record_data,
            headers=headers,
        )

    def _eco_delete_record(self, endpoint, headers, eco_domain_uuid, record_uuid):
        """DELETE /api/core/v1/dns/domains/{uuid}/records/{rid}"""
        url = (
            f"{endpoint}/api/core/v1"
            f"/dns/domains/{eco_domain_uuid}/records/{record_uuid}"
        )
        self._client.delete(url, headers=headers)

    # ------------------------------------------------------------------
    # Initialization: fetch realm domain, mark local domain for sync
    # ------------------------------------------------------------------

    def _ensure_realm_domain(self, endpoint, realm_uuid, basic_auth):
        """Fetch realm domain from ecosystem and ensure it exists locally
        with sync_to_ecosystem=True.
        """
        realm = self._eco_get_realm(endpoint, realm_uuid, basic_auth)
        realm_domain_name = realm.get("domain")
        if not realm_domain_name:
            LOG.warning("Realm has no domain field, skipping init")
            return False

        LOG.info("Realm domain: %s", realm_domain_name)

        domain = dns_models.Domain.objects.get_one_or_none(
            filters={"name": dm_filters.EQ(realm_domain_name)}
        )
        if domain is None:
            LOG.info(
                "Creating local domain %s for ecosystem sync",
                realm_domain_name,
            )
            domain = dns_models.Domain(
                name=realm_domain_name,
                project_id=c.ZERO_UUID,
                sync_to_ecosystem=True,
            )
            domain.save()
        elif not domain.sync_to_ecosystem:
            LOG.info("Marking domain %s for ecosystem sync", realm_domain_name)
            domain.sync_to_ecosystem = True
            domain.update()

        return True

    # ------------------------------------------------------------------
    # Record sync logic
    # ------------------------------------------------------------------

    @staticmethod
    def _build_record_data(record):
        """Convert a local Record to the dict sent to ecosystem."""
        record_prop = record.properties.properties["record"]
        record_type = record_prop.get_property_type()
        return {
            "uuid": str(record.uuid),
            "type": record.type,
            "ttl": record.ttl,
            "disabled": record.disabled,
            "record": record_type.to_simple_type(record.record),
        }

    # ------------------------------------------------------------------
    # Fast path: push only new / updated records since last cycle
    # ------------------------------------------------------------------

    def _fast_sync(self, endpoint, headers, since_dt):
        """Query all recently changed records, push them to ecosystem.

        Records are selected globally by updated_at, then grouped by
        domain.  Only domains with sync_to_ecosystem=True are processed.

        New vs existing is determined by created_at: if the record was
        created after since_dt it is new, otherwise it is an update.
        This avoids fetching ecosystem records on each fast cycle.
        """
        recent_records = dns_models.Record.objects.get_all(
            filters={
                "updated_at": dm_filters.GE(since_dt),
                "type": dm_filters.NE("SOA"),
            }
        )
        if not recent_records:
            return

        # Group records by domain, keep only sync-enabled domains
        by_domain = {}
        for rec in recent_records:
            domain = rec.domain
            if not domain.sync_to_ecosystem:
                continue
            by_domain.setdefault(domain, []).append(rec)

        if not by_domain:
            return

        # Fetch ecosystem domain list once for all domains
        eco_domains = self._eco_list_domains(endpoint, headers)
        eco_domain_map = {ed.get("name"): ed["uuid"] for ed in eco_domains}

        for domain, records in by_domain.items():
            eco_domain_uuid = eco_domain_map.get(domain.name)
            if eco_domain_uuid is None:
                LOG.debug(
                    "Fast sync: domain %s not yet in ecosystem, "
                    "skipping until full sync",
                    domain.name,
                )
                continue

            for rec in records:
                data = self._build_record_data(rec)
                try:
                    if rec.created_at >= since_dt:
                        LOG.debug(
                            "Fast sync: creating record %s %s in %s",
                            rec.type,
                            rec.name,
                            domain.name,
                        )
                        self._eco_create_record(
                            endpoint,
                            headers,
                            eco_domain_uuid,
                            data,
                        )
                    else:
                        LOG.debug(
                            "Fast sync: updating record %s %s in %s",
                            rec.type,
                            rec.name,
                            domain.name,
                        )
                        self._eco_update_record(
                            endpoint,
                            headers,
                            eco_domain_uuid,
                            str(rec.uuid),
                            data,
                        )
                except Exception:
                    LOG.exception(
                        "Fast sync: failed to push record %s %s",
                        rec.type,
                        rec.name,
                    )

    # ------------------------------------------------------------------
    # Full reconciliation: build diff, create / delete
    # ------------------------------------------------------------------

    def _full_sync_domain(self, domain, endpoint, headers, eco_domain_uuid, owner_tag):
        """Full diff-based sync for a single domain.

        `owner_tag` is which of the zone's records are this mirror's own,
        settled once for the whole pass by `_realm_owner_tag`.
        """
        domain_name = domain.name

        # Get local records (skip SOA)
        local_records = dns_models.Record.objects.get_all(
            filters={
                "domain": dm_filters.EQ(domain),
                "type": dm_filters.NE("SOA"),
            }
        )

        eco_records = self._eco_list_records(
            endpoint, headers, eco_domain_uuid, owner_tag
        )
        eco_records = [r for r in eco_records if r.get("type") != "SOA"]

        # Build UUID-keyed maps
        local_map = {str(r.uuid): r for r in local_records}
        eco_map = {r["uuid"]: r for r in eco_records if r.get("uuid")}

        local_uuids = set(local_map.keys())
        eco_uuids = set(eco_map.keys())

        # Create missing
        for uid in local_uuids - eco_uuids:
            rec = local_map[uid]
            LOG.info(
                "Creating record %s %s in ecosystem domain %s",
                rec.type,
                rec.name,
                domain_name,
            )
            try:
                self._eco_create_record(
                    endpoint,
                    headers,
                    eco_domain_uuid,
                    self._build_record_data(rec),
                )
            except Exception:
                LOG.exception(
                    "Failed to create record %s %s in ecosystem",
                    rec.type,
                    rec.name,
                )

        # Update existing only if content differs
        compare_keys = ("type", "ttl", "disabled", "record")
        for uid in local_uuids & eco_uuids:
            rec = local_map[uid]
            local_data = self._build_record_data(rec)
            eco_rec = eco_map[uid]
            if all(local_data.get(k) == eco_rec.get(k) for k in compare_keys):
                continue
            try:
                self._eco_update_record(
                    endpoint,
                    headers,
                    eco_domain_uuid,
                    uid,
                    local_data,
                )
            except Exception:
                LOG.exception(
                    "Failed to update record %s %s in ecosystem",
                    rec.type,
                    rec.name,
                )

        # Delete extra -- but only what this mirror put there. A zone in
        # the ecosystem is not this realm's alone: the ecosystem publishes
        # the realm's own ingress records into it, and an operator may add
        # more. Anything not carrying this mirror's owner tag, including
        # anything created before tags existed, is somebody else's row.
        for uid in eco_uuids - local_uuids:
            eco_rec = eco_map[uid]
            if not owner_tag or owner_tag not in (eco_rec.get("tags") or []):
                LOG.debug(
                    "Keeping %s %s in ecosystem domain %s: not this realm's row",
                    eco_rec.get("type"),
                    eco_rec.get("name"),
                    domain_name,
                )
                continue
            LOG.info(
                "Deleting record %s %s from ecosystem domain %s",
                eco_rec.get("type"),
                eco_rec.get("name"),
                domain_name,
            )
            try:
                self._eco_delete_record(
                    endpoint,
                    headers,
                    eco_domain_uuid,
                    uid,
                )
            except Exception:
                LOG.exception(
                    "Failed to delete record %s %s from ecosystem",
                    eco_rec.get("type"),
                    eco_rec.get("name"),
                )

    # ------------------------------------------------------------------
    # Orchestration: decide fast path vs full reconciliation
    # ------------------------------------------------------------------

    def _sync_all_domains(self, endpoint, realm_uuid, realm_secret, access_token):
        """Sync all local domains marked for ecosystem sync."""
        basic_auth = self._make_basic_auth(realm_uuid, realm_secret)
        headers = self._bearer_headers(access_token)

        # Initialize on first successful call
        if not self._initialized:
            try:
                initialized = self._ensure_realm_domain(
                    endpoint, realm_uuid, basic_auth
                )
            except bazooka_exc.ForbiddenError:
                LOG.warning("Not authorized to fetch realm, skipping")
                return
            except Exception:
                LOG.exception("Failed to initialize realm domain")
                return

            if not initialized:
                return
            self._initialized = True

        now = time.monotonic()
        need_full = (now - self._last_full_sync_at) >= FULL_SYNC_INTERVAL

        if need_full:
            domains = dns_models.Domain.objects.get_all(
                filters={"sync_to_ecosystem": dm_filters.EQ(True)}
            )
            if not domains:
                LOG.debug("No domains marked for ecosystem sync")
                return

            LOG.debug("Running full DNS reconciliation")
            # Which records are this mirror's own does not vary by domain:
            # settled once here, so a token that cannot be read is reported
            # once a pass rather than once a zone.
            owner_tag = self._realm_owner_tag(access_token)
            eco_domains = self._eco_list_domains(endpoint, headers)
            eco_domain_map = {ed.get("name"): ed["uuid"] for ed in eco_domains}

            for domain in domains:
                try:
                    eco_domain_uuid = eco_domain_map.get(domain.name)
                    if eco_domain_uuid is None:
                        LOG.info(
                            "Creating domain %s in ecosystem",
                            domain.name,
                        )
                        eco_domain = self._eco_create_domain(
                            endpoint, headers, domain.name
                        )
                        eco_domain_uuid = eco_domain["uuid"]

                    self._full_sync_domain(
                        domain,
                        endpoint,
                        headers,
                        eco_domain_uuid,
                        owner_tag,
                    )
                except Exception:
                    LOG.exception("Full sync failed for domain %s", domain.name)
            self._last_full_sync_at = now
            self._last_fast_sync_dt = datetime.datetime.now(datetime.timezone.utc)
        else:
            since_dt = self._last_fast_sync_dt
            if since_dt is None:
                return
            LOG.debug("Running fast DNS sync (since %s)", since_dt)
            self._fast_sync(endpoint, headers, since_dt)
            self._last_fast_sync_dt = datetime.datetime.now(datetime.timezone.utc)

    # ------------------------------------------------------------------
    # Service loop
    # ------------------------------------------------------------------

    def _check_pending_future(self):
        """Clear completed future to allow the next submission."""
        if self._pending_future is not None and self._pending_future.done():
            self._pending_future = None

    def _iteration(self):
        self._check_pending_future()

        if self._pending_future is not None:
            LOG.debug("Previous DNS sync request still pending, skipping")
            return

        with contexts.Context().session_manager():
            creds = self._get_ecosystem_credentials()
            if creds is None:
                LOG.debug("DNS sync variables are not configured, skipping")
                return

            endpoint, realm_uuid, realm_secret, access_token = creds

        self._pending_future = self._executor.submit(
            self._do_sync,
            endpoint,
            realm_uuid,
            realm_secret,
            access_token,
        )

    def _do_sync(self, endpoint, realm_uuid, realm_secret, access_token):
        """Run the full sync inside a DB session (executed in thread)."""
        try:
            with contexts.Context().session_manager():
                self._sync_all_domains(endpoint, realm_uuid, realm_secret, access_token)
        except Exception:
            LOG.exception("DNS sync iteration failed")
