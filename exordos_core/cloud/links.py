# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import contextlib
import ipaddress
import logging
import uuid

from gcl_looper.services import basic
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.cloud import bootstrap
from gcl_sdk.cloud import openvpn
import netaddr
from restalchemy.dm import filters
from restalchemy.dm import models as dm
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage import exceptions
from restalchemy.storage.sql import orm

from exordos_core.cloud import local
from exordos_core.cloud import models
from exordos_core.cloud.locking import provider_lock
from exordos_core.common.dm.models import ModelWithFullAsset
from exordos_core.common.exceptions import ValidateException
from exordos_core.compute.dm import models as compute
from exordos_core.network.contracts import atomic_session
from exordos_core.user_api.network.dm import models as catalog

LOG = logging.getLogger(__name__)
KIND = "cloud_net_link"


class CloudLinkEndpoint(dm.ModelWithUUID, orm.SQLStorableWithJSONFieldsMixin):
    """Retain subnet ownership until provider and peer cleanup has finished."""

    __tablename__ = "cloud_link_endpoints"
    __jsonfields__ = ["definition"]
    link = properties.property(types.UUID(), required=True)
    subnet = properties.property(types.UUID(), required=True)
    connection = properties.property(types.AllowNone(types.UUID()), default=None)
    definition = properties.property(types.Dict(), required=True)


def endpoints_for_subnet(subnet, session=None):
    return CloudLinkEndpoint.objects.get_all(
        filters={"subnet": filters.EQ(subnet)}, session=session
    )


class CloudNetworkLink(
    ModelWithFullAsset, orm.SQLStorableWithJSONFieldsMixin, ua.ResourceMixin
):
    __tablename__ = "cloud_network_links"
    __jsonfields__ = ["endpoints", "transport", "secrets", "state", "recovery"]
    endpoints = properties.property(types.TypedList(types.Dict()), required=True)
    transport = properties.property(
        types.Dict(),
        default=lambda: {"driver": "openvpn", "protocol": "tcp", "port": 443},
    )
    mtu = properties.property(
        types.Integer(min_value=1280, max_value=1500), default=1380
    )
    secrets = properties.property(types.Dict(), default=dict)
    state = properties.property(types.Dict(), default=dict)
    recovery = properties.property(types.Dict(), default=dict)
    status = properties.property(
        types.Enum(["NEW", "IN_PROGRESS", "ACTIVE", "DELETING", "ERROR"]), default="NEW"
    )

    def _validate(self, session):
        transport = {
            "driver": "openvpn",
            "protocol": "tcp",
            "port": 443,
            **self.transport,
        }
        if (
            set(transport) != {"driver", "protocol", "port"}
            or transport["driver"] != "openvpn"
            or transport["protocol"] not in {"tcp", "udp"}
            or type(transport["port"]) is not int
            or not 1 <= transport["port"] <= 65535
        ):
            raise ValidateException(err="Unsupported cloud link transport")
        self.transport = transport
        if len(self.endpoints) < 2:
            raise ValidateException(err="A cloud link requires at least 2 endpoints")
        seen, local_nodes, prefixes, definitions = set(), set(), [], []
        allocated = [
            ipaddress.IPv4Network(e["tunnel_cidr"])
            for link in self.objects.get_all(session=session)
            for e in link.secrets.get("endpoints", {}).values()
        ]

        for endpoint in self.endpoints:
            try:
                subnet_id = uuid.UUID(endpoint["subnet"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValidateException(
                    err="An endpoint requires a subnet UUID"
                ) from error
            subnet = compute.Subnet.objects.get_one(
                filters={
                    "uuid": filters.EQ(subnet_id),
                    "project_id": filters.EQ(self.project_id),
                },
                session=session,
            )
            network = compute.Network.objects.get_one(
                filters={"uuid": filters.EQ(subnet.network)}, session=session
            )
            try:
                prefix = ipaddress.IPv4Network(str(subnet.cidr))
            except ValueError as error:
                raise ValidateException(
                    err="Cloud links require IPv4 subnets"
                ) from error
            if (
                subnet_id in seen
                or not prefix.prefixlen
                or any(prefix.overlaps(p) for p in prefixes + allocated)
            ):
                raise ValidateException(
                    err="Link endpoints must have distinct, non-overlapping IPv4 subnets"
                )
            seen.add(subnet_id)
            prefixes.append(prefix)
            config = dict(endpoint, subnet=str(subnet_id))
            if network.driver_spec.get("driver") == "cloud":
                try:
                    if set(config) - {
                        "subnet",
                        "gateway_image",
                        "external_network",
                        "gateway_cores",
                        "gateway_ram",
                    }:
                        raise ValueError("Unknown cloud endpoint option")
                    if (
                        not isinstance(config["gateway_image"], str)
                        or not config["gateway_image"].strip()
                    ):
                        raise ValueError("Gateway image is required")
                    config["external_network"] = str(
                        uuid.UUID(config["external_network"])
                    )
                    for field, default, minimum in [
                        ("gateway_cores", 1, 1),
                        ("gateway_ram", 1024, 512),
                    ]:
                        config.setdefault(field, default)
                        if type(config[field]) is not int or config[field] < minimum:
                            raise ValueError("Invalid gateway resources")
                except (KeyError, TypeError, ValueError) as error:
                    raise ValidateException(err=str(error)) from error
                connection = models.connection(
                    network.driver_spec["connection"], self.project_id, session
                )
                connection_id = connection.uuid
            elif "external_subnet" in config:
                try:
                    if set(config) - {
                        "subnet",
                        "external_subnet",
                        "gateway_image",
                        "gateway_cores",
                        "gateway_ram",
                        "gateway_disk",
                    }:
                        raise ValueError("Unknown local gateway option")
                    if network.driver_spec.get("driver") != "ovs_evpn":
                        raise ValueError("Managed local gateways require an SDN subnet")
                    if (
                        not isinstance(config["gateway_image"], str)
                        or not config["gateway_image"].strip()
                    ):
                        raise ValueError("Gateway image is required")
                    external = compute.Subnet.objects.get_one(
                        filters={
                            "uuid": filters.EQ(uuid.UUID(config["external_subnet"])),
                            "project_id": filters.EQ(self.project_id),
                        },
                        session=session,
                    )
                    external_network = compute.Network.objects.get_one(
                        filters={"uuid": filters.EQ(external.network)},
                        session=session,
                    )
                    if external_network.driver_spec.get("driver") != "flat_bridge":
                        raise ValueError("Local gateway external subnet must be flat")
                    if not external.placeable or external.next_server is not None:
                        raise ValueError("Gateway external subnet must be placeable")
                    if prefix.overlaps(ipaddress.IPv4Network(str(external.cidr))):
                        raise ValueError("Gateway private and external subnets overlap")
                    config["external_subnet"] = str(external.uuid)
                    for field, default, minimum in [
                        ("gateway_cores", 1, 1),
                        ("gateway_ram", 1024, 512),
                        ("gateway_disk", 8, 1),
                    ]:
                        config.setdefault(field, default)
                        if type(config[field]) is not int or config[field] < minimum:
                            raise ValueError("Invalid gateway resources")
                except (KeyError, TypeError, ValueError) as error:
                    raise ValidateException(err=str(error)) from error
                connection_id = None
            else:
                try:
                    if set(config) != {"subnet", "agent", "endpoint"}:
                        raise ValueError(
                            "A local endpoint requires subnet, agent and public endpoint"
                        )
                    config["agent"] = str(uuid.UUID(config["agent"]))
                    config["endpoint"] = str(ipaddress.IPv4Address(config["endpoint"]))
                    agent = ua.UniversalAgent.objects.get_one(
                        filters={"uuid": filters.EQ(config["agent"])}, session=session
                    )
                    if KIND not in agent.capabilities.get("capabilities", []):
                        raise ValueError(
                            "Local gateway agent must advertise cloud_net_link"
                        )
                    if agent.node in local_nodes:
                        raise ValueError(
                            "A local host can serve only one link endpoint"
                        )
                    local_nodes.add(agent.node)
                    for binding in CloudLinkEndpoint.objects.get_all(session=session):
                        if (
                            binding.connection is not None
                            or binding.subnet == subnet_id
                        ):
                            continue
                        if local.managed(binding):
                            other_node = binding.uuid
                        else:
                            other = ua.UniversalAgent.objects.get_one_or_none(
                                filters={
                                    "uuid": filters.EQ(binding.definition["agent"])
                                },
                                session=session,
                            )
                            other_node = other.node if other is not None else None
                        if other_node == agent.node:
                            raise ValueError(
                                "A local host already serves another link endpoint"
                            )
                except (TypeError, ValueError) as error:
                    raise ValidateException(err=str(error)) from error
                connection_id = None
            owned = endpoints_for_subnet(subnet.uuid, session)
            if any(e.link != self.uuid for e in owned):
                raise exceptions.ConflictRecords(
                    model="CloudNetworkLink", msg="Subnet belongs to another cloud link"
                )
            definitions.append((subnet_id, connection_id, config))
        return definitions

    def _sync_bindings(self, definitions, session):
        for subnet_id, connection, definition in definitions:
            existing = endpoints_for_subnet(subnet_id, session)
            if existing:
                if existing[0].definition != definition:
                    raise exceptions.ConflictRecords(
                        model="CloudNetworkLink",
                        msg="Change gateway placement by removing and reattaching the endpoint",
                    )
            else:
                CloudLinkEndpoint(
                    link=self.uuid,
                    subnet=subnet_id,
                    connection=connection,
                    definition=definition,
                ).insert(session=session)
        self.endpoints = [definition for _, _, definition in definitions]

    def insert(self, session=None):
        with atomic_session(self._get_engine(), session) as current:
            current.execute("SELECT pg_advisory_xact_lock(1129074520, 0)")
            definitions = self._validate(current)
            self.endpoints = [d for _, _, d in definitions]
            result = super().insert(session=current)
            self._sync_bindings(definitions, current)
            return result

    def update(self, session=None, force=False):
        with atomic_session(self._get_engine(), session) as current:
            current.execute("SELECT pg_advisory_xact_lock(1129074520, 0)")
            stored = self.objects.get_one(
                filters={"uuid": filters.EQ(self.uuid)}, session=current
            )
            if stored.status == "DELETING":
                raise exceptions.ConflictRecords(
                    model="CloudNetworkLink", msg="Link cleanup is in progress"
                )
            if (
                self.project_id != stored.project_id
                or self.transport != stored.transport
                or self.mtu != stored.mtu
            ):
                raise exceptions.ConflictRecords(
                    model="CloudNetworkLink",
                    msg="Link transport and project are immutable",
                )
            self._sync_bindings(self._validate(current), current)
            self.state, self.secrets, self.recovery = (
                stored.state,
                stored.secrets,
                stored.recovery,
            )
            self.status = (
                "IN_PROGRESS" if self.endpoints != stored.endpoints else stored.status
            )
            return super().update(session=current, force=force)

    def delete(self, session=None, **kwargs):
        with atomic_session(self._get_engine(), session) as current:
            current.execute("SELECT pg_advisory_xact_lock(1129074520, 0)")
            stored = self.objects.get_one(
                filters={"uuid": filters.EQ(self.uuid)}, session=current
            )
            stored.status = "DELETING"
            orm.SQLStorableMixin.update(stored, session=current)

    def request_recovery(self, subnet):
        with atomic_session(self._get_engine(), None) as current:
            current.execute("SELECT pg_advisory_xact_lock(1129074520, 0)")
            stored = self.objects.get_one(
                filters={"uuid": filters.EQ(self.uuid)}, session=current
            )
            if stored.status == "DELETING":
                raise exceptions.ConflictRecords(
                    model="CloudNetworkLink", msg="Link cleanup is in progress"
                )
            endpoint = CloudLinkEndpoint.objects.get_one(
                filters={"link": filters.EQ(self.uuid), "subnet": filters.EQ(subnet)},
                session=current,
            )
            server = stored.state.get(str(endpoint.uuid), {}).get("server_id")
            if endpoint.connection is None or not server:
                raise ValidateException(
                    err="Only a provisioned cloud gateway can be recovered"
                )
            stored.recovery = {**stored.recovery, str(endpoint.uuid): server}
            stored.status = "IN_PROGRESS"
            orm.SQLStorableMixin.update(stored, session=current)
            return stored

    def complete_recovery(self, endpoint, old_server):
        with atomic_session(self._get_engine(), None) as current:
            current.execute("SELECT pg_advisory_xact_lock(1129074520, 0)")
            stored = self.objects.get_one(
                filters={"uuid": filters.EQ(self.uuid)}, session=current
            )
            if stored.recovery.get(str(endpoint)) == old_server:
                stored.recovery = {
                    key: value
                    for key, value in stored.recovery.items()
                    if key != str(endpoint)
                }
                orm.SQLStorableMixin.update(stored, session=current)
            self.recovery = stored.recovery

    def observed(self, **fields):
        with atomic_session(self._get_engine(), None) as current:
            current.execute("SELECT pg_advisory_xact_lock(1129074520, 0)")
            stored = self.objects.get_one(
                filters={"uuid": filters.EQ(self.uuid)}, session=current
            )
            for name, value in fields.items():
                if name == "status" and stored.status == "DELETING":
                    continue
                if name == "status" and stored.endpoints != self.endpoints:
                    value = "IN_PROGRESS"
                setattr(stored, name, value)
            orm.SQLStorableMixin.update(stored, session=current)
            for name in fields:
                setattr(self, name, getattr(stored, name))


class CloudLinkService(basic.BasicService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    def _bindings(link):
        return CloudLinkEndpoint.objects.get_all(
            filters={"link": filters.EQ(link.uuid)}
        )

    @staticmethod
    def _subnet(endpoint):
        return compute.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(endpoint.subnet)}
        )

    def _material(self, link, endpoints):
        secrets = link.secrets
        changed = False
        if not secrets:
            secrets = {"ca": openvpn.certificate(str(link.uuid)), "endpoints": {}}
            changed = True
        elif openvpn.expiring(secrets["ca"]["cert"]):
            # Keep the signing key so old and renewed trust stores both accept
            # leaf certificates while agents apply the update independently.
            secrets["ca"] = openvpn.certificate(
                str(link.uuid), private_key=secrets["ca"]["key"]
            )
            changed = True
        used = [
            ipaddress.IPv4Network(e["tunnel_cidr"])
            for other in CloudNetworkLink.objects.get_all()
            for e in other.secrets.get("endpoints", {}).values()
        ]
        prefixes = [
            ipaddress.IPv4Network(str(self._subnet(e).cidr))
            for other in CloudNetworkLink.objects.get_all()
            for e in self._bindings(other)
        ]
        for endpoint in endpoints:
            own = str(endpoint.uuid)
            if own in secrets["endpoints"]:
                entry = secrets["endpoints"][own]
                for role in ["server", "client"]:
                    if openvpn.expiring(entry[role]["cert"]):
                        entry[role] = openvpn.certificate(
                            endpoint.uuid.hex + "-" + role,
                            secrets["ca"],
                            server=role == "server",
                        )
                        changed = True
                continue
            tunnel = next(
                (
                    s
                    for s in ipaddress.IPv4Network("100.127.0.0/16").subnets(
                        new_prefix=24
                    )
                    if s not in used and not any(s.overlaps(p) for p in prefixes)
                ),
                None,
            )
            if tunnel is None:
                raise ValueError("No free OpenVPN tunnel subnet")
            used.append(tunnel)
            secrets["endpoints"][own] = {
                "server": openvpn.certificate(
                    endpoint.uuid.hex + "-server", secrets["ca"], server=True
                ),
                "client": openvpn.certificate(
                    endpoint.uuid.hex + "-client", secrets["ca"]
                ),
                "tls_server_key": openvpn.tls_key(),
                "tls_clients": {},
                "tunnel_cidr": str(tunnel),
            }
            changed = True
        for endpoint in endpoints:
            own = secrets["endpoints"][str(endpoint.uuid)]
            for server in endpoints:
                if (
                    server.uuid >= endpoint.uuid
                    or server.uuid.hex in own["tls_clients"]
                ):
                    continue
                own["tls_clients"][server.uuid.hex] = openvpn.tls_key(
                    secrets["endpoints"][str(server.uuid)]["tls_server_key"]
                )
                changed = True
        if changed:
            link.observed(secrets=secrets, status="IN_PROGRESS")
        return secrets

    def _provider(self, endpoint):
        cloud = models.connection(endpoint.connection).load_driver()
        subnet = self._subnet(endpoint)
        network = compute.Network.objects.get_one(
            filters={"uuid": filters.EQ(subnet.network)}
        )
        external = cloud._find(cloud.networks(), "network", network.uuid)
        external_subnet = (
            cloud._find(cloud.subnets(external), "subnet", subnet.uuid)
            if external
            else None
        )
        return cloud, subnet, external_subnet

    def _reserve(self, link, endpoint, peers):
        if local.managed(endpoint):
            return local.reserve(link, endpoint)
        if endpoint.connection is None:
            return {
                "endpoint": endpoint.definition["endpoint"],
                "agent": endpoint.definition["agent"],
            }
        cloud, subnet, provider_subnet = self._provider(endpoint)
        if provider_subnet is None:
            raise ValueError("Cloud subnet is not ready")
        transport = link.transport
        rules = [
            {
                "direction": "ingress",
                "ethertype": "IPv4",
                "protocol": transport["protocol"],
                "port_range_min": transport["port"],
                "port_range_max": transport["port"],
                "remote_ip_prefix": "0.0.0.0/0",
            },
            {
                "direction": "ingress",
                "ethertype": "IPv4",
                "remote_ip_prefix": str(subnet.cidr),
            },
            {
                "direction": "egress",
                "ethertype": "IPv4",
                "remote_ip_prefix": "0.0.0.0/0",
            },
        ]
        group = cloud.security_group(endpoint.uuid, rules)
        remote_cidrs = [str(self._subnet(p).cidr) for p in peers]
        port = cloud.ensure_port(
            endpoint.uuid,
            provider_subnet,
            security_group_ids=[group.id],
            allowed_address_pairs=[{"ip_address": c} for c in remote_cidrs],
        )
        assigned = port.fixed_ips[0]["ip_address"]
        reservation = catalog.Address.objects.get_one_or_none(
            filters={"uuid": filters.EQ(endpoint.uuid)}
        )
        if reservation and reservation.address != assigned:
            reservation.delete()
            reservation = None
        if reservation is None:
            catalog.Address(
                uuid=endpoint.uuid,
                subnet=subnet.uuid,
                project_id=link.project_id,
                kind="reservation",
                address=assigned,
            ).insert()
        if sorted(p["ip_address"] for p in port.allowed_address_pairs or []) != sorted(
            remote_cidrs
        ):
            cloud.api.network.update_port(
                port.id, allowed_address_pairs=[{"ip_address": c} for c in remote_cidrs]
            )
        floating = cloud.ensure_floating_ip(
            port, endpoint.definition["external_network"]
        )
        return {
            "endpoint": floating.floating_ip_address,
            "gateway_ip": assigned,
            "port_id": port.id,
            "agent": str(endpoint.uuid),
        }

    def _routes(self, endpoint, state, remote_cidrs):
        if endpoint.connection is None and not local.managed(endpoint):
            return
        if local.managed(endpoint):
            from exordos_core.network.subnet import effective_routers

            subnet = self._subnet(endpoint)
            old = [
                {"destination": str(r["to"]), "nexthop": str(r["via"])}
                for r in effective_routers(subnet)
            ]
        else:
            cloud, _, subnet = self._provider(endpoint)
            old = list(subnet.host_routes or [])
        gateway = state["gateway_ip"]
        # Own all routes through the dedicated link gateway, preserve other next hops.
        routes = [r for r in old if r["nexthop"] != gateway]
        if any(
            ipaddress.IPv4Network(r["destination"]).overlaps(
                ipaddress.IPv4Network(cidr)
            )
            for r in routes
            for cidr in remote_cidrs
            if r["destination"] != "0.0.0.0/0"
        ):
            raise ValueError("An existing subnet route conflicts with the cloud link")
        routes += [{"destination": c, "nexthop": gateway} for c in remote_cidrs]
        if routes != old:
            if local.managed(endpoint):
                subnet.routers = [
                    {
                        "to": netaddr.IPNetwork(r["destination"]),
                        "via": netaddr.IPAddress(r["nexthop"]),
                    }
                    for r in routes
                ]
                subnet.update()
            else:
                cloud.api.network.update_subnet(subnet.id, host_routes=routes)

    @staticmethod
    def _target(endpoint, bundle, agent, generation):
        target = ua.TargetResource.objects.get_one_or_none(
            filters={"uuid": filters.EQ(endpoint.uuid), "kind": filters.EQ(KIND)}
        )
        value = {
            "uuid": str(endpoint.uuid),
            "configuration": bundle,
            "generation": generation,
        }
        if target is None:
            target = ua.TargetResource(
                uuid=endpoint.uuid, kind=KIND, agent=uuid.UUID(agent), value=value
            )
            target.calculate_hash()
            target.full_hash = target.hash
            target.insert()
        elif target.value != value or str(target.agent) != agent:
            target.value, target.agent = value, uuid.UUID(agent)
            target.calculate_hash()
            target.full_hash = target.hash
            target.update()
        return target

    def _boot(self, link, endpoint, state, bundle):
        if local.managed(endpoint):
            return local.booted(endpoint)
        if endpoint.connection is None:
            return True
        config = models.connection(endpoint.connection)
        cloud = config.load_driver()
        agent = ua.UniversalAgent.objects.get_one_or_none(
            filters={"uuid": filters.EQ(endpoint.uuid)}
        )
        if agent is None:
            ua.UniversalAgent(
                uuid=endpoint.uuid,
                node=endpoint.uuid,
                name="cloud-link-gateway",
                capabilities={"capabilities": [KIND]},
                facts={"facts": []},
            ).insert()
        key = ua.NodeEncryptionKey.get_or_create(endpoint.uuid)
        agent_data = bootstrap.agent_user_data(
            endpoint.uuid,
            key.private_key,
            config.orch_endpoint,
            config.status_endpoint,
            config.agent_package,
            hostname="cloud-link-" + endpoint.uuid.hex[:8],
            caps_drivers="OpenVPNCapabilityDriver",
        )
        port = cloud.api.network.get_port(state["port_id"])
        server = cloud.server(
            endpoint.uuid, uuid.uuid5(config.uuid, "network-gateways")
        )
        recovering = link.recovery.get(str(endpoint.uuid))
        if server and recovering == server.id:
            cloud.delete_server(server)
            server = None
        if server and server.status in {"ERROR", "SHUTOFF"}:
            cloud.delete_server(server)
            return False
        if server is None:
            for actual in ua.Resource.objects.get_all(
                filters={"uuid": filters.EQ(endpoint.uuid), "kind": filters.EQ(KIND)}
            ):
                actual.delete()
        server = cloud.ensure_server(
            endpoint.uuid,
            name="exordos-gateway-" + endpoint.uuid.hex[:8],
            pool=uuid.uuid5(config.uuid, "network-gateways"),
            project=link.project_id,
            cores=endpoint.definition["gateway_cores"],
            ram=endpoint.definition["gateway_ram"],
            image=endpoint.definition["gateway_image"],
            ports=[port],
            user_data=openvpn.bootstrap_user_data(endpoint.uuid, bundle, agent_data),
        )
        state["server_id"] = server.id
        if recovering and server.id != recovering:
            link.complete_recovery(endpoint.uuid, recovering)
        return server.status == "ACTIVE"

    def _remove(self, link, endpoint):
        for target in ua.TargetResource.objects.get_all(
            filters={"uuid": filters.EQ(endpoint.uuid), "kind": filters.EQ(KIND)}
        ):
            target.delete()
        actual = ua.Resource.objects.get_all(
            filters={"uuid": filters.EQ(endpoint.uuid), "kind": filters.EQ(KIND)}
        )
        if endpoint.connection is None:
            if local.managed(endpoint):
                if link.state.get(str(endpoint.uuid), {}).get("gateway_ip"):
                    self._routes(endpoint, link.state[str(endpoint.uuid)], [])
                if not local.remove(endpoint):
                    return False
                for obj in actual:
                    obj.delete()
                agent = ua.UniversalAgent.objects.get_one_or_none(
                    filters={
                        "uuid": filters.EQ(uuid.uuid5(endpoint.uuid, local.AGENT_NAME))
                    }
                )
                if agent:
                    agent.delete()
            elif actual:
                return False
        else:
            cloud, _, provider_subnet = self._provider(endpoint)
            server = cloud.server(
                endpoint.uuid, uuid.uuid5(endpoint.connection, "network-gateways")
            )
            if server:
                cloud.delete_server(server)
                return False
            port = cloud._find(cloud.api.network.ports(), "port", endpoint.uuid)
            if port:
                if provider_subnet:
                    routes = [
                        r
                        for r in provider_subnet.host_routes or []
                        if r["nexthop"] != port.fixed_ips[0]["ip_address"]
                    ]
                    cloud.api.network.update_subnet(
                        provider_subnet.id, host_routes=routes
                    )
                for floating in cloud.api.network.ips(port_id=port.id):
                    cloud._require(floating, "floating_ip")
                    cloud.api.network.delete_ip(floating.id)
                cloud.delete_port(port)
            group = cloud._find(
                cloud.api.network.security_groups(), "security_group", endpoint.uuid
            )
            if group:
                cloud.api.network.delete_security_group(group.id)
            for model in [catalog.Address, ua.UniversalAgent, ua.NodeEncryptionKey]:
                for obj in model.objects.get_all(
                    filters={"uuid": filters.EQ(endpoint.uuid)}
                ):
                    obj.delete()
            for obj in actual:
                obj.delete()
        endpoint.delete()
        return True

    def reconcile(self, link):
        bindings = self._bindings(link)
        if link.status == "DELETING":
            if all([self._remove(link, e) for e in bindings]):
                orm.SQLStorableMixin.delete(link)
            return
        wanted = {uuid.UUID(e["subnet"]) for e in link.endpoints}
        endpoints = sorted(
            [e for e in bindings if e.subnet in wanted], key=lambda e: e.uuid
        )
        removed = [e for e in bindings if e.subnet not in wanted]
        # Fence removed cloud sites even if an unavailable survivor cannot yet
        # acknowledge the new topology. Keep ownership until route cleanup.
        for endpoint in removed:
            if local.managed(endpoint):
                local.fence(endpoint)
            elif endpoint.connection is not None:
                cloud = models.connection(endpoint.connection).load_driver()
                server = cloud.server(
                    endpoint.uuid, uuid.uuid5(endpoint.connection, "network-gateways")
                )
                if server:
                    cloud.delete_server(server)
        if len(endpoints) < 2:
            raise ValueError("Cloud link endpoints are missing")
        secrets = self._material(link, endpoints)
        state = {
            str(e.uuid): self._reserve(
                link, e, [p for p in endpoints if p.uuid != e.uuid]
            )
            for e in endpoints
        }
        state.update(
            {
                str(e.uuid): link.state[str(e.uuid)]
                for e in removed
                if str(e.uuid) in link.state
            }
        )
        for key, value in state.items():
            if link.state.get(key, {}).get("server_id"):
                value["server_id"] = link.state[key]["server_id"]
        # All public endpoints exist before the first gateway VM is created.
        link.observed(
            state=state,
            **({"status": "IN_PROGRESS"} if link.status in {"NEW", "ERROR"} else {}),
        )
        ready = True
        for endpoint in endpoints:
            own = dict(
                secrets["endpoints"][str(endpoint.uuid)],
                cidr=str(self._subnet(endpoint).cidr),
                gateway_ip=state[str(endpoint.uuid)].get("gateway_ip"),
            )
            peers = [
                {
                    "id": str(p.uuid),
                    "cidr": str(self._subnet(p).cidr),
                    "endpoint": state[str(p.uuid)]["endpoint"],
                }
                for p in endpoints
                if p.uuid != endpoint.uuid
            ]
            bundle = openvpn.render(
                endpoint.uuid,
                own,
                peers,
                secrets["ca"]["cert"],
                link.transport["protocol"],
                link.transport["port"],
                link.mtu,
            )
            current = state[str(endpoint.uuid)]
            booted = self._boot(link, endpoint, current, bundle)
            self._routes(endpoint, current, [p["cidr"] for p in peers])
            target = self._target(
                endpoint,
                bundle,
                current["agent"],
                current.get("server_id", str(endpoint.uuid)),
            )
            actual = ua.Resource.objects.get_one_or_none(
                filters={"uuid": filters.EQ(endpoint.uuid), "kind": filters.EQ(KIND)}
            )
            ready &= bool(
                booted
                and actual
                and actual.hash == target.hash
                and actual.value.get("status") == "ACTIVE"
            )
        # Withdraw removed peers before deleting their gateways. Disconnected
        # survivors retain their bindings until they apply the new configuration.
        if ready:
            for endpoint in removed:
                if self._remove(link, endpoint):
                    secrets["endpoints"].pop(str(endpoint.uuid), None)
                    state.pop(str(endpoint.uuid), None)
                else:
                    ready = False
        link.observed(
            state=state, secrets=secrets, status="ACTIVE" if ready else "IN_PROGRESS"
        )

    def _iteration(self):
        with provider_lock("cloud-network-links", blocking=False) as acquired:
            if not acquired:
                return
            for link in CloudNetworkLink.objects.get_all():
                try:
                    with contextlib.ExitStack() as stack:
                        connections = sorted(
                            {
                                e.connection
                                for e in self._bindings(link)
                                if e.connection is not None
                            }
                        )
                        for connection in connections:
                            stack.enter_context(provider_lock(connection))
                        current = CloudNetworkLink.objects.get_one(
                            filters={"uuid": filters.EQ(link.uuid)}
                        )
                        self.reconcile(current)
                except Exception:
                    LOG.exception(
                        "Unable to reconcile cloud network link %s", link.uuid
                    )
                    current = CloudNetworkLink.objects.get_one_or_none(
                        filters={"uuid": filters.EQ(link.uuid)}
                    )
                    if current is not None:
                        current.observed(status="ERROR")
