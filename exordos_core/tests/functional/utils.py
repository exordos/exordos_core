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

import contextlib
import os
import pathlib
import socket
import typing as tp
from urllib import parse

from gcl_sdk import migrations as sdk_migrations
from restalchemy.storage.sql import migrations
from restalchemy.tests.functional import db_utils as ra_db_utils
from restalchemy.tests.functional.restapi.ra_based.microservice import service

ENDPOINT_TEMPLATE = "http://127.0.0.1:%s/"
# Prefix of the shadow tables holding the rows the migrations seed.
SEED_TABLE_PREFIX = "seed_snapshot_"


class RestServiceTestCase(ra_db_utils.DBEngineMixin):
    __LAST_MIGRATION__ = None
    __FIRST_MIGRATION__ = None
    __API_VERSION__ = "v1"
    __APP__ = None

    _seed_snapshot: tp.Optional[tp.Dict[str, tp.Any]] = None

    @classmethod
    def get_endpoint(cls, template: str = ENDPOINT_TEMPLATE) -> str:
        return template % cls.service_port

    @classmethod
    def find_free_port(cls) -> int:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.bind(("127.0.0.1", 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            return s.getsockname()[1]

    @classmethod
    def setup_class(cls):
        cls.init_engine()
        # Run service
        cls.service_port = cls.find_free_port()
        url = parse.urlparse(cls.get_endpoint())
        cls._service = service.RESTService(
            bind_host=url.hostname, bind_port=url.port, app_root=cls.__APP__
        )
        cls._service.start()

    @classmethod
    def teardown_class(cls):
        cls._service.stop()
        cls.drop_all_views()
        cls.drop_all_tables(cascade=True)
        # Hack for psycopg to finish fast, otherwise we'll need to wait for GC
        cls.engine.__del__()
        cls.destroy_engine()

    @staticmethod
    def get_migration_engine(
        migrations_path: tp.Optional[str] = None,
    ) -> migrations.MigrationEngine:
        if migrations_path is None:
            migrations_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "../../../migrations",
            )

        migration_engine = migrations.MigrationEngine(migrations_path=migrations_path)
        return migration_engine

    @classmethod
    def apply_migrations(
        cls,
        migration_engine: migrations.MigrationEngine,
        last_migration: tp.Optional[str] = None,
    ) -> None:
        last_migration = last_migration or migration_engine.get_latest_migration()
        migration_engine.apply_migration(last_migration)

    def apply_all_migrations(self) -> None:
        self._sdk_migration = self.get_migration_engine(
            migrations_path=str(pathlib.Path(sdk_migrations.__file__).parent)
        )
        self._migration = self.get_migration_engine(
            migrations_path=os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "../../../migrations",
            )
        )
        self.rollback_migrations()

        self.apply_migrations(
            migration_engine=self._sdk_migration,
            last_migration=None,
        )
        self.apply_migrations(
            migration_engine=self._migration,
            last_migration=self.__LAST_MIGRATION__,
        )

    @property
    def base_url(self) -> str:
        return self.get_endpoint() + self.__API_VERSION__ + "/"

    @classmethod
    def drop_table(cls, table_name, session=None, cascade=False):
        cascade = " CASCADE" if cascade else ""
        with cls.engine.session_manager(session=session) as s:
            s.execute(
                f"drop table if exists {session.engine.escape(table_name)}{cascade}"
            )

    @classmethod
    def drop_all_tables(cls, session=None, cascade=False):
        with cls.engine.session_manager(session=session) as s:
            tables = cls.get_all_tables(session=s)
            for table in tables:
                cls.drop_table(table, session=s, cascade=cascade)

    @classmethod
    def get_all_views(cls, session=None) -> tp.Set[str]:
        with cls.engine.session_manager(session=session) as s:
            if session.engine.dialect.name == "mysql":
                res = s.execute("""
                    select
                        table_name as table_name
                    from information_schema.views
                    where table_schema = database();
                """).fetchall()
            elif session.engine.dialect.name == "postgresql":
                res = s.execute("""
                    select
                        table_name as table_name
                    from information_schema.views
                    where table_schema = current_schema();
                """).fetchall()
            else:
                raise NotImplementedError("Unsupported dialect")
        return {row["table_name"] for row in res}

    @classmethod
    def drop_all_views(cls, session=None):
        with cls.engine.session_manager(session=session) as s:
            views = cls.get_all_views(session=s)
            for view in views:
                cls.drop_view(view, session=s)

    @classmethod
    def drop_view(cls, view_name, session=None):
        with cls.engine.session_manager(session=session) as s:
            s.execute(f"drop view if exists {session.engine.escape(view_name)}")

    def setup_method(self) -> None:
        # Rebuilding the whole schema before every test costs ~0.5s. Do it
        # once, snapshot the rows the migrations seed, and reset later tests
        # by emptying the tables and restoring that snapshot (~0.01s). The
        # snapshot is only usable while the schema it was taken from is still
        # there -- another service's teardown_method/teardown_class shares the
        # database and may have dropped it.
        if self._seed_snapshot is not None and self._snapshot_is_usable():
            self._restore_seed_snapshot()
            return

        self.apply_all_migrations()
        self._seed_snapshot = (
            self._take_seed_snapshot()
            if self.engine.dialect.name == "postgresql"
            else None
        )

    @classmethod
    def _base_tables(cls, session) -> tp.List[str]:
        rows = session.execute(
            """
            select table_name as table_name
            from information_schema.tables
            where table_schema = current_schema()
              and table_type = 'BASE TABLE';
            """
        ).fetchall()
        return sorted(
            row["table_name"]
            for row in rows
            if row["table_name"] != migrations.RA_MIGRATION_TABLE_NAME
            and not row["table_name"].startswith(SEED_TABLE_PREFIX)
        )

    def _take_seed_snapshot(self) -> tp.Dict[str, tp.Any]:
        with self.engine.session_manager() as s:
            tables = self._base_tables(s)
            # Copy the seeded rows into shadow tables so that resetting is a
            # server-side INSERT ... SELECT rather than a round trip per row.
            seeded = []
            for table in tables:
                s.execute(f'DROP TABLE IF EXISTS "{SEED_TABLE_PREFIX}{table}"')
                s.execute(
                    f'CREATE TABLE "{SEED_TABLE_PREFIX}{table}" AS TABLE "{table}"'
                )
                if s.execute(
                    f'SELECT 1 FROM "{SEED_TABLE_PREFIX}{table}" LIMIT 1'
                ).fetchall():
                    seeded.append(table)
                else:
                    s.execute(f'DROP TABLE "{SEED_TABLE_PREFIX}{table}"')
            sequences = [
                (
                    row["sequence_name"],
                    s.execute(
                        f"""select last_value, is_called
                            from "{row["sequence_name"]}";"""
                    ).fetchall()[0],
                )
                for row in s.execute(
                    """
                    select sequence_name as sequence_name
                    from information_schema.sequences
                    where sequence_schema = current_schema();
                    """
                ).fetchall()
            ]

        # The whole reset is one multi-statement round trip. SET LOCAL keeps
        # the constraint triggers disabled for this transaction only, so a
        # failed reset cannot hand a pooled connection back with foreign keys
        # still off; they have to be off because the seeded rows reference
        # each other and cannot be restored in any FK-satisfying order.
        # DELETE rather than TRUNCATE: almost every table is empty by the end
        # of a test, and DELETE barely touches those, while TRUNCATE rewrites
        # each of them regardless (0.008s against 0.12s for the whole schema).
        # The sequences are restored below, so RESTART IDENTITY is not needed.
        statements = ["SET LOCAL session_replication_role = replica"]
        statements += [f'DELETE FROM "{table}"' for table in tables]
        statements += [
            f'INSERT INTO "{table}" SELECT * FROM "{SEED_TABLE_PREFIX}{table}"'
            for table in seeded
        ]
        statements += [
            f"SELECT setval('{name}', {state['last_value']}, {state['is_called']})"
            for name, state in sequences
        ]
        return {"tables": set(tables), "reset": ";\n".join(statements)}

    def _snapshot_is_usable(self) -> bool:
        with self.engine.session_manager() as s:
            return set(self._base_tables(s)) == self._seed_snapshot["tables"]

    def _restore_seed_snapshot(self) -> None:
        with self.engine.session_manager() as s:
            s.execute(self._seed_snapshot["reset"])

    def rollback_migrations(self) -> None:
        # Rollback migrations
        self._migration.rollback_migration(self.__FIRST_MIGRATION__)
        self._sdk_migration.rollback_migration(sdk_migrations.INIT_MIGRATION_FILENAME)

    def teardown_method(self) -> None:
        self.rollback_migrations()
