#    Copyright 2025 Genesis Corporation.
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
from oslo_config import cfg
from restalchemy.api import constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as core_constants
from exordos_core.common import exceptions as ex_exceptions
from exordos_core.common.api.controllers import StoredFieldsQueryMixin
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.user_api.network.dm import models

CONF = cfg.CONF


class PlacementResource(resources.ResourceByModelWithCustomProps):
    def generate_schema_object(self, method, openapi_version):
        schema = super().generate_schema_object(method, openapi_version)
        if "network" in schema["properties"]:
            schema["properties"]["network"].pop("readOnly", None)
        if method == constants.CREATE:
            schema["oneOf"] = [
                {"required": ["subnet"], "not": {"required": ["network"]}},
                {"required": ["network"], "not": {"required": ["subnet"]}},
            ]
            if issubclass(self.get_model(), compute_models.Port):
                schema["oneOf"].append(
                    {
                        "not": {
                            "anyOf": [
                                {"required": ["subnet"]},
                                {"required": ["network"]},
                            ]
                        }
                    }
                )
            if self.get_model() is models.Address:
                schema.setdefault("required", []).append("kind")
        return schema


class NetworkController(ra_controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/network/"


def _own_or(project_id, filters, others=None):
    """The caller's own rows, or those matching ``others``, within ``filters``."""
    scope = dm_filters.AND({"project_id": dm_filters.EQ(project_id)})
    if others:
        scope = dm_filters.OR(scope, dm_filters.AND(others))
    return dm_filters.AND(filters, scope) if filters else scope


class _VisibleScopeMixin:
    """A listing wider than the caller's project, as `_visible_scope` says."""

    def filter(self, filters, order_by=None):
        self._enforce("read")
        if self._ctx_project_id is not None:
            return super(iam_controllers.PolicyBasedController, self).filter(
                self._visible_scope(filters), order_by=order_by
            )
        return super().filter(filters, order_by=order_by)


class NetworkCRUDController(
    StoredFieldsQueryMixin,
    _VisibleScopeMixin,
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for networks over the ``compute.Network`` model.

    For ``ovs_evpn`` the driver allocates ``vni``/``rt``/``mtu``.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "network"

    __resource__ = resources.ResourceByModelWithCustomProps(
        compute_models.Network,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                # A VNI named here could be another network's overlay.
                "driver_state": {constants.ALL: field_p.Permissions.RO},
                "is_default": {constants.ALL: field_p.Permissions.RO},
                "default_identity_group": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )

    # The core allocates the rest. `flat_bridge` is operator-only: its spec
    # names a host file the core rewrites.
    CALLER_SPEC_KEYS = frozenset({"driver", "mtu"})
    MTU_RANGE = nc.OVERLAY_MTU_RANGE

    def _validate_driver_spec(self, spec) -> None:
        spec = spec or {}
        if not nc.is_overlay(spec):
            raise ex_exceptions.ValidateException(
                err=f"driver_spec.driver must be {nc.OVERLAY_DRIVER!r}"
            )
        extra = set(spec) - self.CALLER_SPEC_KEYS
        if extra:
            raise ex_exceptions.ValidateException(
                err=(
                    f"driver_spec may carry only {sorted(self.CALLER_SPEC_KEYS)}; "
                    f"{sorted(extra)} are allocated by the core"
                )
            )
        mtu = spec.get("mtu")
        if mtu is not None and not (
            isinstance(mtu, int)
            and not isinstance(mtu, bool)
            and self.MTU_RANGE[0] <= mtu <= self.MTU_RANGE[1]
        ):
            raise ex_exceptions.ValidateException(
                err="driver_spec.mtu must be an integer in %d..%d" % self.MTU_RANGE
            )

    def create(self, **kwargs):
        self._validate_driver_spec(kwargs.get("driver_spec"))
        # Publishing exposes the network to every project. Manifests bypass
        # this gate; they are operator-authored.
        if kwargs.get("access") == "public":
            self._enforce("share")
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        if "driver_spec" in kwargs:
            from gcl_sdk.agents.universal.clients.backend.db import (
                merge_declared_object,
            )

            stored = self.get(uuid=uuid)
            kwargs["driver_spec"] = merge_declared_object(
                stored.driver_spec, kwargs["driver_spec"]
            )
            self._validate_driver_spec(kwargs["driver_spec"])
        if kwargs.get("access") == "public":
            self._enforce("share")
        return super().update(uuid, **kwargs)

    def get(self, uuid, **kwargs):
        self._enforce("read")
        filters = {"uuid": dm_filters.EQ(uuid)}
        if self._ctx_project_id is not None:
            filters = self._visible_scope(filters)
        return self.model.objects.get_one(filters=filters)

    def _visible_scope(self, filters):
        """Own networks plus every ``access=public`` one."""
        return _own_or(
            self._ctx_project_id, filters, {"access": dm_filters.EQ("public")}
        )


class SubnetController(
    StoredFieldsQueryMixin,
    _VisibleScopeMixin,
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for subnets over the ``compute.Subnet`` model."""

    __policy_service_name__ = "network"
    __policy_name__ = "subnet"

    __resource__ = resources.ResourceByModelWithCustomProps(
        compute_models.Subnet,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                # Ownership is checked on create only.
                "network": {constants.ALL: field_p.Permissions.RW},
                "effective_routers": {constants.ALL: field_p.Permissions.RO},
                "effective_dns_servers": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        if kwargs.get("floating_pool") or kwargs.get("default_floating_pool"):
            self._enforce("floating_pool")
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        if "floating_pool" in kwargs or "default_floating_pool" in kwargs:
            self._enforce("floating_pool")
        return super().update(uuid, **kwargs)

    def get(self, uuid, **kwargs):
        self._enforce("read")
        filters = {"uuid": dm_filters.EQ(uuid)}
        if self._ctx_project_id is not None:
            filters = self._visible_scope(filters)
        return self.model.objects.get_one(filters=filters)

    def _visible_scope(self, filters):
        """Own subnets plus those of public networks, to allocate from."""
        public_nets = [
            n.uuid
            for n in compute_models.Network.objects.get_all(
                filters={"access": dm_filters.EQ("public")}
            )
        ]
        return _own_or(
            self._ctx_project_id,
            filters,
            {"network": dm_filters.In(public_nets)} if public_nets else None,
        )


class LBController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "lb"

    __resource__ = resources.ResourceByRAModel(
        models.LB,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "ipsv4": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )


class VhostController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __pr_name__ = "parent"
    __policy_service_name__ = "network"
    __policy_name__ = "lb_vhost"

    __resource__ = resources.ResourceByRAModel(
        models.Vhost,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "parent": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )


class VhostRouteController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __pr_name__ = "parent"
    __policy_service_name__ = "network"
    __policy_name__ = "lb_vhost_route"

    __resource__ = resources.ResourceByRAModel(
        models.Route,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "parent": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )


class BackendPoolController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __pr_name__ = "parent"
    __policy_service_name__ = "network"
    __policy_name__ = "lb_backendpool"

    __resource__ = resources.ResourceByRAModel(
        models.BackendPool,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "parent": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )


class NetworkFunctionController(
    StoredFieldsQueryMixin,
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """Read and edit network functions, never create them.

    The compiler finds functions by their owner; an ownerless one would look
    in force while nothing reads it.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "nf"

    __resource__ = resources.ResourceByModelWithCustomProps(
        models.NetworkFunction,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                # The compiler's: a nulled owner orphans the function while
                # a second one is seeded beside it.
                "provenance": {constants.ALL: field_p.Permissions.RO},
                "generated_config": {constants.ALL: field_p.Permissions.RO},
                "effective_config": {constants.ALL: field_p.Permissions.RO},
                "kind": {constants.UPDATE: field_p.Permissions.RO},
                "owner_subnet": {constants.ALL: field_p.Permissions.RO},
                "owner_network": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        raise ex_exceptions.ValidateException(
            err=(
                "network functions are not created on their own: a subnet is "
                "seeded with its DHCP, a network with "
                "its resolver and metadata proxy, and a port with what it "
                "expands into. Edit the one that already serves the object "
                "you mean"
            )
        )

    def update(self, uuid, **kwargs):
        if "config" in kwargs:
            from gcl_sdk.agents.universal.clients.backend.db import (
                merge_declared_object,
            )

            stored = self.get(uuid=uuid)
            kwargs["config"] = merge_declared_object(stored.config, kwargs["config"])
        return super().update(uuid, **kwargs)


def _network_of(subnet):
    """The network uuid a subnet names, however the row hands it over."""
    return getattr(subnet.network, "uuid", subnet.network)


class PortController(
    StoredFieldsQueryMixin,
    _VisibleScopeMixin,
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for ports over the ``compute.Port`` model."""

    __policy_service_name__ = "network"
    __policy_name__ = "port"

    __resource__ = PlacementResource(
        compute_models.Port,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "activation_barrier": {constants.ALL: field_p.Permissions.HIDDEN},
                "legacy_flat_placement": {constants.ALL: field_p.Permissions.HIDDEN},
                "source": {constants.ALL: field_p.Permissions.RO},
                "interface": {constants.ALL: field_p.Permissions.RO},
                "config": {constants.ALL: field_p.Permissions.RO},
                "target_mask": {constants.ALL: field_p.Permissions.RO},
                "machine": {constants.ALL: field_p.Permissions.RO},
                # `generated` rows may sit outside their node's project.
                "provenance": {constants.ALL: field_p.Permissions.RO},
                # On the platform's legs the address is IPAM's, and it is
                # what a door onto the node leads to.
                "ipv4": {constants.ALL: field_p.Permissions.RO},
                "security_groups": {constants.ALL: field_p.Permissions.RO},
                "identity_groups": {constants.ALL: field_p.Permissions.RO},
                "network": {constants.ALL: field_p.Permissions.RW},
                "mask": {constants.ALL: field_p.Permissions.RO},
                "mac": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        self.model.validate_create_input(kwargs)
        from exordos_core.network.contracts import atomic_session
        from exordos_core.network.permissions import gate_allocated_private_address
        from exordos_core.network.permissions import gate_explicit_address

        gate_explicit_address(
            self,
            kwargs,
            self._ctx_project_id or kwargs.get("project_id", core_constants.ZERO_UUID),
            private=True,
        )
        with atomic_session(self.model._get_engine()) as session:
            port = super().create(**kwargs)
            gate_allocated_private_address(self, port, session)
            return port

    def update(self, uuid, **kwargs):
        stored = self.get(uuid)
        self.model.validate_update_input(kwargs, stored)
        # The selector is a read-only projection once placement is resolved.
        kwargs.pop("network", None)
        return super().update(uuid, **kwargs)

    def _own_overlay_subnets(self):
        """The caller's overlay subnets, where foreign ports are shown.

        Flat subnets are shared metal; an overlay subnet is the tenant's own
        segment, so any port in it is theirs to see.
        """
        subnets = compute_models.Subnet.objects.get_all(
            filters={"project_id": dm_filters.EQ(self._ctx_project_id)}
        )
        if not subnets:
            return []
        networks = {
            str(network.uuid): network
            for network in compute_models.Network.objects.get_all(
                filters={
                    "uuid": dm_filters.In(
                        list({str(_network_of(subnet)) for subnet in subnets})
                    )
                }
            )
        }
        return [
            subnet.uuid
            for subnet in subnets
            if nc.is_overlay(
                getattr(networks.get(str(_network_of(subnet))), "driver_spec", None)
            )
        ]

    def _visible_scope(self, filters):
        """Own ports plus any port on the caller's overlay subnets.

        A managed service's port (requester-managed) would otherwise hold
        addresses in the tenant's subnet invisibly. Read-only: `update` and
        `delete` still force `project_id`.
        """
        subnets = self._own_overlay_subnets()
        return _own_or(
            self._ctx_project_id,
            filters,
            {"subnet": dm_filters.In(subnets)} if subnets else None,
        )

    def get(self, uuid, **kwargs):
        """What `filter` lists, fetched one by one.

        `get` cannot take the `OR` scope, so fetch wide and decide; a
        refusal re-asks the narrow way so "not yours" fails as before.
        """
        self._enforce("read")
        # The policy-based `get` takes the uuid by keyword, the base one
        # positionally.
        if self._ctx_project_id is None:
            return super().get(uuid=uuid, **kwargs)
        port = super(iam_controllers.PolicyBasedController, self).get(uuid, **kwargs)
        if str(port.project_id) == str(self._ctx_project_id):
            return port
        subnet = getattr(port.subnet, "uuid", port.subnet)
        if any(str(subnet) == str(own) for own in self._own_overlay_subnets()):
            return port
        return super().get(uuid=uuid, **kwargs)


class CatalogController(ra_controllers.RoutesListController):
    """Namespace router for /v1/network/catalog/ reference objects."""

    __TARGET_PATH__ = "/v1/network/catalog/"


class SecurityGroupController(
    StoredFieldsQueryMixin,
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for security-group reference objects."""

    __policy_service_name__ = "network"
    __policy_name__ = "security_group"

    __resource__ = resources.ResourceByModelWithCustomProps(
        models.SecurityGroup,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={"rules": {constants.ALL: field_p.Permissions.RO}},
        ),
    )


class SecurityGroupRuleController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "security_group_rule"
    __resource__ = resources.ResourceByRAModel(
        models.SecurityGroupRule, convert_underscore=False
    )


class PortSecurityGroupController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "port_security_group"
    __resource__ = resources.ResourceByRAModel(
        models.PortSecurityGroup, convert_underscore=False
    )


class PortIdentityGroupController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "port_identity_group"
    __resource__ = resources.ResourceByRAModel(
        models.PortIdentityGroup, convert_underscore=False
    )


class IdentityGroupController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for identity groups; the wire bit is allocated, never given."""

    __policy_service_name__ = "network"
    __policy_name__ = "identity_group"

    __resource__ = resources.ResourceByRAModel(
        models.IdentityGroup,
        convert_underscore=False,
        hidden_fields=["identity"],
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "automatic": {constants.ALL: field_p.Permissions.RO},
                # Naming a bit would claim another group's membership.
                "identity": {constants.ALL: field_p.Permissions.RO},
                # The bit is only unique within its network.
                "network": {constants.UPDATE: field_p.Permissions.RO},
            },
        ),
    )


class ProxyServiceController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for the doors an overlay network opens onto the installation.

    Needs `custom_destination` to name a bare address (not ownership-
    checkable) or to open a door on a non-private network (whose other
    tenants' agents would talk to the door owner as if to the platform).
    """

    __policy_service_name__ = "network"
    __policy_name__ = "proxy_service"

    __resource__ = resources.ResourceByRAModel(
        models.ProxyService,
        convert_underscore=False,
    )

    def _gate_privileged(self, kwargs, uuid=None) -> None:
        # Fields an update omits are taken from the stored row.
        existing = None
        if uuid is not None:
            existing = models.ProxyService.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(uuid)}
            )
        destination = kwargs.get("destination", getattr(existing, "destination", None))
        kind = getattr(destination, "KIND", None) or (
            destination.get("kind") if isinstance(destination, dict) else None
        )
        if kind == "address":
            self._enforce("custom_destination")
        network = kwargs.get("network", getattr(existing, "network", None))
        if network is None:
            return
        found = compute_models.Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(network)}
        )
        if found is not None and found.access != "private":
            self._enforce("custom_destination")

    def create(self, **kwargs):
        self._gate_privileged(kwargs)
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        self._gate_privileged(kwargs, uuid=uuid)
        return super().update(uuid, **kwargs)


class AddressController(
    StoredFieldsQueryMixin,
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for address reference objects — the IPAM ledger."""

    __policy_service_name__ = "network"
    __policy_name__ = "address"

    __resource__ = PlacementResource(
        models.Address,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                # The compiler's. A caller-written `floating` origin would
                # open the fabric guard to any address.
                "origin": {constants.ALL: field_p.Permissions.RO},
                "owner_port": {constants.ALL: field_p.Permissions.RO},
                "association": {constants.ALL: field_p.Permissions.RO},
                "allocation": {constants.ALL: field_p.Permissions.RO},
                "delete_requested": {constants.ALL: field_p.Permissions.HIDDEN},
                "status": {constants.ALL: field_p.Permissions.RO},
                "network": {constants.ALL: field_p.Permissions.RW},
                # An allocation does not move: an update naming only the
                # address would pin one past the create-time gate below.
                "address": {constants.ALL: field_p.Permissions.RW},
                "subnet": {constants.ALL: field_p.Permissions.RW},
            },
        ),
    )

    def _gate_explicit_address(self, kwargs, stored=None) -> None:
        if kwargs.get("address") is None:
            return
        from exordos_core.network.contracts import resolve_subnet

        subnet = resolve_subnet(
            self._ctx_project_id
            or kwargs.get("project_id")
            or getattr(stored, "project_id", core_constants.ZERO_UUID),
            subnet=kwargs.get("subnet", getattr(stored, "subnet", None)),
            network=kwargs.get("network"),
            address=kwargs["address"],
            floating=True
            if kwargs.get("kind", getattr(stored, "kind", None)) == "floating"
            else None,
        )
        owner = (
            self._ctx_project_id
            or kwargs.get("project_id")
            or getattr(stored, "project_id", core_constants.ZERO_UUID)
        )
        if str(subnet.project_id) != str(owner):
            self._enforce("explicit_address")

    def create(self, **kwargs):
        self.model.validate_create_input(kwargs)
        self._gate_explicit_address(kwargs)
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        stored = self.get(uuid=uuid)
        # Allocation placement and value are immutable; reject changes before
        # resolving a new location (which could otherwise mask a 409).
        for field in ("subnet", "address", "kind"):
            if field in kwargs and str(kwargs[field]) != str(getattr(stored, field)):
                if field == "address":
                    self._gate_explicit_address({"address": stored.address}, stored)
                from restalchemy.storage import exceptions as storage_exc

                raise storage_exc.ConflictRecords(
                    model="Address", msg=f"{field} is immutable"
                )
        self._gate_explicit_address(kwargs, stored)
        self.model.validate_update_input(kwargs, stored)
        kwargs.pop("network", None)
        return super().update(uuid, **kwargs)


class BorderController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "border"

    __resource__ = resources.ResourceByRAModel(
        models.Border,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "ipsv4": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )
