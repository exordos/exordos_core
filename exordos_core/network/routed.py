# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

"""Logical routed networks, their local segments and deferred port placement."""

import ipaddress
import logging
import uuid

from restalchemy.dm import filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage import exceptions
from restalchemy.storage.sql import orm

from exordos_core.common.dm.models import ModelWithFullAsset
from exordos_core.common.exceptions import ValidateException
from exordos_core.network.contracts import atomic_session

LOG = logging.getLogger(__name__)


class NetworkSegment(
    ModelWithFullAsset, orm.SQLStorableWithJSONFieldsMixin, ra_models.SimpleViewMixin
):
    __tablename__ = "network_segments"
    __jsonfields__ = ["definition"]

    network = properties.property(types.UUID(), required=True)
    local_network = properties.property(types.UUID(), required=True)
    subnet = properties.property(types.UUID(), required=True)
    definition = properties.property(types.Dict(), required=True)
    status = properties.property(
        types.Enum(["IN_PROGRESS", "ACTIVE", "ERROR", "DELETING"]),
        default="IN_PROGRESS",
    )
    error = properties.property(types.String(), default="")


def parts(network, session=None):
    return NetworkSegment.objects.get_all(
        filters={"network": filters.EQ(network.uuid)}, session=session
    )


def owner(*, subnet=None, network=None, session=None):
    query = (
        {"subnet": filters.EQ(subnet)}
        if subnet is not None
        else {"local_network": filters.EQ(network)}
    )
    rows = NetworkSegment.objects.get_all(filters=query, session=session, limit=1)
    return rows[0] if rows else None


def require_unmanaged(**kwargs):
    if owner(**kwargs) is not None:
        raise exceptions.ConflictRecords(
            model="NetworkSegment", msg="manage the segment through its logical network"
        )


def require_logical_request(value, session=None):
    """New workloads select the logical network, rather than its backing rows."""
    value = value or {}
    segment = None
    if value.get("subnet"):
        segment = owner(subnet=value["subnet"], session=session)
    elif value.get("network"):
        segment = owner(network=value["network"], session=session)
    if segment is not None:
        raise ValidateException(
            err="select the logical network instead of its local segment"
        )


def requested_network(value, project_id, session=None, *, allow_foreign=False):
    from exordos_core.compute.dm import models

    if not isinstance(value, dict) or not value.get("network"):
        return None
    network = models.Network.objects.get_one(
        filters={"uuid": filters.EQ(value["network"])}, session=session
    )
    if network.driver_spec.get("driver") != "routed":
        return None
    if session is not None:
        session.execute(
            "SELECT pg_advisory_xact_lock(1381258580, hashtext(%s))",
            (str(network.uuid),),
        )
        network = models.Network.objects.get_one(
            filters={"uuid": filters.EQ(network.uuid)}, session=session
        )
    target_project = value.get("project_id", project_id)
    if str(target_project) != str(project_id) and not allow_foreign:
        raise ValidateException(err="logical network belongs to another project")
    if (
        str(network.project_id) != str(target_project)
        and network.access != "public"
        and not allow_foreign
    ):
        raise ValidateException(err="logical network belongs to another project")
    if network.driver_state.get("deleting"):
        raise ValidateException(err="logical network cleanup is in progress")
    if value.get("target_ipv4") is not None or value.get("auto_floating_ip"):
        raise ValidateException(
            err="logical network placement requires automatic private addressing"
        )
    return network


def native_pair(left, right):
    """New segments in one Neutron scope share a managed router."""
    from exordos_core.cloud.models import connection

    a, b = left["driver_spec"], right["driver_spec"]
    return (
        not left.get("subnet")
        and not right.get("subnet")
        and a.get("driver") == b.get("driver") == "cloud"
        and a["connection"] == b["connection"]
        and connection(a["connection"]).driver_spec.KIND == "openstack"
    )


def validate_gateway_options(value, cloud=False):
    from gcl_sdk.cloud import openvpn

    allowed = {"role", "gateway_image", "gateway_cores", "gateway_ram"}
    allowed |= (
        {"external_subnet", "gateway_disk"}
        if cloud == "core"
        else {"external_network"}
        if cloud
        else {"external_subnet", "gateway_disk", "agent", "endpoint"}
    )
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValidateException(err="Invalid network gateway options")
    if value.get("role", "auto") not in openvpn.ROLES:
        raise ValidateException(err="Invalid gateway role")
    for key in ("external_network", "external_subnet", "agent"):
        if key in value:
            try:
                uuid.UUID(str(value[key]))
            except (TypeError, ValueError):
                raise ValidateException(err="Invalid gateway resource UUID") from None
    for key, minimum in (
        ("gateway_cores", 1),
        ("gateway_ram", 512),
        ("gateway_disk", 1),
    ):
        if key in value and (type(value[key]) is not int or value[key] < minimum):
            raise ValidateException(err="Invalid gateway resources")
    for key in ("gateway_image", "endpoint"):
        if key in value and (not isinstance(value[key], str) or not value[key].strip()):
            raise ValidateException(err="Invalid gateway image or endpoint")


def lock(network, session):
    session.execute(
        "SELECT pg_advisory_xact_lock(1381258580, hashtext(%s))", (str(network.uuid),)
    )


def save_network(network, session=None, update=False):
    from exordos_core.compute.dm import models

    with atomic_session(network._get_engine(), session) as current:
        lock(network, current)
        try:
            prefix = ipaddress.IPv4Network(str(network.cidr))
            if prefix.prefixlen > 23:
                raise ValueError("Network address pool must contain a /23")
            if network.driver_spec and network.driver_spec != {"driver": "routed"}:
                raise ValueError("Logical network placement belongs to its subnets")
            policy = {
                "driver": "auto",
                "protocol": "tcp",
                "port": 443,
                "mtu": 1380,
                **network.connectivity,
            }
            if (
                set(policy) != {"driver", "protocol", "port", "mtu"}
                or policy["driver"] not in {"auto", "openvpn"}
                or policy["protocol"] not in {"tcp", "udp"}
                or type(policy["port"]) is not int
                or not 1 <= policy["port"] <= 65535
                or type(policy["mtu"]) is not int
                or not 1280 <= policy["mtu"] <= 1500
            ):
                raise ValueError("Invalid connectivity policy")
        except (TypeError, ValueError) as error:
            raise ValidateException(err=str(error)) from None
        network.driver_spec = {"driver": "routed"}
        if update:
            stored = models.Network.objects.get_one(
                filters={"uuid": filters.EQ(network.uuid)}, session=current
            )
            if (
                stored.cidr != network.cidr
                or stored.project_id != network.project_id
                or stored.connectivity != network.connectivity
            ):
                raise exceptions.ConflictRecords(
                    model="Network", msg="network scope and connectivity are immutable"
                )
            if stored.driver_state.get("deleting"):
                raise exceptions.ConflictRecords(
                    model="Network", msg="network cleanup is in progress"
                )
            network.driver_state = stored.driver_state
            for part in parts(network, current):
                if not part.definition.get("subnet"):
                    local = models.Network.objects.get_one(
                        filters={"uuid": filters.EQ(part.local_network)},
                        session=current,
                    )
                    if local.access != network.access:
                        local.access = network.access
                        local.update(session=current)
            return orm.SQLStorableMixin.update(network, session=current)
        return orm.SQLStorableMixin.insert(network, session=current)


def definition_for(subnet, root, session, imported=None):
    from exordos_core.compute.dm import models

    refs = [str(ref) for ref in subnet.hypervisors]
    if not refs or len(refs) != len(set(refs)):
        raise ValidateException(err="A subnet requires distinct hypervisor UUIDs")
    pools = []
    for ref in sorted(refs):
        session.execute(
            "SELECT pg_advisory_xact_lock(1129074517, hashtext(%s))", (ref,)
        )
        pools.append(
            models.MachinePool.objects.get_one(
                filters={"uuid": filters.EQ(ref)}, session=session
            )
        )
    clouds = [pool for pool in pools if pool.is_cloud]
    if clouds and (len(clouds) != 1 or len(pools) != 1):
        raise ValidateException(
            err="A provider subnet belongs to exactly one cloud hypervisor"
        )
    if clouds and clouds[0].project_id != root.project_id:
        raise ValidateException(err="Cloud hypervisor belongs to another project")
    if not clouds and any(
        pool.driver_spec.KIND not in {"libvirt", "exordos_local_hyper"}
        or not pool.driver_spec.ovs
        for pool in pools
    ):
        raise ValidateException(
            err="Local subnet hypervisors must support the SDN fabric"
        )
    effective = [{**pool.network_defaults, **subnet.gateway} for pool in pools]
    if any(value != effective[0] for value in effective):
        raise ValidateException(
            err="Set subnet gateway overrides for hypervisors with different network defaults"
        )
    gateway = effective[0]
    validate_gateway_options(gateway, clouds[0].driver_spec.KIND if clouds else False)
    driver = {"driver": "ovs_evpn"}
    if clouds:
        driver = {"driver": "cloud", "connection": str(clouds[0].uuid)}
        if gateway.get("external_network"):
            driver["external_network"] = gateway["external_network"]
    if imported is not None:
        local = models.Network.objects.get_one(
            filters={"uuid": filters.EQ(imported.network)}, session=session
        )
        if local.driver_spec.get("driver") != driver["driver"]:
            raise ValidateException(
                err="Imported subnet and hypervisor drivers do not match"
            )
        if clouds and str(local.driver_spec.get("connection")) != str(clouds[0].uuid):
            raise ValidateException(
                err="Imported subnet belongs to another cloud hypervisor"
            )
        driver = local.driver_spec
    result = {"driver_spec": driver, "hypervisors": refs, "gateway": gateway}
    if imported is not None:
        result["subnet"] = str(imported.uuid)
    return result


def validate_topology(root, definitions):
    from gcl_sdk.cloud import openvpn

    policy = root.connectivity.get("driver", "auto")
    for index, left in enumerate(definitions):
        for right in definitions[index + 1 :]:
            if policy == "auto" and native_pair(left, right):
                if left["driver_spec"].get("external_network") != right[
                    "driver_spec"
                ].get("external_network"):
                    raise ValidateException(
                        err="Native subnets must use the same external network"
                    )
                continue
            for endpoint in (left, right):
                gateway = endpoint["gateway"]
                if endpoint["driver_spec"]["driver"] == "cloud":
                    if not gateway.get("gateway_image") or not (
                        gateway.get("external_network")
                        or gateway.get("external_subnet")
                        or endpoint["driver_spec"].get("external_network")
                    ):
                        raise ValidateException(
                            err="Cloud connectivity requires a gateway image and external network"
                        )
                elif not gateway.get("agent") and not (
                    gateway.get("gateway_image") and gateway.get("external_subnet")
                ):
                    raise ValidateException(
                        err="Local connectivity requires a gateway image and transport subnet"
                    )
            try:
                openvpn.serves(
                    uuid.UUID(int=1),
                    uuid.UUID(int=2),
                    left["gateway"].get("role", "auto"),
                    right["gateway"].get("role", "auto"),
                )
            except ValueError as error:
                raise ValidateException(err=str(error)) from None


def save_subnet(subnet, network, session, imported=None):
    import netaddr

    from exordos_core.compute.dm import models

    lock(network, session)
    root = models.Network.objects.get_one(
        filters={"uuid": filters.EQ(network.uuid)}, session=session
    )
    if root.driver_state.get("deleting"):
        raise exceptions.ConflictRecords(
            model="Network", msg="network cleanup is in progress"
        )
    if subnet.project_id != root.project_id:
        raise ValidateException(
            err="Subnet and network must belong to the same project"
        )
    if subnet.floating_pool or subnet.default_floating_pool or not subnet.placeable:
        raise ValidateException(
            err="Logical network subnets must support private workload placement"
        )
    definition = definition_for(subnet, root, session, imported)
    current = parts(root, session)
    validate_topology(
        root,
        [part.definition for part in current if part.status != "DELETING"]
        + [definition],
    )
    reserved = [
        ipaddress.IPv4Network(
            str(
                models.Subnet.objects.get_one(
                    filters={"uuid": filters.EQ(part.subnet)}, session=session
                ).cidr
            )
        )
        for part in current
    ]
    pool = ipaddress.IPv4Network(str(root.cidr))
    if imported is not None:
        for field in ("cidr", "ip_range"):
            if str(getattr(subnet, field)) != str(getattr(imported, field)):
                raise exceptions.ConflictRecords(
                    model="Subnet", msg=f"{field} is immutable"
                )
        prefix = ipaddress.IPv4Network(str(imported.cidr))
    elif subnet.cidr is not None:
        prefix = ipaddress.IPv4Network(str(subnet.cidr))
        if prefix.prefixlen != 23 or not prefix.subnet_of(pool):
            raise ValidateException(
                err="A managed subnet must be a /23 within the network pool"
            )
    else:
        prefix = next(
            (
                candidate
                for candidate in pool.subnets(new_prefix=23)
                if not any(candidate.overlaps(p) for p in reserved)
            ),
            None,
        )
    if prefix is None:
        raise ValidateException(err="No free /23 in network address pool")
    if any(prefix.overlaps(other) for other in reserved):
        raise ValidateException(err="Subnet CIDRs overlap")
    if imported is None:
        local = models.Network(
            uuid=uuid.uuid5(subnet.uuid, "network"),
            project_id=root.project_id,
            name=subnet.name or str(subnet.uuid),
            access=root.access,
            owner_network=root.uuid,
            driver_spec=definition["driver_spec"],
        )
        local.insert(session=session)
        subnet.properties["cidr"].set_value_force(netaddr.IPNetwork(str(prefix)))
        if not subnet.routers:
            subnet.routers = [
                {
                    "to": netaddr.IPNetwork("0.0.0.0/0"),
                    "via": netaddr.IPAddress(str(prefix.network_address + 1)),
                }
            ]
        subnet.network = local.uuid
    else:
        if imported.project_id != root.project_id:
            raise ValidateException(err="Imported subnet belongs to another project")
        subnet.network = imported.network
        local = models.Network.objects.get_one(
            filters={"uuid": filters.EQ(subnet.network)}, session=session
        )
        local.driver_state = {
            **local.driver_state,
            "routed_segments": {
                **local.driver_state.get("routed_segments", {}),
                str(subnet.uuid): str(root.uuid),
            },
        }
        orm.SQLStorableMixin.update(local, session=session)
    subnet.logical_network = root.uuid
    subnet.status, subnet.error = "IN_PROGRESS", ""
    subnet._validate_routes()
    if imported is None:
        result = orm.SQLStorableMixin.insert(subnet, session=session)
    else:
        result = orm.SQLStorableMixin.update(subnet, session=session)
    NetworkSegment(
        uuid=subnet.uuid,
        project_id=root.project_id,
        name=subnet.name,
        network=root.uuid,
        local_network=subnet.network,
        subnet=subnet.uuid,
        definition=definition,
    ).insert(session=session)
    return result


def update_subnet(subnet, stored, session, force=False):
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.compute.dm import models

    root = models.Network.objects.get_one(
        filters={"uuid": filters.EQ(stored.logical_network)}, session=session
    )
    lock(root, session)
    part = owner(subnet=subnet.uuid, session=session)
    if root.driver_state.get("deleting") or part.status == "DELETING":
        raise exceptions.ConflictRecords(
            model="Subnet", msg="subnet cleanup is in progress"
        )
    for field in (
        "network",
        "logical_network",
        "cidr",
        "ip_range",
        "project_id",
        "hypervisors",
    ):
        if getattr(subnet, field) != getattr(stored, field):
            raise exceptions.ConflictRecords(
                model="Subnet", msg=f"{field} is immutable"
            )
    if subnet.gateway != stored.gateway:
        if endpoints_for_subnet(subnet.uuid, session):
            raise exceptions.ConflictRecords(
                model="Subnet", msg="gateway placement is immutable while connected"
            )
        definition = definition_for(
            subnet,
            root,
            session,
            imported=stored if part.definition.get("subnet") else None,
        )
        if definition["driver_spec"] != part.definition["driver_spec"]:
            raise exceptions.ConflictRecords(
                model="Subnet", msg="subnet provider placement is immutable"
            )
        validate_topology(
            root,
            [
                definition if other.uuid == part.uuid else other.definition
                for other in parts(root, session)
                if other.status != "DELETING"
            ],
        )
        part.definition = definition
        part.update(session=session)
    subnet._validate_routes()
    return orm.SQLStorableMixin.update(subnet, session=session, force=force)


def delete_subnet(subnet, session):
    from exordos_core.compute.dm import models

    root = models.Network.objects.get_one(
        filters={"uuid": filters.EQ(subnet.logical_network)}, session=session
    )
    lock(root, session)
    part = owner(subnet=subnet.uuid, session=session)
    if busy(part, session):
        raise exceptions.ConflictRecords(
            model="Subnet", msg="subnet has allocated resources"
        )
    part.status = subnet.status = "DELETING"
    part.update(session=session)
    return orm.SQLStorableMixin.update(subnet, session=session)


def busy(segment, session=None):
    from exordos_core.cloud.links import CloudNetworkLink
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.cloud.local import managed
    from exordos_core.compute.dm import models
    from exordos_core.user_api.network.dm import models as catalog

    owned_links = {
        link.uuid
        for link in CloudNetworkLink.objects.get_all(
            filters={"owner_network": filters.EQ(segment.network)}, session=session
        )
    }
    endpoints = endpoints_for_subnet(segment.subnet, session)
    if any(endpoint.link not in owned_links for endpoint in endpoints):
        return True
    gateways = {e.uuid for e in endpoints if managed(e)}
    for model in (models.Node, models.NodeSet):
        if any(
            not (model is models.Node and item.uuid in gateways)
            and (
                str((item.properties.get_value("default_network") or {}).get("subnet"))
                == str(segment.subnet)
                or str(
                    (item.properties.get_value("default_network") or {}).get("network")
                )
                == str(segment.local_network)
            )
            for item in model.objects.get_all(session=session)
        ):
            return True
    reservations = {endpoint.uuid for endpoint in endpoints}
    if any(
        address.uuid not in reservations or address.kind != "reservation"
        for address in catalog.Address.objects.get_all(
            filters={"subnet": filters.EQ(segment.subnet)}, session=session
        )
    ) or models.NodeSet.objects.get_all(
        filters={"network_subnet": filters.EQ(segment.subnet)}, session=session
    ):
        return True

    return any(
        p.node not in gateways and p.owner_node not in gateways
        for p in models.Port.objects.get_all(
            filters={"subnet": filters.EQ(segment.subnet)}, session=session
        )
    )


def delete_network(network, session=None):
    from exordos_core.compute.dm import models

    session.execute(
        "SELECT pg_advisory_xact_lock(1381258580, hashtext(%s))", (str(network.uuid),)
    )
    for model in (models.Node, models.NodeSet):
        if any(
            str((item.properties.get_value("default_network") or {}).get("network"))
            == str(network.uuid)
            for item in model.objects.get_all(session=session)
        ):
            raise exceptions.ConflictRecords(
                model="Network", msg="network is referenced by nodes or node sets"
            )
    for segment in parts(network, session):
        if busy(segment, session):
            raise exceptions.ConflictRecords(
                model="Network", msg="network has allocated ports"
            )
        segment.status = "DELETING"
        segment.update(session=session)
        subnet = models.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(segment.subnet)}, session=session
        )
        subnet.status = "DELETING"
        orm.SQLStorableMixin.update(subnet, session=session)
    network.driver_state = {**network.driver_state, "deleting": True}
    return orm.SQLStorableMixin.update(network, session=session)


def router_id(network):
    if network.driver_spec.get("driver") != "cloud":
        return None
    if getattr(network, "owner_network", None) is None:
        return None
    segment = owner(network=network.uuid)
    if segment is None or segment.definition.get("subnet"):
        return None
    from exordos_core.compute.dm import models

    root = models.Network.objects.get_one(filters={"uuid": filters.EQ(segment.network)})
    if root.connectivity.get("driver", "auto") != "auto":
        return None
    return uuid.uuid5(root.uuid, "router:" + str(network.driver_spec["connection"]))


def native_subnets(link):
    if not isinstance(getattr(link, "owner_network", None), uuid.UUID):
        return set()
    from exordos_core.compute.dm import models

    network = models.Network.objects.get_one(
        filters={"uuid": filters.EQ(link.owner_network)}
    )
    if network.connectivity.get("driver", "auto") != "auto":
        return set()
    members = parts(network)
    return {
        frozenset((str(a.subnet), str(b.subnet)))
        for a in members
        for b in members
        if a.uuid != b.uuid and native_pair(a.definition, b.definition)
    }


def candidate(node, pool):
    from exordos_core.compute.dm import models

    network = requested_network(
        node.properties.get_value("default_network"), placement_project(node)
    )
    if network is None:
        return None
    ports = models.Port.objects.get_all(filters={"node": filters.EQ(node.uuid)})
    for segment in parts(network):
        if ports and any(p.subnet != segment.subnet for p in ports if p.index == 0):
            continue
        if (
            segment.status != "ACTIVE"
            or str(pool.uuid) not in segment.definition["hypervisors"]
        ):
            continue
        local = models.Network.objects.get_one(
            filters={"uuid": filters.EQ(segment.local_network)}
        )
        spec = local.driver_spec
        cloud = pool.is_cloud
        if (
            cloud
            and spec.get("driver") == "cloud"
            and str(pool.uuid) == str(spec["connection"])
        ) or (not cloud and spec.get("driver") != "cloud"):
            return segment
    return False


def bind(node, pool, session):
    from exordos_core.compute.dm import models
    from exordos_core.network.node_creation import create_default_port

    segment = candidate(node, pool)
    if segment is None:
        return
    if segment is False:
        raise ValidateException(err="No active network segment on the selected site")
    session.execute(
        "SELECT pg_advisory_xact_lock(1381258580, hashtext(%s))",
        (str(segment.network),),
    )
    segment = candidate(node, pool)
    if segment is False:
        raise ValidateException(err="Network segment is no longer available")
    if models.Port.objects.get_all(
        filters={"node": filters.EQ(node.uuid)}, session=session
    ):
        return
    value = dict(node.properties.get_value("default_network"))
    value.pop("network")
    value["subnet"] = str(segment.subnet)
    create_default_port(
        node, value, workload_project(node, session), session, allow_foreign=True
    )


def workload_project(node, session=None):
    from exordos_core.compute import constants
    from exordos_core.compute.dm import models

    if node.node_set is not None and node.project_id == constants.NODE_SET_PROJECT:
        parent = models.NodeSet.objects.get_one(
            filters={"uuid": filters.EQ(node.node_set)}, session=session
        )
        return parent.project_id
    return node.project_id


def placement_project(node, session=None):
    return (node.properties.get_value("default_network") or {}).get(
        "project_id", workload_project(node, session)
    )


def reconcile(network):
    from exordos_core.compute.dm import models

    with atomic_session(network._get_engine()) as session:
        session.execute("SAVEPOINT routed_network")
        session.execute(
            "SELECT pg_advisory_xact_lock(1381258580, hashtext(%s))",
            (str(network.uuid),),
        )
        current = models.Network.objects.get_one_or_none(
            filters={"uuid": filters.EQ(network.uuid)}, session=session
        )
        try:
            if current is not None:
                _reconcile(current)
        except Exception:
            session.execute("ROLLBACK TO SAVEPOINT routed_network")
            raise
        finally:
            session.execute("RELEASE SAVEPOINT routed_network")


def _reconcile(network):
    from exordos_core.cloud.links import CloudNetworkLink
    from exordos_core.cloud.links import endpoints_for_subnet
    from exordos_core.compute.dm import models

    current = parts(network)
    deleting = network.driver_state.get("deleting", False)
    active = [p for p in current if p.status != "DELETING" and not deleting]
    pairs = [(a, b) for i, a in enumerate(active) for b in active[i + 1 :]]
    policy = network.connectivity
    vpn = any(
        policy.get("driver", "auto") != "auto"
        or not native_pair(a.definition, b.definition)
        for a, b in pairs
    )
    link_id = uuid.uuid5(network.uuid, "connectivity")
    link = CloudNetworkLink.objects.get_one_or_none(
        filters={"uuid": filters.EQ(link_id)}
    )
    if not vpn and link is not None:
        if link.status != "DELETING":
            link.delete()
    if vpn:
        endpoints = []
        for part in active:
            endpoint = {
                "subnet": str(part.subnet),
                **part.definition.get("gateway", {}),
            }
            if part.definition["driver_spec"].get("external_network"):
                endpoint.setdefault(
                    "external_network",
                    part.definition["driver_spec"].get("external_network"),
                )
            endpoints.append(endpoint)
        if link is None:
            link = CloudNetworkLink(
                uuid=link_id,
                owner_network=network.uuid,
                project_id=network.project_id,
                name=network.name,
                endpoints=endpoints,
                transport={
                    k: policy.get(k, v)
                    for k, v in {
                        "driver": "openvpn",
                        "protocol": "tcp",
                        "port": 443,
                    }.items()
                },
                mtu=policy.get("mtu", 1380),
            )
            link.transport["driver"] = "openvpn"
            link.insert()
        elif link.status != "DELETING" and {e["subnet"] for e in link.endpoints} != {
            e["subnet"] for e in endpoints
        }:
            link.endpoints = endpoints
            link.update()
    state = {}
    for part in current:
        if deleting or part.status == "DELETING":
            if endpoints_for_subnet(part.subnet):
                state[str(part.subnet)] = {
                    "subnet": str(part.subnet),
                    "status": "DELETING",
                    "error": part.error,
                }
                continue
            session = models.Network._get_engine()._get_session_from_storage()
            session.execute("SAVEPOINT routed_segment")
            try:
                subnet = models.Subnet.objects.get_one(
                    filters={"uuid": filters.EQ(part.subnet)}
                )
                local = models.Network.objects.get_one(
                    filters={"uuid": filters.EQ(part.local_network)}
                )
                if not part.definition.get("subnet"):
                    subnet._retire_routed = True
                    subnet.delete()
                    part.delete()
                    local.delete()
                else:
                    part.delete()
                    subnet.logical_network = None
                    subnet.hypervisors, subnet.gateway = [], {}
                    subnet.status, subnet.error = "ACTIVE", ""
                    orm.SQLStorableMixin.update(subnet)
                    marker = dict(local.driver_state.get("routed_segments", {}))
                    marker.pop(str(part.subnet), None)
                    local.driver_state = {
                        **local.driver_state,
                        "routed_segments": marker,
                    }
                    orm.SQLStorableMixin.update(local)
            except Exception:
                session.execute("ROLLBACK TO SAVEPOINT routed_segment")
                LOG.exception("Error retiring network segment %s", part.uuid)
                part = NetworkSegment.objects.get_one(
                    filters={"uuid": filters.EQ(part.uuid)}
                )
                part.status, part.error = "DELETING", "Segment cleanup failed; retrying"
                part.update()
                state[str(part.subnet)] = {
                    "hypervisors": part.definition["hypervisors"],
                    "subnet": str(part.subnet),
                    "status": part.status,
                    "error": part.error,
                }
            finally:
                session.execute("RELEASE SAVEPOINT routed_segment")
            continue
        try:
            local = models.Network.objects.get_one(
                filters={"uuid": filters.EQ(part.local_network)}
            )
            driver = local.load_driver()
            ready = any(s.uuid == part.subnet for s in driver.list_subnets())
            if ready and (shared := router_id(local)):
                ready = driver.native_ready(part.subnet, shared)
            if vpn:
                from gcl_sdk.agents.universal.dm import models as ua

                from exordos_core.cloud.links import KIND

                endpoint = next(iter(endpoints_for_subnet(part.subnet)), None)
                target = (
                    ua.TargetResource.objects.get_one_or_none(
                        filters={
                            "uuid": filters.EQ(endpoint.uuid),
                            "kind": filters.EQ(KIND),
                        }
                    )
                    if endpoint
                    else None
                )
                actual = (
                    ua.Resource.objects.get_one_or_none(
                        filters={
                            "uuid": filters.EQ(endpoint.uuid),
                            "kind": filters.EQ(KIND),
                        }
                    )
                    if endpoint
                    else None
                )
                ready = ready and bool(
                    target
                    and actual
                    and target.hash == actual.hash
                    and actual.value.get("status") == "ACTIVE"
                )
            part.status, part.error = ("ACTIVE" if ready else "IN_PROGRESS"), ""
        except Exception:
            LOG.exception("Error reconciling network segment %s", part.uuid)
            part.status, part.error = "ERROR", "Local network reconciliation failed"
        part.update()
        observed = models.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(part.subnet)}
        )
        observed.status, observed.error = part.status, part.error
        orm.SQLStorableMixin.update(observed)
        state[str(part.subnet)] = {
            "hypervisors": part.definition["hypervisors"],
            "subnet": str(part.subnet),
            "status": part.status,
            "error": part.error,
        }
    if deleting and not parts(network):
        orm.SQLStorableMixin.delete(network)
        return
    network.driver_state = {"subnets": state, "deleting": deleting}
    orm.SQLStorableMixin.update(network)


def reconcile_all(networks=None):
    from exordos_core.compute.dm import models

    for network in models.Network.objects.get_all() if networks is None else networks:
        if network.driver_spec.get("driver") == "routed":
            try:
                reconcile(network)
            except Exception:
                LOG.exception("Error reconciling logical network %s", network.uuid)
                for part in parts(network):
                    if part.status not in {"ACTIVE", "DELETING"}:
                        part.status, part.error = (
                            "ERROR",
                            "Network connectivity reconciliation failed",
                        )
                        part.update()


def expand_security_rules(port, rules):
    """Resolve logical-network group members to routable source addresses."""
    from exordos_core.compute.dm import models
    from exordos_core.user_api.network.dm import models as catalog

    rules = list(rules)
    groups = {r["remote_group_id"] for r in rules if r.get("remote_group_id")}
    segment = (
        owner(subnet=getattr(port.subnet, "uuid", port.subnet)) if groups else None
    )
    if segment is None:
        return rules
    root = models.Network.objects.get_one(filters={"uuid": filters.EQ(segment.network)})
    subnets = {p.subnet for p in parts(root)}
    memberships = catalog.PortSecurityGroup.objects.get_all(
        filters={"security_group": filters.In(groups)}
    )
    ports = {
        p.uuid: p
        for p in models.Port.objects.get_all(
            filters={"uuid": filters.In(m.port for m in memberships)}
        )
    }
    addresses = {str(group): set() for group in groups}
    for member in memberships:
        peer = ports.get(member.port)
        if peer is not None and peer.subnet in subnets and peer.ipv4 is not None:
            addresses[str(member.security_group)].add(str(peer.ipv4) + "/32")
    result = []
    for rule in rules:
        group = rule.get("remote_group_id")
        if group:
            for address in sorted(addresses[str(group)]):
                result.append(
                    {
                        **{k: v for k, v in rule.items() if k != "remote_group_id"},
                        "remote_ip": address,
                    }
                )
        else:
            result.append(rule)
    return result
