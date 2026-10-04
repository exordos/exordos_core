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

from urllib.parse import urlparse

from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.common import exceptions as ra_exc
from restalchemy.storage import exceptions as storage_exc

from exordos_core.storage import constants as sc
from exordos_core.storage.dm import models


class InvalidRawstorEndpoint(ra_exc.ValidationErrorException):
    message = "Invalid rawstor endpoint: %(msg)s"


def _validate_endpoint(endpoint, scheme):
    try:
        parsed = urlparse(endpoint)
        if (
            not isinstance(endpoint, str)
            or parsed.scheme != scheme
            or not parsed.hostname
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or any(c.isspace() for c in endpoint)
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InvalidRawstorEndpoint(msg=f"expected {scheme}://host:port/")
    return parsed


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

    def update(self, uuid, **kwargs):
        if "driver_spec" in kwargs:
            self._validate_driver_spec_uniqueness(kwargs, exclude_uuid=uuid)
        return super().update(uuid, **kwargs)

    def _validate_driver_spec_uniqueness(self, kwargs: dict, exclude_uuid=None) -> None:
        """Validate MDS ports and OST addresses before scheduling a cluster."""
        driver_spec = kwargs["driver_spec"]
        endpoint = driver_spec.get("endpoint")
        if endpoint is None:
            return
        ost_endpoint = driver_spec.get("ost_endpoint", "")
        if driver_spec.get("kind") == "rawstor":
            mds = _validate_endpoint(endpoint, "mds")
            _validate_endpoint(ost_endpoint, "ost")
        else:
            mds = urlparse(endpoint)
        # JSONB field filtering is not yet supported by restalchemy.
        existing_clusters = models.StorageCluster.objects.get_all()
        for cluster in existing_clusters:
            if str(cluster.uuid) == str(exclude_uuid):
                # Existing disks and chunk maps still use these addresses.
                if (
                    cluster.driver_spec.endpoint != endpoint
                    or cluster.driver_spec.ost_endpoint != ost_endpoint
                ):
                    raise storage_exc.ConflictRecords(
                        model="StorageCluster",
                        msg="Registered MDS and OST endpoints cannot be changed",
                    )
                continue
            if cluster.driver_spec.KIND != driver_spec["kind"]:
                continue
            existing_endpoint = urlparse(cluster.driver_spec.endpoint)
            if cluster.driver_spec.endpoint == endpoint or (
                mds.scheme == "mds"
                and existing_endpoint.scheme == "mds"
                and existing_endpoint.port == mds.port
            ):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster", msg=f"MDS port={mds.port}"
                )
            if ost_endpoint and cluster.driver_spec.ost_endpoint.rstrip(
                "/"
            ) == ost_endpoint.rstrip("/"):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster", msg=f"ost_endpoint={ost_endpoint}"
                )
