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

import os
import typing as tp
from urllib.parse import urlparse
import uuid as sys_uuid

import bazooka
from gcl_iam import tokens
from gcl_iam.tests.functional import clients as iam_clients
from gcl_sdk.agents.universal.dm import models as sdk_ua_models
from gcl_sdk.events import clients as sdk_clients
from gcl_sdk.infra.dm import models as sdk_infra_models
import netaddr
import pytest
from restalchemy.dm import filters as dm_filters
from restalchemy.storage.sql import engines

from exordos_core.common import constants as c
from exordos_core.common import utils
from exordos_core.common.dm import targets as ct
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as node_models
from exordos_core.compute.node_set.dm import models as node_set_models
from exordos_core.config import constants as cc
from exordos_core.config.dm import models as conf_models
from exordos_core.elements.dm import utils as element_utils
from exordos_core.secret import constants as sc
from exordos_core.secret.dm import models as secret_models
from exordos_core.tests.functional import consts
from exordos_core.tests.functional import utils as test_utils
from exordos_core.user_api.api import app as user_app
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam import drivers as user_drivers
from exordos_core.user_api.iam.dm import models as iam_models
from exordos_core.user_api.network.dm import models as network_models

FIRST_MIGRATION = "0000-root-d34de1.py"


@pytest.fixture(scope="session")
def context_storage():
    hs256_jwks_encryption_key = os.getenv(
        "HS256_JWKS_ENCRYPTION_KEY",
        c.DEFAULT_HS256_JWKS_ENCRYPTION_KEY,
    )
    return utils.get_context_storage(
        global_salt=os.getenv("GLOBAL_SALT", c.DEFAULT_GLOBAL_SALT),
        hs256_jwks_encryption_key=hs256_jwks_encryption_key,
        events_client=sdk_clients.DummyEventClient(),
    )


@pytest.fixture(scope="session")
def decode_id_token():
    driver = user_drivers.DirectDriver()

    def _decode(token: str):
        unverified = tokens.UnverifiedToken(token)
        algorithm = driver.get_algorithm(unverified)
        return algorithm.decode(token, ignore_audience=True)

    return _decode


@pytest.fixture(scope="session")
def admin_username():
    return c.DEFAULT_ADMIN_USERNAME


@pytest.fixture(scope="session")
def admin_password():
    return os.getenv("ADMIN_PASSWORD", c.DEFAULT_ADMIN_PASSWORD)


@pytest.fixture(scope="session")
def default_client_id():
    return c.DEFAULT_CLIENT_ID


@pytest.fixture(scope="session")
def default_client_secret():
    return os.getenv("DEFAULT_CLIENT_SECRET", c.DEFAULT_CLIENT_SECRET)


@pytest.fixture(scope="session")
def default_client_uuid():
    return c.DEFAULT_CLIENT_UUID


@pytest.fixture(scope="module")
def user_api_service(context_storage):
    iam_engine_driver = user_drivers.DirectDriver()

    class ApiRestService(test_utils.RestServiceTestCase):
        __FIRST_MIGRATION__ = FIRST_MIGRATION
        __APP__ = user_app.build_wsgi_application(
            context_storage=context_storage,
            iam_engine_driver=iam_engine_driver,
        )

    rest_service = ApiRestService()
    rest_service.setup_class()

    yield rest_service

    rest_service.teardown_class()


@pytest.fixture()
def user_api(user_api_service: test_utils.RestServiceTestCase):
    user_api_service.setup_method()

    yield user_api_service

    user_api_service.teardown_method()


@pytest.fixture()
def auth_user_admin(
    admin_username: str,
    admin_password: str,
    default_client_uuid: str,
    default_client_id: str,
    default_client_secret: str,
):
    return iam_clients.GenesisCoreAuth(
        username=admin_username,
        password=admin_password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
    )


@pytest.fixture()
def auth_test1_user(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
    default_client_uuid: str,
    default_client_id: str,
    default_client_secret: str,
):
    password = "test1test"
    client = user_api_client(
        auth_user_admin,
        permissions=[
            iam_c.PERMISSION_USER_CREATE,
        ],
    )
    user = client.create_user(username="test1", password=password)
    user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
    client.confirm_email(
        user_uuid=user_obj.uuid,
        code=str(user_obj.confirmation_code),
    )

    return iam_clients.GenesisCoreAuth(
        username=user["username"],
        password=password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
        uuid=user["uuid"],
        email=user["email"],
    )


@pytest.fixture()
def auth_test2_user(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
    default_client_uuid: str,
    default_client_id: str,
    default_client_secret: str,
):
    password = "test2test"
    client = user_api_client(
        auth_user_admin,
        permissions=[
            iam_c.PERMISSION_USER_CREATE,
        ],
    )
    user = client.create_user(username="test2", password=password)
    user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
    client.confirm_email(
        user_uuid=user_obj.uuid,
        code=str(user_obj.confirmation_code),
    )

    return iam_clients.GenesisCoreAuth(
        username=user["username"],
        password=password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
        uuid=user["uuid"],
        email=user["email"],
    )


@pytest.fixture()
def auth_test1_p1_user(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
    default_client_uuid: str,
    default_client_id: str,
    default_client_secret: str,
):
    password = "testtest1p1"
    client = user_api_client(
        auth_user_admin,
        permissions=[
            iam_c.PERMISSION_USER_CREATE,
        ],
    )
    user = client.create_user(username="test1p1", password=password)
    user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
    client.confirm_email(
        user_uuid=user_obj.uuid,
        code=str(user_obj.confirmation_code),
    )

    auth = iam_clients.GenesisCoreAuth(
        username=user["username"],
        password=password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
        uuid=user["uuid"],
        email=user["email"],
        project_id=None,
    )

    client = user_api_client(auth)

    org = client.create_organization(name="OrganizationU1P1")
    project = client.create_project(
        uuid=str(sys_uuid.uuid4()),
        organization_uuid=org["uuid"],
        name="ProjectU1P1",
    )

    return iam_clients.GenesisCoreAuth(
        username=user["username"],
        password=password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
        uuid=user["uuid"],
        email=user["email"],
        project_id=project["uuid"],
    )


@pytest.fixture()
def auth_test2_p1_user(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
    default_client_uuid: str,
    default_client_id: str,
    default_client_secret: str,
):
    password = "testtest2p1"
    client = user_api_client(auth_user_admin)
    user = client.create_user(username="test2p1", password=password)
    user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
    client.confirm_email(
        user_uuid=user_obj.uuid,
        code=str(user_obj.confirmation_code),
    )

    auth = iam_clients.GenesisCoreAuth(
        username=user["username"],
        password=password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
        uuid=user["uuid"],
        email=user["email"],
        project_id=None,
    )

    client = user_api_client(auth)

    org = client.create_organization(name="OrganizationU2P1")
    project = client.create_project(
        uuid=str(sys_uuid.uuid4()),
        organization_uuid=org["uuid"],
        name="ProjectU2P1",
    )

    return iam_clients.GenesisCoreAuth(
        username=user["username"],
        password=password,
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
        uuid=user["uuid"],
        email=user["email"],
        project_id=project["uuid"],
    )


@pytest.fixture()
def user_api_client(user_api, auth_user_admin):

    def build_client(
        auth: iam_clients.GenesisCoreAuth,
        permissions: tp.Optional[tp.List[str]] = None,
        project_id: tp.Optional[str] = None,
    ):
        permissions = permissions or []
        client = iam_clients.GenericAutoRefreshRESTClient(
            f"{user_api.get_endpoint()}v1/",
            auth,
        )
        admin_client = iam_clients.GenericAutoRefreshRESTClient(
            f"{user_api.get_endpoint()}v1/",
            auth_user_admin,
        )

        admin_client.set_permissions_to_user(
            user_uuid=auth.uuid,
            permissions=permissions,
            project_id=project_id,
        )

        return client

    return build_client


@pytest.fixture()
def user_api_noauth_client(user_api):
    return lambda: iam_clients.GenesisCoreTestNoAuthRESTClient(
        f"{user_api.get_endpoint()}v1/"
    )


@pytest.fixture
def node_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "node",
        cores: int = 1,
        ram: int = 1024,
        image: str = "ubuntu_24.04",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: tp.Optional[str] = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        status_value = nc.NodeStatus.NEW.value if status is None else status.value
        node = node_models.Node(
            uuid=uuid,
            name=name,
            cores=cores,
            ram=ram,
            project_id=project_id,
            status=status_value,
            disk_spec=sdk_infra_models.RootDiskSpec(image=image),
            **kwargs,
        )
        view = node.dump_to_simple_view()
        if status is None:
            view.pop("status")
        view.pop("node_set")
        return view

    return factory


@pytest.fixture
def node_set_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "node_set",
        cores: int = 1,
        ram: int = 1024,
        image: str = "ubuntu_24.04",
        replicas: int = 1,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: str = nc.NodeStatus.NEW.value,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        obj = node_set_models.NodeSet(
            uuid=uuid,
            name=name,
            cores=cores,
            ram=ram,
            image=image,
            replicas=replicas,
            project_id=project_id,
            status=status,
            disk_spec=sdk_infra_models.SetRootDiskSpec(image=image),
            **kwargs,
        )
        view = obj.dump_to_simple_view()
        return view

    return factory


@pytest.fixture
def pool_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        agent: tp.Optional[sys_uuid.UUID] = None,
        name: str = "pool-default",
        driver_spec: tp.Optional[dict] = None,
        status: tp.Optional[str] = None,
        avail_cores: int = 8,
        avail_ram: int = 16384,
        all_cores: int = 8,
        all_ram: int = 16384,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        driver_spec = {"driver": "libvirt"} if driver_spec is None else driver_spec
        status_value = nc.MachinePoolStatus.ACTIVE.value if status is None else status
        storage_pool = node_models.ThinStoragePool(
            pool_type="dummy",
            capacity_usable=1000,
            capacity_provisioned=0,
            oversubscription_ratio=1.0,
            available_actual=1000,
        )

        pool = node_models.MachinePool(
            uuid=uuid,
            agent=agent,
            name=name,
            status=status_value,
            driver_spec=driver_spec,
            avail_cores=avail_cores,
            avail_ram=avail_ram,
            all_cores=all_cores,
            all_ram=all_ram,
            storage_pools=[storage_pool],
            **kwargs,
        )
        view = pool.dump_to_simple_view()
        if status is None:
            view.pop("status")
        return view

    return factory


@pytest.fixture
def machine_factory(default_pool: tp.Dict[str, tp.Any]):
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        pool: tp.Optional[sys_uuid.UUID] = None,
        name: str = "node",
        cores: int = 1,
        ram: int = 1024,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: str = nc.MachineStatus.ACTIVE.value,
        build_status: str = nc.MachineBuildStatus.READY.value,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        pool = pool or sys_uuid.UUID(default_pool["uuid"])
        machine = node_models.Machine(
            uuid=uuid,
            pool=pool,
            name=name,
            cores=cores,
            ram=ram,
            project_id=project_id,
            status=status,
            build_status=build_status,
            **kwargs,
        )
        view = machine.dump_to_simple_view()
        return view

    return factory


@pytest.fixture
def volume_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "volume-default",
        size: int = 10,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        volume = node_models.Volume(
            uuid=uuid,
            name=name,
            size=size,
            project_id=project_id,
            **kwargs,
        )
        view = volume.dump_to_simple_view(
            skip=[
                "label",
                "image",
                "node",
                "index",
                "status",
                "pool",
            ]
        )
        return view

    return factory


@pytest.fixture
def config_factory():
    def factory(
        target_node: sys_uuid.UUID,
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "config",
        path: str = "/etc/genesis-configs/config.conf",
        content_body: str = "test",
        on_change_cmd: tp.Optional[str] = None,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: str = cc.ConfigStatus.NEW.value,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        target = ct.NodeTarget.from_node(target_node)
        body = conf_models.TextBodyConfig.from_text(content_body)
        if on_change_cmd is None:
            on_change = conf_models.OnChangeNoAction()
        else:
            on_change = conf_models.OnChangeShell.from_command(on_change_cmd)

        config = conf_models.Config(
            uuid=uuid,
            name=name,
            path=path,
            target=target,
            body=body,
            on_change=on_change,
            project_id=project_id,
            status=status,
            **kwargs,
        )
        view = config.dump_to_simple_view()
        return view

    return factory


@pytest.fixture
def password_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "password",
        constructor: tp.Optional[secret_models.AbstractSecretConstructor] = None,
        method: sc.SecretMethod = sc.SecretMethod.AUTO_HEX,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: tp.Optional[cc.ConfigStatus] = None,
        value: tp.Optional[str] = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        constructor = (
            secret_models.PlainSecretConstructor()
            if constructor is None
            else constructor
        )
        status_value = cc.ConfigStatus.NEW.value if status is None else status.value
        obj = secret_models.Password(
            uuid=uuid,
            name=name,
            method=method.value,
            project_id=project_id,
            status=status_value,
            constructor=constructor,
            **kwargs,
        )
        view = obj.dump_to_simple_view()
        if status is None:
            view.pop("status")
        if value is None:
            view.pop("value")
        return view

    return factory


@pytest.fixture
def cert_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "cert",
        domains: tp.Collection[str] = ("genesis-core.tech",),
        email: str = "user@genesis-core.tech",
        key: tp.Optional[str] = None,
        cert: tp.Optional[str] = None,
        constructor: tp.Optional[secret_models.AbstractSecretConstructor] = None,
        method: tp.Optional[secret_models.AbstractCertificateMethod] = None,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: tp.Optional[cc.ConfigStatus] = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        constructor = (
            secret_models.PlainSecretConstructor()
            if constructor is None
            else constructor
        )
        method = secret_models.DNSCoreCertificateMethod() if method is None else method
        status_value = cc.ConfigStatus.NEW.value if status is None else status.value
        obj = secret_models.Certificate(
            uuid=uuid,
            name=name,
            method=method,
            project_id=project_id,
            status=status_value,
            constructor=constructor,
            domains=list(domains),
            email=email,
            key=key,
            cert=cert,
            **kwargs,
        )
        view = obj.dump_to_simple_view()
        if status is None:
            view.pop("status")
        if key is None:
            view.pop("key")
        if cert is None:
            view.pop("cert")
        view.pop("expiration_threshold")
        view.pop("overcome_threshold")

        return view

    return factory


@pytest.fixture
def ssh_key_factory():
    def factory(
        target_node: sys_uuid.UUID,
        target_public_key: str,
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "key",
        constructor: tp.Optional[secret_models.AbstractSecretConstructor] = None,
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        status: tp.Optional[cc.ConfigStatus] = None,
        user: str = "root",
        authorized_keys=".ssh/authorized_keys",
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        target = ct.NodeTarget.from_node(target_node)
        constructor = (
            secret_models.PlainSecretConstructor()
            if constructor is None
            else constructor
        )
        status_value = cc.ConfigStatus.NEW.value if status is None else status.value
        obj = secret_models.SSHKey(
            uuid=uuid,
            name=name,
            project_id=project_id,
            status=status_value,
            constructor=constructor,
            target=target,
            user=user,
            authorized_keys=authorized_keys,
            target_public_key=target_public_key,
            **kwargs,
        )
        view = obj.dump_to_simple_view()
        if status is None:
            view.pop("status")

        return view

    return factory


@pytest.fixture
def pool_builder_factory() -> tp.Callable:
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        status: str = nc.BuilderStatus.ACTIVE.value,
        **kwargs,
    ) -> sdk_ua_models.UniversalAgent:
        uuid = uuid or sys_uuid.uuid4()
        agent = sdk_ua_models.UniversalAgent(
            uuid=uuid,
            capabilities={
                "capabilities": [
                    "builder_pool",
                    "builder_pool_machine",
                    "builder_pool_volume",
                ]
            },
            facts={"facts": []},
            name=f"compute_pool_builder_{str(uuid)[:8]}",
            node=sys_uuid.uuid4(),
            status=status,
            **kwargs,
        )

        return agent

    return factory


@pytest.fixture
def interface_factory() -> tp.Callable:
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        mac: tp.Optional[str] = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        interface = node_models.Interface(
            uuid=uuid,
            mac=mac or node_models.Port.generate_mac(),
            **kwargs,
        )
        view = interface.dump_to_simple_view()
        return view

    return factory


@pytest.fixture
def machine_pool_reservation_factory() -> tp.Callable:
    def factory(
        pool: sys_uuid.UUID,
        uuid: tp.Optional[sys_uuid.UUID] = None,
        machine: tp.Optional[sys_uuid.UUID] = None,
        cores: int = 1,
        ram: int = 1024,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        reservation = node_models.MachinePoolReservations(
            uuid=uuid,
            pool=pool,
            machine=machine,
            cores=cores,
            ram=ram,
            **kwargs,
        )
        view = reservation.dump_to_simple_view()
        return view

    return factory


@pytest.fixture
def lb_factory():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "load-balancer-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        lb = network_models.LB(
            uuid=uuid,
            name=name,
            project_id=project_id,
            **kwargs,
        )
        view = lb.dump_to_simple_view(
            skip=[
                "status",
                "ipsv4",
            ]
        )
        return view

    return factory


@pytest.fixture
def lb_factory_with_model():
    def factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "load-balancer-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        **kwargs,
    ) -> tp.Tuple[tp.Dict[str, tp.Any], network_models.LB]:
        uuid = uuid or sys_uuid.uuid4()
        lb = network_models.LB(
            uuid=uuid,
            name=name,
            project_id=project_id,
            **kwargs,
        )
        view = lb.dump_to_simple_view(
            skip=[
                "status",
                "ipsv4",
            ]
        )
        return view, lb

    return factory


@pytest.fixture
def vhost_factory():
    def factory(
        lb,
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "vhost-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        enabled: bool = True,
        protocol: str = network_models.Protocol.HTTP,
        port: int = 80,
        domains: list[str] | None = None,
        cert: network_models.CertKind | None = None,
        external_sources: list[str] | None = None,
        proxy_protocol_from: list[str] | None = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        vhost = network_models.Vhost(
            parent=lb,
            uuid=uuid,
            name=name,
            project_id=project_id,
            enabled=enabled,
            protocol=protocol,
            port=port,
            domains=domains,
            cert=cert,
            external_sources=external_sources,
            proxy_protocol_from=proxy_protocol_from,
            **kwargs,
        )
        view = vhost.dump_to_simple_view(
            skip=[
                "parent",
                "status",
            ]
        )
        return view

    return factory


@pytest.fixture
def vhost_factory_with_model():
    def factory(
        lb,
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "vhost-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        enabled: bool = True,
        protocol: str = network_models.Protocol.HTTP,
        port: int = 80,
        domains: list[str] | None = None,
        cert: network_models.CertKind | None = None,
        external_sources: list[str] | None = None,
        proxy_protocol_from: list[str] | None = None,
        **kwargs,
    ) -> tp.Tuple[tp.Dict[str, tp.Any], network_models.Vhost]:
        uuid = uuid or sys_uuid.uuid4()
        vhost = network_models.Vhost(
            parent=lb,
            uuid=uuid,
            name=name,
            project_id=project_id,
            enabled=enabled,
            protocol=protocol,
            port=port,
            domains=domains,
            cert=cert,
            external_sources=external_sources,
            proxy_protocol_from=proxy_protocol_from,
            **kwargs,
        )
        view = vhost.dump_to_simple_view(
            skip=[
                "parent",
                "status",
            ]
        )
        return view, vhost

    return factory


@pytest.fixture
def backend_pool_factory():
    def factory(
        lb,
        endpoints: list[network_models.BackendHostKind],
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "backend-pool-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        balance: str = network_models.BalanceTypes.RR.value,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        backend_pool = network_models.BackendPool(
            parent=lb,
            uuid=uuid,
            name=name,
            project_id=project_id,
            balance=balance,
            endpoints=endpoints,
            **kwargs,
        )
        view = backend_pool.dump_to_simple_view(
            skip=[
                "parent",
                "status",
            ]
        )
        return view

    return factory


@pytest.fixture
def backend_pool_factory_with_model():
    def factory(
        lb,
        endpoints: list[network_models.BackendHostKind],
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "backend-pool-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        balance: str = network_models.BalanceTypes.RR.value,
        **kwargs,
    ) -> tp.Tuple[tp.Dict[str, tp.Any], network_models.BackendPool]:
        uuid = uuid or sys_uuid.uuid4()
        backend_pool = network_models.BackendPool(
            parent=lb,
            uuid=uuid,
            name=name,
            project_id=project_id,
            balance=balance,
            endpoints=endpoints,
            **kwargs,
        )
        view = backend_pool.dump_to_simple_view(
            skip=[
                "parent",
                "status",
            ]
        )
        return view, backend_pool

    return factory


@pytest.fixture
def route_factory():
    def factory(
        vhost,
        condition: network_models.AbstractHTTPRouteCondKind,
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: str = "route-default",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        enabled: bool = True,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        route = network_models.Route(
            parent=vhost,
            uuid=uuid,
            name=name,
            project_id=project_id,
            enabled=enabled,
            condition=condition,
            **kwargs,
        )
        view = route.dump_to_simple_view(
            skip=[
                "parent",
                "status",
            ]
        )
        return view

    return factory


@pytest.fixture()
def default_pool(
    pool_factory: tp.Callable,
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
):
    uuid = sys_uuid.UUID("00000000-1111-0000-0000-000000000000")
    default_pool = pool_factory(uuid=uuid)
    client = user_api_client(auth_user_admin)
    url = client.build_collection_uri(["compute", "hypervisors"])
    client.post(url, json=default_pool)

    pool = node_models.MachinePool.objects.get_one(
        filters={"uuid": dm_filters.EQ(uuid)}
    )
    pool.status = "ACTIVE"
    pool.save()

    return default_pool


@pytest.fixture
def default_node(
    node_factory: tp.Callable,
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
):
    uuid = sys_uuid.UUID("00000000-1112-0000-0000-000000000000")
    default_node = node_factory(uuid=uuid)
    client = user_api_client(auth_user_admin)
    url = client.build_collection_uri(["compute", "nodes"])
    client.post(url, json=default_node)

    yield default_node

    url = client.build_resource_uri(["compute", "nodes", uuid])
    client.delete(url)


@pytest.fixture
def default_network(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
) -> node_models.Network:
    uuid = sys_uuid.UUID("00000000-1112-0100-0000-000000000000")
    network = node_models.Network(
        uuid=uuid,
        driver_spec={"driver": "dummy"},
        project_id=c.SERVICE_PROJECT_ID,
    )
    network.insert()

    return network


@pytest.fixture
def default_subnet(
    default_network: node_models.Network,
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
) -> node_models.Subnet:
    uuid = sys_uuid.UUID("00000000-1112-0130-0000-000000000000")
    subnet = node_models.Subnet(
        uuid=uuid,
        network=default_network.uuid,
        cidr=netaddr.IPNetwork("10.0.0.0/24"),
        project_id=c.SERVICE_PROJECT_ID,
    )
    subnet.insert()

    return subnet


@pytest.fixture
def default_machine_agent(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
) -> tp.Dict[str, tp.Any]:
    uuid = sys_uuid.UUID("00000000-1112-0100-0000-000000000211")
    agent = sdk_ua_models.UniversalAgent(
        uuid=uuid,
        capabilities={"capabilities": ["pool", "pool_volume", "pool_machine"]},
        facts={"facts": []},
        name="machine_agent",
        node=sys_uuid.UUID("00000000-1112-1100-0000-000000000000"),
        status="ACTIVE",
    )
    agent.insert()

    return agent.dump_to_simple_view()


@pytest.fixture
def default_pool_builder(
    user_api_client: iam_clients.GenesisCoreTestRESTClient,
    auth_user_admin: iam_clients.GenesisCoreAuth,
) -> tp.Dict[str, tp.Any]:
    uuid = sys_uuid.UUID("00000000-1112-0100-0000-000000000322")
    agent = sdk_ua_models.UniversalAgent(
        uuid=uuid,
        capabilities={
            "capabilities": [
                "builder_pool",
                "builder_pool_machine",
                "builder_pool_volume",
            ]
        },
        facts={"facts": []},
        name="compute_pool_builder_default",
        node=sys_uuid.UUID("00000000-1112-1100-0000-000000000000"),
        status="ACTIVE",
    )
    agent.insert()

    return agent.dump_to_simple_view()


@pytest.fixture(scope="session", autouse=True)
def setup_db_for_worker(worker_id):
    db_uri = consts.get_database_uri()
    if not worker_id:
        yield
        return

    parsed = urlparse(db_uri)
    worker_db_name_with_slash = f"{parsed.path}_{worker_id}"
    worker_db_name = worker_db_name_with_slash.strip("/")
    db_type = parsed.scheme
    db_created = False
    engines.engine_factory.configure_factory(db_url=db_uri)
    engine = engines.engine_factory.get_engine()
    conn = engine.get_connection()

    # Check if database exists
    if db_type == "postgresql":
        conn.autocommit = True
        c = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (worker_db_name,)
        )
        exists = c.fetchall()
    elif db_type == "mysql":
        c = conn.cursor()
        c.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
            (worker_db_name,),
        )
        exists = c.fetchall()
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    if not exists:
        db_created = True
        if db_type == "postgresql":
            conn.autocommit = True
            conn.execute(f"CREATE DATABASE {worker_db_name}")
        elif db_type == "mysql":
            conn.cursor().execute(f"CREATE DATABASE {worker_db_name}")

    engine.close_connection(conn)
    del engine
    engines.engine_factory.destroy_engine()

    os.environ["DATABASE_URI"] = parsed._replace(
        path=worker_db_name_with_slash
    ).geturl()
    yield

    if db_created:
        engines.engine_factory.configure_factory(db_url=db_uri)
        engine = engines.engine_factory.get_engine()
        conn = engine.get_connection()
        if db_type == "postgresql":
            conn.autocommit = True
            conn.execute(f"DROP DATABASE IF EXISTS {worker_db_name} WITH (FORCE)")
        elif db_type == "mysql":
            conn.cursor().execute(f"DROP DATABASE IF EXISTS {worker_db_name}")

        engine.close_connection(conn)
        del engine
        engines.engine_factory.destroy_engine()


@pytest.fixture(scope="session")
def base_manifest_schema():
    return element_utils.load_base_manifest_schema()


@pytest.fixture(scope="session")
def full_manifest_schema():
    return element_utils.load_full_manifest_schema()


@pytest.fixture(scope="session")
def user_api_spec():
    return element_utils.load_user_api_spec()


@pytest.fixture(scope="session")
def inventory():
    return bazooka.get(c.INVENTORY_URL).json()


@pytest.fixture
def self_signed_cert():
    """Generates self signed certificate for a hostname, and optional IP addresses."""

    def factory(hostname, ip_addresses=None, key=None):
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone
        import ipaddress

        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        # Generate our key
        if key is None:
            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend(),
            )

        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])

        # best practice seem to be to include the hostname in the SAN, which *SHOULD* mean COMMON_NAME is ignored.
        alt_names = [x509.DNSName(hostname)]

        # allow addressing by IP, for when you don't have real DNS (common in most testing scenarios
        if ip_addresses:
            for addr in ip_addresses:
                # openssl wants DNSnames for ips...
                alt_names.append(x509.DNSName(addr))
                # ... whereas golang's crypto/tls is stricter, and needs IPAddresses
                # note: older versions of cryptography do not understand ip_address objects
                alt_names.append(x509.IPAddress(ipaddress.ip_address(addr)))

        san = x509.SubjectAlternativeName(alt_names)

        # path_len=0 means this cert can only sign itself, not other certs.
        basic_contraints = x509.BasicConstraints(ca=True, path_length=0)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1000)
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=10 * 365))
            .add_extension(basic_contraints, False)
            .add_extension(san, False)
            .sign(key, hashes.SHA256(), default_backend())
        )
        cert_pem = cert.public_bytes(encoding=serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )

        return cert_pem.decode(), key_pem.decode()

    return factory
