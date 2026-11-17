#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import threading
from unittest.mock import patch
import uuid

from cryptography import x509
from gcl_sdk.agents.universal.dm import models
import psycopg
from psycopg import sql
import pytest
from restalchemy.storage.sql import engines

from exordos_core.agent.universal.drivers.secret.backend import cert as backend
from exordos_core.common import spiffe


@pytest.mark.skipif(
    not os.environ.get("EXORDOS_CERTIFICATE_TEST_DB"), reason="Requires test PostgreSQL"
)
def test_authority_survives_restart_and_concurrent_first_issuance():
    admin_dsn = os.environ["EXORDOS_CERTIFICATE_TEST_DB"]
    name = "spiffe_test_" + uuid.uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(name)
            )
        )
    dsn = admin_dsn.rsplit("/", 1)[0] + "/" + name
    path = (
        Path(__file__).resolve().parents[8]
        / "migrations/0015-spiffe-authorities-2f5766.py"
    )
    spec = importlib.util.spec_from_file_location("spiffe_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Session:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params):
            return self.connection.execute(query, params)

    try:
        with psycopg.connect(dsn) as connection:
            migration.migration_step.upgrade(Session(connection))
        engines.engine_factory.configure_factory(db_url=dsn)
        project = uuid.uuid4()

        def issue():
            value = {
                "uuid": str(uuid.uuid4()),
                "project_id": str(project),
                "name": "test",
                "method": {
                    "kind": "spiffe",
                    "spiffe_id": "spiffe://test.example/service",
                    "validity_days": 30,
                },
                "domains": [],
                "expiration_threshold": 14,
                "overcome_threshold": False,
            }
            target = models.Resource.from_value(value, "certificate", frozenset(value))
            return backend.CertBotBackendClient._spiffe_certificate(target)

        barrier = threading.Barrier(2)
        original = spiffe.create_authority

        def create(domain):
            result = original(domain)
            barrier.wait(timeout=10)
            return result

        with (
            patch.object(spiffe, "create_authority", side_effect=create),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = [executor.submit(issue) for _ in range(2)]
            successes = [f.result() for f in results if f.exception() is None]
            failures = [f.exception() for f in results if f.exception() is not None]
        assert len(successes) == 1 and len(failures) == 1
        with psycopg.connect(dsn) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM storage_spiffe_authorities"
                ).fetchone()[0]
                == 1
            )
        # A new engine emulates a restarted issuer, including the losing writer retry.
        engines.engine_factory.destroy_engine()
        engines.engine_factory.configure_factory(db_url=dsn)
        retried = issue()
        first_chain = x509.load_pem_x509_certificates(successes[0].fullchain.encode())
        retry_chain = x509.load_pem_x509_certificates(retried.fullchain.encode())
        assert first_chain[1] == retry_chain[1]
        retry_chain[0].verify_directly_issued_by(first_chain[1])
        engines.engine_factory.destroy_engine()
        with psycopg.connect(dsn) as connection:
            migration.migration_step.downgrade(Session(connection))
            assert (
                connection.execute(
                    "SELECT to_regclass('storage_spiffe_authorities')"
                ).fetchone()[0]
                is None
            )
    finally:
        engines.engine_factory.destroy_engine()
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
