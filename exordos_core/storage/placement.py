#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import uuid

from restalchemy.dm import filters as dm_filters

from exordos_core.compute.dm import models as compute_models


def references_remote_pool(value, pool_ids, local_pools):
    name = str(value.get("storage_pool"))
    if name not in pool_ids:
        return False
    if value.get("storage_location") or value.get("storage_backend") == "rawstor":
        return True
    if value.get("storage_backend") == "local":
        return False
    try:
        owner = uuid.UUID(str(value.get("pool")))
    except (ValueError, TypeError, AttributeError):
        # Missing placement cannot prove this is a local disk.
        return True
    if owner not in local_pools:
        pools = compute_models.MachinePool.objects.get_all(
            filters={"uuid": dm_filters.EQ(owner)}
        )
        local_pools[owner] = {p.name for pool in pools for p in pool.storage_pools}
    # Match the same local-name precedence used when assigning a volume.
    return name not in local_pools[owner]
