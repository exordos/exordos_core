#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from unittest.mock import Mock

from gcl_sdk.agents.universal.drivers import pool
from oslo_config import cfg
import pytest
from restalchemy.common import exceptions

from exordos_core.common import features
from exordos_core.common import openapi
from exordos_core.storage.scheduler import select
from exordos_core.user_api.api import routes


def test_rawstor_is_disabled_by_default():
    assert not features.rawstor_enabled()


@pytest.mark.parametrize("enabled", [False, True])
def test_storage_routes_and_openapi_follow_flag(enabled):
    cfg.CONF.set_override("rawstor", enabled, group="features")
    try:
        assert routes.ApiEndpointRoute.is_route("storage") == enabled
        if not enabled:
            with pytest.raises(exceptions.IncorrectRouteAttribute):
                routes.ApiEndpointRoute.get_route("storage")
        paths = openapi.build(openapi.USER_API)["paths"]
        assert (
            any(path.startswith("/storage/") or "/storage/" in path for path in paths)
            == enabled
        )
    finally:
        cfg.CONF.clear_override("rawstor", group="features")


def test_disabled_rawstor_keeps_local_pool_without_querying_clusters(monkeypatch):
    clusters = Mock(side_effect=AssertionError("must not query remote clusters"))
    monkeypatch.setattr(select, "_active_clusters", clusters)
    local = pool.ThinStoragePool(pool_type="dir", name="local", capacity_usable=10)
    remote = pool.ThinStoragePool(
        pool_type="rawstor", name="remote", capacity_usable=10
    )
    assert select.collect_storage_pool_candidates([local, remote]) == [(local, None)]
    clusters.assert_not_called()
