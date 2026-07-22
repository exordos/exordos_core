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

import typing as tp
from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra.dm import models as sdk_models
import netaddr
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.compute.dm import models
from exordos_core.network import service
from exordos_core.network.driver import base as driver_base


def fake_load_driver(
    *args: tp.Any, **kwargs: tp.Any
) -> driver_base.AbstractNetworkDriver:
    return TestNetworkService.network_driver


@mock.patch.object(
    models.Network,
    "load_driver",
    fake_load_driver,
)
class TestNetworkService:
    network_driver = None

    def setup_method(self) -> None:
        # Run service
        self._service = service.NetworkService()
        self.__class__.network_driver = mock.MagicMock()

    def teardown_method(self) -> None:
        self.__class__.network_driver = None

    def _save_network_driver(self, driver: driver_base.AbstractNetworkDriver):
        self.__class__.network_driver = driver

    def _get_network_driver(self) -> driver_base.AbstractNetworkDriver:
        return self.__class__.network_driver

    def _schedule_pool(self, pool_uuid: str, agent_uuid: str) -> models.MachinePool:
        pool = models.MachinePool.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(pool_uuid),
            },
        )
        pool.agent = sys_uuid.UUID(agent_uuid)
        pool.update()
        return pool

    def _add_node(self, **kwargs) -> models.Node:
        node_uuid = sys_uuid.uuid4()
        node = models.Node(
            uuid=node_uuid,
            name="foo-node",
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(
                image="ubuntu_24.04",
            ),
            project_id=c.SERVICE_PROJECT_ID,
            **kwargs,
        )
        node.insert()
        return node

    def _add_network(self, **kwargs) -> tp.Tuple[models.Network, models.Subnet]:
        network = models.Network(
            name="foo-network",
            driver_spec={"driver": "dummy"},
            project_id=c.SERVICE_PROJECT_ID,
        )
        network.insert()

        subnet = models.Subnet(
            network=network.uuid,
            cidr=netaddr.IPNetwork("10.0.0.0/24"),
            project_id=c.SERVICE_PROJECT_ID,
            **kwargs,
        )
        subnet.insert()
        return network, subnet

    def _add_port(
        self,
        subnet: models.Subnet,
        node: tp.Optional[models.Node] = None,
        ipv4: str = netaddr.IPAddress("10.0.0.0"),
        mask: str = netaddr.IPAddress("255.255.255.0"),
        mac: tp.Optional[str] = None,
        save: bool = True,
    ) -> tp.Tuple[models.Port]:
        port = subnet.port(
            ipv4=ipv4,
            mask=mask,
            mac=mac or models.Port.generate_mac(),
            project_id=subnet.project_id,
            node_uuid=node.uuid if node else None,
        )
        if save:
            port.insert()

        return port

    def _schedule_node(
        self, node_uuid: str, machine: tp.Union[models.Machine, str]
    ) -> models.Node:
        node = models.Node.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(node_uuid),
            },
        )
        if isinstance(machine, str):
            machine = models.Machine.objects.get_one(
                filters={
                    "uuid": dm_filters.EQ(machine),
                },
            )
        machine.node = sys_uuid.UUID(node_uuid)
        machine.update()
        return node

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_empty_iteration(self):
        self._service._iteration()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_node_no_networks(self):
        node = self._add_node()

        self._service._iteration()

        node.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_node_add_subnet(self):
        node = self._add_node()
        network, subnet = self._add_network()

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False

            def __init__(self):
                pass

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True
                assert subnet.cidr == netaddr.IPNetwork("10.0.0.0/24")
                return subnet

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert FakeDriver.create_subnet_called

        [
            port.delete()
            for port in models.Port.objects.get_all(
                filters={
                    "subnet": dm_filters.EQ(subnet.uuid),
                },
            )
        ]
        subnet.delete()
        network.delete()
        node.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_node_add_port(self):
        node = self._add_node()
        network, subnet = self._add_network()
        port_uuid = None

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def create_port(self, port: models.Port) -> models.Port:
                nonlocal port_uuid

                self.__class__.create_port_called = True
                assert port.subnet == subnet.uuid
                assert port.ipv4 == netaddr.IPAddress("10.0.0.0")
                assert port.mask == netaddr.IPAddress("255.255.255.0")
                port_uuid = port.uuid
                return port

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert not FakeDriver.create_subnet_called
        assert FakeDriver.create_port_called

        # Check default network
        node: models.Node = models.Node.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(str(node.uuid)),
            },
        )
        assert node.default_network["ipv4"] == "10.0.0.0"
        assert node.default_network["mask"] == "255.255.255.0"
        assert node.default_network["mac"]
        assert node.default_network["subnet"] == str(subnet.uuid)
        assert node.default_network["port"] == str(port_uuid)

        port_obj = models.Port.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(port_uuid),
            },
        )
        port_obj.delete()
        subnet.delete()
        network.delete()
        node.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_update_port_status(self):
        node = self._add_node()
        network, subnet = self._add_network()
        port = self._add_port(subnet, node)

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def list_ports(self, subnet: models.Subnet) -> tp.Iterable[models.Port]:
                port.status = ua_pool.PortStatus.ACTIVE.value
                return [port]

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        updated_port = models.Port.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(port.uuid),
            },
        )

        assert not FakeDriver.create_subnet_called
        assert not FakeDriver.create_port_called
        assert updated_port.status == ua_pool.PortStatus.ACTIVE

        port.delete()
        subnet.delete()
        network.delete()
        node.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_delete_port(self):
        network, subnet = self._add_network()
        port = self._add_port(subnet, save=False)

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False
            delete_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def list_ports(self, subnet: models.Subnet) -> tp.Iterable[models.Port]:
                port.status = ua_pool.PortStatus.ACTIVE.value
                return [port]

            def delete_port(self, port: models.Port) -> None:
                self.__class__.delete_port_called = True
                assert port.ipv4 == netaddr.IPAddress("10.0.0.0")

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert not FakeDriver.create_subnet_called
        assert not FakeDriver.create_port_called
        assert FakeDriver.delete_port_called

        subnet.delete()
        network.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_node_add_port_target_ip(self):
        extra = {"default_network": {"target_ipv4": "10.0.0.10"}}
        node = self._add_node(**extra)
        network, subnet = self._add_network()
        port_uuid = None

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def create_port(self, port: models.Port) -> models.Port:
                nonlocal port_uuid

                self.__class__.create_port_called = True
                assert port.subnet == subnet.uuid
                assert port.ipv4 == netaddr.IPAddress("10.0.0.10")
                assert port.mask == netaddr.IPAddress("255.255.255.0")
                port_uuid = port.uuid
                return port

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert not FakeDriver.create_subnet_called
        assert FakeDriver.create_port_called

        # Check target ip
        node: models.Node = models.Node.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(str(node.uuid)),
            },
        )
        assert node.default_network["ipv4"] == "10.0.0.10"
        assert node.default_network["target_ipv4"] == "10.0.0.10"
        assert node.default_network["mask"] == "255.255.255.0"
        assert node.default_network["mac"]
        assert node.default_network["subnet"] == str(subnet.uuid)
        assert node.default_network["port"] == str(port_uuid)

        port_obj = models.Port.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(port_uuid),
            },
        )
        port_obj.delete()
        subnet.delete()
        network.delete()
        node.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_node_add_ip_range(self):
        node = self._add_node()

        extra = {"ip_range": netaddr.IPRange("10.0.0.100", "10.0.0.200")}
        network, subnet = self._add_network(**extra)
        port_uuid = None

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def create_port(self, port: models.Port) -> models.Port:
                nonlocal port_uuid

                self.__class__.create_port_called = True
                assert port.subnet == subnet.uuid
                assert port.ipv4 == netaddr.IPAddress("10.0.0.100")
                assert port.mask == netaddr.IPAddress("255.255.255.0")
                port_uuid = port.uuid
                return port

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert not FakeDriver.create_subnet_called
        assert FakeDriver.create_port_called

        # Check target ip
        node: models.Node = models.Node.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(str(node.uuid)),
            },
        )
        assert node.default_network["ipv4"] == "10.0.0.100"
        assert node.default_network["mask"] == "255.255.255.0"
        assert node.default_network["mac"]
        assert node.default_network["subnet"] == str(subnet.uuid)
        assert node.default_network["port"] == str(port_uuid)

        port_obj = models.Port.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(port_uuid),
            },
        )
        port_obj.delete()
        subnet.delete()
        network.delete()
        node.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_hw_node_without_machine(self):
        extra = {"ip_range": netaddr.IPRange("10.0.0.100", "10.0.0.200")}
        network, subnet = self._add_network(**extra)
        port_uuid = None

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def create_port(self, port: models.Port) -> models.Port:
                nonlocal port_uuid
                self.__class__.create_port_called = True

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert not FakeDriver.create_subnet_called
        assert not FakeDriver.create_port_called

        subnet.delete()
        network.delete()

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_new_hw_node(
        self,
        machine_factory: tp.Callable,
        pool_factory: tp.Callable,
        node_factory: tp.Callable,
        interface_factory: tp.Callable,
    ):
        # HW machine pool
        hw_pool = pool_factory(machine_type="HW")
        hw_pool = models.MachinePool.restore_from_simple_view(**hw_pool)
        hw_pool.insert()

        # HW machines
        hw_machine = machine_factory(
            machine_type="HW",
            pool=hw_pool.uuid,
            status="IDLE",
            cores=4,
            ram=2048,
        )
        hw_machine = models.Machine.restore_from_simple_view(**hw_machine)
        hw_machine.insert()

        # Interface
        hw_interface = interface_factory(
            machine=hw_machine.uuid,
            name="eth0",
            ipv4=netaddr.IPAddress("10.0.0.253"),
            mask=netaddr.IPAddress("255.255.255.0"),
        )
        hw_interface = models.Interface.restore_from_simple_view(**hw_interface)
        hw_interface.insert()

        # HW node
        node = node_factory(node_type="HW", cores=1, ram=1024)
        node = models.Node.restore_from_simple_view(**node)
        node.insert()

        # Schedule machine to the node
        hw_machine.node = node.uuid
        hw_machine.update()

        extra = {
            "ip_range": netaddr.IPRange("10.0.0.100", "10.0.0.200"),
            "ip_discovery_range": netaddr.IPRange("10.0.0.201", "10.0.0.254"),
        }
        network, subnet = self._add_network(**extra)
        port_uuid = None

        class FakeDriver(driver_base.DummyNetworkDriver):
            create_subnet_called = False
            create_port_called = False

            def __init__(self):
                pass

            def list_subnets(self) -> tp.Iterable[models.Subnet]:
                return [subnet]

            def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
                self.__class__.create_subnet_called = True

            def create_port(self, port: models.Port) -> models.Port:
                nonlocal port_uuid

                self.__class__.create_port_called = True
                assert port.subnet == subnet.uuid
                assert port.ipv4 == netaddr.IPAddress("10.0.0.100")
                assert port.mask == netaddr.IPAddress("255.255.255.0")
                port_uuid = port.uuid
                return port

        self._save_network_driver(FakeDriver())

        self._service._iteration()

        assert not FakeDriver.create_subnet_called
        assert FakeDriver.create_port_called

        # Check target ip
        node: models.Node = models.Node.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(str(node.uuid)),
            },
        )
        assert node.default_network["ipv4"] == "10.0.0.100"
        assert node.default_network["mask"] == "255.255.255.0"
        assert node.default_network["mac"] == hw_interface.mac
        assert node.default_network["subnet"] == str(subnet.uuid)
        assert node.default_network["port"] == str(port_uuid)

        port_obj = models.Port.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(port_uuid),
            },
        )
        port_obj.delete()
        subnet.delete()
        network.delete()
        hw_interface.delete()
        hw_machine.delete()
        hw_pool.delete()
        node.delete()
