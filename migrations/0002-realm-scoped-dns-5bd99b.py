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

from restalchemy.storage.sql import migrations

DOMAINS_VIEW = """
CREATE OR REPLACE VIEW public.domains AS
SELECT id, name, master, last_check, type, notified_serial, account, options, catalog
FROM public.dns_domains
WHERE NOT sync_only
"""

RECORDS_VIEW = """
CREATE OR REPLACE VIEW public.records AS
SELECT records.domain_id,
       records.name,
       records.type,
       records.content,
       records.ttl,
       records.prio,
       records.disabled,
       records.ordername,
       records.auth
FROM public.dns_records records
JOIN public.dns_domains domains ON domains.id = records.domain_id
WHERE NOT domains.sync_only
"""

LEGACY_DOMAINS_VIEW = """
CREATE OR REPLACE VIEW public.domains AS
SELECT id, name, master, last_check, type, notified_serial, account, options, catalog
FROM public.dns_domains
"""

LEGACY_RECORDS_VIEW = """
CREATE OR REPLACE VIEW public.records AS
SELECT domain_id, name, type, content, ttl, prio, disabled, ordername, auth
FROM public.dns_records
"""


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0001-zero-entities-5a7a0e.py"]

    @property
    def migration_id(self):
        return "5bd99bee-1705-4329-ae88-0848f85b9546"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE public.dns_domains "
            "ADD COLUMN sync_only boolean DEFAULT false NOT NULL"
        )
        session.execute(
            "ALTER TABLE public.dns_domains ADD COLUMN realm_id character varying(32)"
        )
        session.execute(
            "ALTER TABLE public.dns_domains ADD CONSTRAINT "
            "dns_domains_realm_id_format_check CHECK "
            "(realm_id IS NULL OR realm_id ~ '^[0-9a-f]{6,32}$')"
        )
        session.execute(
            "ALTER TABLE public.dns_domains ADD CONSTRAINT "
            "dns_domains_realm_scope_check CHECK "
            "((realm_id IS NULL AND NOT sync_only) OR "
            "(realm_id IS NOT NULL AND sync_only AND sync_to_ecosystem))"
        )
        session.execute(
            "CREATE UNIQUE INDEX dns_domains_realm_id_idx "
            "ON public.dns_domains (realm_id) WHERE realm_id IS NOT NULL"
        )
        session.execute(DOMAINS_VIEW)
        session.execute(RECORDS_VIEW)

    def downgrade(self, session):
        session.execute(LEGACY_DOMAINS_VIEW)
        session.execute(LEGACY_RECORDS_VIEW)
        session.execute("DROP INDEX public.dns_domains_realm_id_idx")
        session.execute(
            "ALTER TABLE public.dns_domains "
            "DROP CONSTRAINT dns_domains_realm_scope_check"
        )
        session.execute(
            "ALTER TABLE public.dns_domains "
            "DROP CONSTRAINT dns_domains_realm_id_format_check"
        )
        session.execute("ALTER TABLE public.dns_domains DROP COLUMN realm_id")
        session.execute("ALTER TABLE public.dns_domains DROP COLUMN sync_only")


migration_step = MigrationStep()
