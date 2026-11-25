#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

"""Publish object lifecycle jobs to Core, retaining data until detach completes."""

from gcl_sdk.agents.universal.dm import models as ua
from restalchemy.dm import filters

from exordos_core.compute.dm import models as compute
from exordos_core.storage.dm import models


def sync_targets(builder):
    clusters = {
        c.uuid: c
        for c in models.StorageCluster.objects.get_all(
            filters={"builder": filters.EQ(builder)}
        )
    }
    if not clusters:
        return
    pools = {
        str(p.uuid): p
        for p in models.StoragePool.objects.get_all(
            filters={"cluster": filters.In(list(clusters))}
        )
    }
    targets = {
        t.uuid: t
        for t in ua.TargetResource.objects.get_all(
            filters={"kind": filters.EQ("storage_volume")}
        )
        if t.value.get("cluster") in {str(c) for c in clusters}
    }
    desired = set()
    if pools:
        for volume in compute.MachineVolume.objects.get_all(
            filters={
                "storage_backend": filters.EQ("rawstor"),
                "storage_pool": filters.In(list(pools)),
            }
        ):
            policy = pools[volume.storage_pool]
            cluster = clusters[policy.cluster]
            if cluster.agent is None:
                # Keep an existing object while its Core agent is being scheduled.
                desired.add(volume.uuid)
                continue
            value = {
                "uuid": str(volume.uuid),
                "cluster": str(cluster.uuid),
                "project_id": str(volume.project_id),
                "size": volume.size,
                "storage_pool": str(policy.uuid),
                "storage_location": cluster.driver_spec.endpoint,
                "storage_policy": {
                    "mirrors": policy.driver_spec.mirrors,
                    "failure_domain": policy.driver_spec.failure_domain,
                },
            }
            target = ua.TargetResource(
                uuid=volume.uuid,
                kind="storage_volume",
                value=value,
                agent=cluster.agent,
                res_uuid=ua.Resource.gen_res_uuid(volume.uuid, "storage_volume"),
            )
            target.calculate_hash()
            desired.add(volume.uuid)
            current = targets.get(volume.uuid)
            if current is None:
                target.insert()
            elif current.hash != target.hash or current.agent != target.agent:
                current.update_value(target)
                current.agent = target.agent
                current.update()
    # A removed SQL row does not mean the disk is detached. Both the desired
    # attachment and the hypervisor's last actual report must have disappeared.
    attached = {
        r.uuid
        for model in (ua.TargetResource, ua.Resource)
        for r in model.objects.get_all(filters={"kind": filters.EQ("pool_volume")})
    }
    for identifier, target in targets.items():
        if identifier not in desired and identifier not in attached:
            target.delete()
