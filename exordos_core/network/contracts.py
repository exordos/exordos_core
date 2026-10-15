#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Canonical placement selection shared by Port and Address creation."""

from contextlib import contextmanager

import netaddr
from restalchemy.common import contexts
from restalchemy.dm import filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.common import exceptions


def resolve_subnet(
    project_id, subnet=None, network=None, address=None, floating=False, session=None
):
    from exordos_core.compute.dm import models as compute
    from exordos_core.user_api.network.dm import models as catalog

    if (subnet is None) == (network is None):
        raise exceptions.ValidateException(
            err="specify exactly one of subnet or network"
        )
    candidates = compute.Subnet.objects.get_all(
        session=session,
        filters={
            "uuid" if subnet is not None else "network": filters.EQ(
                subnet if subnet is not None else network
            )
        },
    )
    eligible = []
    for candidate in candidates:
        if not catalog.may_allocate_in(project_id, candidate):
            continue
        if floating:
            if not catalog.is_floating_pool(candidate):
                continue
        elif floating is False and (
            not candidate.placeable or candidate.next_server is not None
        ):
            continue
        if address is not None:
            try:
                ip = netaddr.IPAddress(str(address))
            except (ValueError, netaddr.AddrFormatError):
                raise exceptions.ValidateException(
                    err="address is not an IP address"
                ) from None
            if ip.version != 4 or ip not in candidate.cidr:
                continue
            if candidate.ip_range_pair is not None:
                first, last = candidate.ip_range_pair
                if not first <= ip <= last:
                    continue
        eligible.append(candidate)
    if not eligible:
        raise exceptions.ValidateException(
            err="no accessible subnet matches placement and address"
        )
    if len(eligible) != 1:
        raise storage_exc.ConflictRecords(
            model="Subnet",
            msg="specify subnet; candidates: "
            + ", ".join(sorted(str(s.uuid) for s in eligible)),
        )
    return eligible[0]


def validate_placement_input(value, *, create, allow_default=False):
    both_missing = value.get("subnet") is None and value.get("network") is None
    if (
        create
        and (value.get("subnet") is None) == (value.get("network") is None)
        and not (allow_default and both_missing)
    ):
        raise exceptions.ValidateException(
            err="specify exactly one of subnet or network"
        )
    for key in ("subnet", "network"):
        if key in value and value[key] is None:
            raise exceptions.ValidateException(err=f"{key} cannot be null")


def validate_port_input(value, stored=None):
    validate_inline_fields(
        value,
        PORT_CREATE_FIELDS
        | {"activation_barrier"}
        | ({"owner_node"} if stored is not None else set()),
        "Port",
    )
    validate_placement_input(value, create=stored is None, allow_default=True)
    for field in ("uuid", "project_id", "subnet", "network", "node", "owner_node"):
        if value.get(field) is not None:
            validate_uuid(value[field], field)
    node = value.get("node", getattr(stored, "node", None))
    if "index" in value:
        index = value["index"]
        if index is not None and (
            not isinstance(index, int) or isinstance(index, bool) or index < 0
        ):
            raise exceptions.ValidateException(
                err="index must be a non-negative integer"
            )
        if node is not None and index is None:
            raise exceptions.ValidateException(
                err="omit index for automatic assignment; attached index cannot be null"
            )
        if node is None and index is not None:
            raise exceptions.ValidateException(err="detached port must have index=null")
    for key in ("target_ipv4", "target_mac"):
        if key in value and value[key] is None and stored is None:
            raise exceptions.ValidateException(
                err=f"omit {key} for automatic assignment"
            )
    if value.get("target_mac") is not None:
        import re

        mac = value["target_mac"]
        if (
            not isinstance(mac, str)
            or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", mac)
            or int(mac[:2], 16) & 1
            or mac.lower() == "00:00:00:00:00:00"
        ):
            raise exceptions.ValidateException(
                err="target_mac must be a nonzero unicast MAC"
            )
    if (
        stored is not None
        and "network" in value
        and str(value["network"]) != str(stored.network)
    ):
        raise storage_exc.ConflictRecords(model="Port", msg="network is immutable")


def validate_address_input(value, stored=None):
    validate_inline_fields(
        value,
        ADDRESS_CREATE_FIELDS | ({"owner_port"} if stored is not None else set()),
        "Address",
    )
    validate_placement_input(value, create=stored is None)
    for field in ("uuid", "project_id", "subnet", "network", "port", "owner_port"):
        if value.get(field) is not None:
            validate_uuid(value[field], field)
    if stored is None and "kind" not in value:
        raise exceptions.ValidateException(err="AddressCreate requires kind")
    if "address" in value and value["address"] is None:
        raise exceptions.ValidateException(err="omit address for automatic assignment")
    kind = value.get("kind", getattr(stored, "kind", None))
    if kind == "reservation" and "port" in value:
        raise exceptions.ValidateException(err="reservation does not accept port")
    if (
        stored is not None
        and "network" in value
        and str(value["network"]) != str(stored.network)
    ):
        raise storage_exc.ConflictRecords(model="Address", msg="network is immutable")


def automatic_identity_uuid(network):
    import uuid

    return uuid.uuid5(
        uuid.UUID("6e66736c-0000-0000-0000-000000000001"), "identity:%s" % network
    )


PORT_CREATE_FIELDS = frozenset(
    (
        "uuid",
        "project_id",
        "name",
        "description",
        "subnet",
        "network",
        "node",
        "index",
        "target_ipv4",
        "target_mac",
        "dhcp",
        "dns",
        "network_identity",
        "port_security",
    )
)
ADDRESS_CREATE_FIELDS = frozenset(
    ("uuid", "project_id", "subnet", "network", "kind", "port", "address")
)
INLINE_FLOATING_CREATE_FIELDS = ADDRESS_CREATE_FIELDS - {
    "uuid",
    "project_id",
    "kind",
    "port",
}


def validate_uuid(value, field):
    import uuid

    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise exceptions.ValidateException(err=f"{field} must be a UUID") from None


def validate_inline_fields(value, allowed, field):
    extra = set(value) - allowed
    if extra:
        raise exceptions.ValidateException(
            err=f"{field} does not accept fields: {', '.join(sorted(extra))}"
        )


def applied_by_owner(target, actual, require_hash=True):
    """Only the assigned host's report for the current target is evidence."""
    from gcl_sdk.agents.universal.dm import models as ua

    if (
        target is None
        or actual is None
        or target.agent is None
        or (require_hash and actual.hash != target.hash)
    ):
        return False
    agent = ua.UniversalAgent.objects.get_one_or_none(
        filters={"uuid": filters.EQ(target.agent)}
    )
    return (
        agent is not None
        and actual.node == agent.node
        and actual.updated_at >= target.tracked_at
    )


def published_private_addresses(subnet_ids, session=None):
    """Every retained interface target holds its private IP until host ack."""
    from gcl_sdk.agents.universal.dm import models as ua

    result = {}
    for target in ua.TargetResource.objects.get_all(
        filters={
            "kind": filters.EQ("evpn_port"),
            "master": filters.In(list(subnet_ids)),
        },
        session=session,
    ):
        address = target.value.get("ipv4")
        if address:
            result.setdefault(str(target.master), set()).add(str(address))
    return result


@contextmanager
def atomic_session(engine, session=None):
    """Share one connection with nested validators, including non-HTTP calls."""
    existing = session or engine._get_session_from_storage()
    if existing is not None:
        yield existing
    else:
        with contexts.Context().session_manager() as current:
            yield current


def published_macs(subnet_ids, session=None):
    from gcl_sdk.agents.universal.dm import models as ua

    return {
        target.value["mac"].lower()
        for target in ua.TargetResource.objects.get_all(
            filters={
                "kind": filters.EQ("evpn_port"),
                "master": filters.In(list(subnet_ids)),
            },
            session=session,
        )
        if target.value.get("mac")
    }
