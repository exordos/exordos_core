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
from restalchemy.api import resources
from restalchemy.storage.sql import migrations
from restalchemy.tests.functional import db_utils as ra_db_utils
from restalchemy.tests.functional.restapi.ra_based.microservice import service

ENDPOINT_TEMPLATE = "http://127.0.0.1:%s/"


def build_app(builder: tp.Callable[..., tp.Any], *args: tp.Any, **kwargs: tp.Any):
    """Build a WSGI application without dropping the other services' routes.

    restalchemy keeps one process-wide resource map and every application
    replaces it with its own routes on build. Several services share a test
    worker, so keep the union: a service whose resources are missing from
    the map answers 404 as soon as it has to build a Location header.
    """
    known = dict(resources.ResourceMap.resource_map)
    app = builder(*args, **kwargs)
    known.update(resources.ResourceMap.resource_map)
    resources.ResourceMap.set_resource_map(known)
    return app


class RestServiceTestCase(ra_db_utils.DBEngineMixin):
    __LAST_MIGRATION__ = None
    __FIRST_MIGRATION__ = None
    __API_VERSION__ = "v1"
    __APP__ = None

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
        # The engine is process-wide and shared by every service in this
        # worker (see setup_db_for_worker in conftest), so it is not created
        # here: doing so would replace it under the services already running.

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

    @classmethod
    def shutdown_engine(cls):
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
        # Apply migrations
        self.apply_all_migrations()

    def rollback_migrations(self) -> None:
        # Rollback migrations
        self._migration.rollback_migration(self.__FIRST_MIGRATION__)
        self._sdk_migration.rollback_migration(sdk_migrations.INIT_MIGRATION_FILENAME)

    def teardown_method(self) -> None:
        self.rollback_migrations()
