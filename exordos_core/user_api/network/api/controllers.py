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

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.compute.dm import models as compute_models
from exordos_core.user_api.network.dm import models

CONF = cfg.CONF


class NetworkController(ra_controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/network/"


class NetworkCRUDController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for overlay/flat networks.

    Exposes the existing ``compute.Network`` storage model — no parallel
    table. ``driver_spec`` selects the driver (``flat_bridge`` / ``ovs_evpn``);
    for ``ovs_evpn`` the driver fills ``vni``/``rt``/``mtu`` on first load, so
    those are effectively read-only from the caller's point of view.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "network"

    __resource__ = resources.ResourceByRAModel(
        compute_models.Network,
        convert_underscore=False,
    )

    def create(self, **kwargs):
        # Publishing a network (access=public) is a privileged act: it
        # exposes the network to every project. The permission gate is on the
        # HTTP path only; the manifest/db-back path writes the model directly
        # and is operator-authored.
        if kwargs.get("access") == "public":
            self._enforce("share")
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        # Same gate for private -> public: flipping access is publishing too.
        if kwargs.get("access") == "public":
            self._enforce("share")
        return super().update(uuid, **kwargs)

    def filter(self, filters, order_by=None):
        """Shared-network visibility.

        A caller lists its own networks **plus** any ``access=public`` network
        of the installation — the isolation boundary is the network, not the
        project. Overrides the parent's project-id forcing with an OR scope,
        keeping any caller-supplied filters (name, driver, …) as an AND.
        """
        self._enforce("read")
        if self._ctx_project_id is not None:
            scope = dm_filters.OR(
                dm_filters.AND({"project_id": dm_filters.EQ(self._ctx_project_id)}),
                dm_filters.AND({"access": dm_filters.EQ("public")}),
            )
            merged = dm_filters.AND(filters, scope) if filters else scope
            return super(iam_controllers.PolicyBasedController, self).filter(
                merged, order_by=order_by
            )
        return super().filter(filters, order_by=order_by)


class SubnetController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for subnets.

    Top-level with a ``network`` reference and ``?network=`` filtering (the
    AWS/GCP shape), exposing the existing ``compute.Subnet`` storage model.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "subnet"

    __resource__ = resources.ResourceByRAModel(
        compute_models.Subnet,
        convert_underscore=False,
    )

    def filter(self, filters, order_by=None):
        """Subnets of a shared network are visible to allocators.

        A caller sees its own subnets **plus** the subnets of any public
        network, so it can pick one to allocate a public address in. Ports and
        allocations stay project-scoped elsewhere — only the subnet is shared.
        Caller-supplied filters (e.g. ``?network=``) are kept as an AND.
        """
        self._enforce("read")
        if self._ctx_project_id is not None:
            public_nets = [
                n.uuid
                for n in compute_models.Network.objects.get_all(
                    filters={"access": dm_filters.EQ("public")}
                )
            ]
            branches = [
                dm_filters.AND({"project_id": dm_filters.EQ(self._ctx_project_id)})
            ]
            if public_nets:
                branches.append(dm_filters.AND({"network": dm_filters.In(public_nets)}))
            scope = dm_filters.OR(*branches) if len(branches) > 1 else branches[0]
            merged = dm_filters.AND(filters, scope) if filters else scope
            return super(iam_controllers.PolicyBasedController, self).filter(
                merged, order_by=order_by
            )
        return super().filter(filters, order_by=order_by)


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
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """Read and edit the network functions.

    Not create them. A function is what something else already has — a
    subnet's DHCP and default group, a network's resolver and metadata
    proxy, what a port expanded into — and the compiler finds it by that
    ownership. One made through this endpoint would belong to nothing, be
    read by nothing, and look exactly like a function that is in force,
    which is the worst of the three.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "nf"

    __resource__ = resources.ResourceByRAModel(
        models.NetworkFunction,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={},
        ),
    )

    def create(self, **kwargs):
        raise ex_exceptions.ValidateException(
            err=(
                "network functions are not created on their own: a subnet is "
                "seeded with its DHCP and its default group, a network with "
                "its resolver and metadata proxy, and a port with what it "
                "expands into. Edit the one that already serves the object "
                "you mean"
            )
        )


class PortController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for ports.

    Thin base + the ``simple`` ``config`` kind over the existing
    ``compute.Port`` storage model. ``status`` is reconcile-driven and
    ``machine`` is derived, so both are read-only.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "port"

    __resource__ = resources.ResourceByRAModel(
        compute_models.Port,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "machine": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )


class CatalogController(ra_controllers.RoutesListController):
    """Namespace router for /v1/network/catalog/ reference objects."""

    __TARGET_PATH__ = "/v1/network/catalog/"


class SecurityGroupController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for security-group reference objects."""

    __policy_service_name__ = "network"
    __policy_name__ = "security_group"

    __resource__ = resources.ResourceByRAModel(
        models.SecurityGroup,
        convert_underscore=False,
    )


class IdentityGroupController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for identity groups — what a port *is*.

    The number a group travels as is allocated here rather than asked for:
    it is an implementation detail of the fabric, and one chosen by hand
    could collide with a group in another project and hand its access away.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "identity_group"

    __resource__ = resources.ResourceByRAModel(
        models.IdentityGroup,
        convert_underscore=False,
        hidden_fields=["identity", "conj_id"],
    )

    def create(self, **kwargs):
        # Bits are a network's, so the allocation needs to know which one —
        # and what comes back is either a bit or the join number of an
        # address set, which is the compiler's decision and not the
        # caller's. Both are hidden for the same reason: they are how the
        # fabric carries the group, and one chosen by hand could collide
        # with a group in another project and hand its access away.
        if kwargs.get("identity") is None and kwargs.get("conj_id") is None:
            kwargs.update(models.IdentityGroup.allocate(kwargs.get("network")))
        return super().create(**kwargs)


class AddressController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    """CRUD for address reference objects — the IPAM ledger.

    ``allocation`` (``reserved``/``freed``) says whether the address is held
    against its subnet's pool; ``association`` says which port is using it.
    Both are edited here — freeing an address hands it back, and it is
    refused while something is answering on it.
    """

    __policy_service_name__ = "network"
    __policy_name__ = "address"

    __resource__ = resources.ResourceByRAModel(
        models.Address,
        convert_underscore=False,
    )

    def _gate_explicit_address(self, kwargs) -> None:
        # Pinning a *specific* address in a subnet the caller does not own
        # (possible only for a public network — the model refuses the rest)
        # needs the explicit-address permission; auto-allocation there does
        # not. Own-subnet allocations are unaffected.
        if kwargs.get("address") is not None and kwargs.get("subnet") is not None:
            subnet = compute_models.Subnet.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(kwargs["subnet"])}
            )
            if subnet is not None and str(subnet.project_id) != str(
                self._ctx_project_id
            ):
                self._enforce("explicit_address")

    def create(self, **kwargs):
        self._gate_explicit_address(kwargs)
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        # The same gate on update: pinning an explicit address into a foreign
        # public subnet by PUT must not skip the permission the create path
        # enforces.
        self._gate_explicit_address(kwargs)
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
