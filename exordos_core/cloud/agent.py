# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from gcl_sdk.agents.universal.drivers import cloud
from gcl_sdk.agents.universal.drivers import pool

from exordos_core.cloud.models import connection


class ControllerMetaPool(pool.MetaPool):
    def load_driver(self):
        if self.driver_spec.KIND != "cloud":
            return super().load_driver()
        config = connection(self.driver_spec.connection)
        key = ("cloud", self.uuid)
        stamp = (config.updated_at, self.driver_spec.dump_to_simple_view())
        cached = self.__driver_map__.get(key)
        if cached is None or cached[0] != stamp:
            cached = (
                stamp,
                cloud.CloudPoolDriver(self, provider=config.load_driver()),
            )
            self.__driver_map__[key] = cached
        return cached[1]


class ControllerPoolAgentDriver(cloud.ControllerPoolAgentDriver):
    __model_map__ = dict(
        cloud.ControllerPoolAgentDriver.__model_map__, pool=ControllerMetaPool
    )
