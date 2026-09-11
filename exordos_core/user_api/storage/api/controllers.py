#    Copyright 2026 Genesis Corporation.
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

from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.storage import exceptions as storage_exc

from exordos_core.storage import constants as sc
from exordos_core.storage.dm import models


class StorageController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/storage/"


class StorageClustersController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/storage/clusters/ endpoint"""

    __policy_name__ = "storage_cluster"
    __policy_service_name__ = sc.POLICY_SERVICE_NAME

    __resource__ = resources.ResourceByRAModel(
        model_class=models.StorageCluster,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        self._validate_driver_spec_uniqueness(kwargs)

        return super().create(**kwargs)

    def _validate_driver_spec_uniqueness(self, kwargs: dict) -> None:
        """Validate the driver_spec's endpoint is unique among clusters.

        Two clusters sharing an endpoint would make a volume's
        storage_location ambiguous about which cluster actually holds it.
        """
        driver_spec = kwargs["driver_spec"]
        endpoint = driver_spec.get("endpoint")
        if endpoint is None:
            return

        # TODO(akremenetsky): Use JSON field filter when restalchemy
        # supports filtering by JSONB fields.
        existing_clusters = models.StorageCluster.objects.get_all()
        for cluster in existing_clusters:
            if (
                cluster.driver_spec.KIND == driver_spec["kind"]
                and getattr(cluster.driver_spec, "endpoint", None) == endpoint
            ):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster",
                    msg=f"endpoint={endpoint}",
                )
