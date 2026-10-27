# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Lazy project networking; project UUIDs need not have an IAM record."""

import hashlib
import uuid

from restalchemy.dm import filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.common import config
from exordos_core.common import exceptions
from exordos_core.compute.dm import models


def find_legacy_flat_subnet(project_id, session):
    if not config.CONF.network.legacy_flat_default:
        return None
    ports = models.Port.objects.get_all(
        filters={"project_id": filters.EQ(project_id)},
        session=session,
    )
    subnet_ids = {
        getattr(port.subnet, "uuid", port.subnet)
        for port in ports
        if port.subnet is not None
    }
    if not subnet_ids:
        return None
    subnets = models.Subnet.objects.get_all(
        filters={"uuid": filters.In(list(subnet_ids)), "placeable": filters.EQ(True)},
        session=session,
    )
    network_ids = {
        getattr(subnet.network, "uuid", subnet.network) for subnet in subnets
    }
    if not network_ids:
        return None
    networks = {
        network.uuid: network
        for network in models.Network.objects.get_all(
            filters={"uuid": filters.In(list(network_ids))},
            session=session,
        )
    }
    eligible = [
        subnet
        for subnet in subnets
        if subnet.next_server is None
        and networks[getattr(subnet.network, "uuid", subnet.network)].driver_spec.get(
            "driver"
        )
        == "flat_bridge"
    ]
    if len(eligible) > 1:
        raise storage_exc.ConflictRecords(
            model="Subnet",
            msg="multiple project flat subnets; specify network or subnet",
        )
    return eligible[0] if eligible else None


def ensure_default_subnet(project_id, session):
    project_id = uuid.UUID(str(project_id))
    # Serialize the empty-network case without requiring an IAM project row.
    lock_key = int.from_bytes(
        hashlib.sha256(project_id.bytes).digest()[:8], "big", signed=True
    )
    session.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
    network = models.Network.objects.get_one_or_none(
        filters={"project_id": filters.EQ(project_id), "is_default": filters.EQ(True)},
        session=session,
    )
    if network is None:
        network = models.Network(
            project_id=project_id,
            name="default",
            is_default=True,
            driver_spec={"driver": "ovs_evpn"},
        )
        network.insert(session=session)
    subnets = models.Subnet.objects.get_all(
        filters={"network": filters.EQ(network.uuid), "placeable": filters.EQ(True)},
        session=session,
    )
    if not subnets:
        subnet = models.Subnet.restore_from_simple_view(
            project_id=str(project_id),
            name="default",
            network=str(network.uuid),
            cidr="10.128.0.0/20",
        )
        subnet.insert(session=session)
        return subnet
    # Use the same ambiguity rule as explicit network placement.
    from exordos_core.network.contracts import resolve_subnet

    return resolve_subnet(project_id, network=network.uuid, session=session)


def resolve_default_floating_subnet(project_id, session=None):
    from exordos_core.user_api.network.dm import models as catalog

    pools = [
        subnet
        for subnet in models.Subnet.objects.get_all(
            filters={"floating_pool": filters.EQ(True), "placeable": filters.EQ(False)},
            session=session,
        )
        if catalog.may_allocate_in(project_id, subnet)
    ]
    defaults = [pool for pool in pools if pool.default_floating_pool]
    candidates = defaults or pools
    if not candidates:
        raise exceptions.ValidateException(err="no accessible floating pool")
    if len(candidates) != 1:
        raise storage_exc.ConflictRecords(
            model="Subnet",
            msg="ambiguous floating pool; specify network or subnet",
        )
    return candidates[0]
