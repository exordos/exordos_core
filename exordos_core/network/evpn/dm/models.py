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

import typing as tp

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types


class EvpnPort(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Per-port slice of the ovs_evpn data plane.

    Scheduled to the universal agent of the port's hypervisor node.
    The on-host driver renders it into an evpn_connector client config
    (resolving the ofport itself — ofport is a host-side fact) and a
    host-local DHCP record.
    """

    status = properties.property(types.String(max_length=32), default="NEW")
    mac = properties.property(types.AllowNone(types.Mac()), default=None)
    ipv4 = properties.property(
        types.AllowNone(types.String(max_length=15)), default=None
    )
    vni = properties.property(types.Integer(min_value=1, max_value=2**24 - 1))
    imp_rt = properties.property(types.TypedList(types.String(max_length=32)))
    exp_rt = properties.property(types.TypedList(types.String(max_length=32)))
    # DHCP record served by the host-local responder: routers,
    # dns_servers, mtu — computed from Subnet and the network driver
    # spec.
    dhcp = properties.property(types.Dict(), default=lambda: {})
    # Which functions serve this port: each entry is {kind, nf}. What
    # each of them answers with travels as its own
    # `evpn_nf` resource, so editing a network's resolver re-hashes that
    # one slice instead of every port on the host. A port whose network
    # carries no `dhcp` function gets no DHCP, and the `simple` kind's
    # per-port toggles have already been applied here.
    nfs = properties.property(types.List(), default=lambda: [])
    # Compiled allow-list from the port's security groups: each rule is
    # {proto, port?, dst?}. The on-host driver turns this into a per-guest
    # conntrack pipeline on br-int; an empty list is allow-all.
    security_rules = properties.property(types.List(), default=lambda: [])
    # Floating IPs bound to this port (`fip`): each entry is a 1:1 NAT
    # mapping {public} between a public-network address and the port's own
    # address. The on-host driver programs the NAT on the port's
    # hypervisor (path stays symmetric with DVR).
    fips = properties.property(types.List(), default=lambda: [])
    # Which group this port *is*, as a number the fabric can carry: stamped
    # on everything it sends, so a rule elsewhere can name the group instead
    # of listing the addresses of whoever happens to be in it today. Zero
    # means unidentified — nothing is stamped and no rule can name it.
    identity = properties.property(
        types.Integer(min_value=0, max_value=0xFFFF), default=0
    )
    # Whose guest this is. The host-local proxy is an overlay guest's only
    # door to the installation, so it is also the only place that can hold
    # the guest to asking about itself — which it cannot do without knowing
    # which uuid "itself" is.
    node = properties.property(types.AllowNone(types.UUID()), default=None)
    # Whether the guest may claim an address or MAC other than the ones it
    # was given. Enforced on br-int by the agent, because libvirt cannot
    # carry an nwfilter on an openvswitch virtualport — the
    # toggle existed on the port and reached no data plane at all.
    port_security = properties.property(types.Boolean(), default=True)

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_port"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(
            (
                "uuid",
                "mac",
                "ipv4",
                "vni",
                "imp_rt",
                "exp_rt",
                "dhcp",
                "nfs",
                "security_rules",
                "fips",
                "identity",
                "node",
                "port_security",
            )
        )


class EvpnHost(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Per-hypervisor EVPN stack parameters (one per node).

    uuid == node uuid. The on-host driver renders gobgpd session
    parameters from it; VNIs/VRFs are derived from the evpn_port
    resources of the same host.
    """

    status = properties.property(types.String(max_length=32), default="NEW")
    as_number = properties.property(types.Integer(min_value=1, max_value=2**32 - 1))
    rr_addresses = properties.property(types.TypedList(types.String(max_length=45)))
    # The addresses this installation has published: an overlay guest may
    # reach them, where the fabric guard drops everything else that is not
    # globally routable. Carried per host because the guard is installed per
    # host, and identical on all of them — the fact is the installation's.
    published_addresses = properties.property(
        types.TypedList(types.String(max_length=45)), default=list
    )

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_host"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(("uuid", "as_number", "rr_addresses", "published_addresses"))


class EvpnNf(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """One network function, compiled for one host.

    uuid == uuid5(nf, node), so a function shared by several hosts has one
    slice on each and they converge independently. The slice carries what
    the function answers with; who it answers for is each guest's own
    `evpn_port`. That split is what keeps an edit of a network's resolver
    from re-hashing — and reinstalling — every port on the hypervisor.
    """

    status = properties.property(types.String(max_length=32), default="NEW")
    # dhcp | dns | proxy: the services a host runs for a VNI's guests.
    kind = properties.property(types.String(max_length=32))
    vni = properties.property(types.Integer(min_value=1, max_value=2**24 - 1))
    config = properties.property(types.Dict(), default=lambda: {})

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_nf"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(("uuid", "kind", "vni", "config"))


class BgpRr(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Route reflector parameters (one per RR node, design decision 8).

    uuid == the RR node's agent uuid. The on-host driver renders a
    gobgpd reflector config: passive peering via gobgp dynamic-neighbors
    over the given underlay prefixes, so the RR never needs to know the
    hypervisor list.
    """

    status = properties.property(types.String(max_length=32), default="NEW")
    as_number = properties.property(types.Integer(min_value=1, max_value=2**32 - 1))
    peer_prefixes = properties.property(types.TypedList(types.String(max_length=45)))

    @classmethod
    def get_resource_kind(cls) -> str:
        return "bgp_rr"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(("uuid", "as_number", "peer_prefixes"))


class EvpnAddressSet(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """One identity group's membership, compiled for one host.

    uuid == uuid5(group, node), the same shape as `EvpnNf` and for the same
    reason. A group within its network's sixteen bits needs none of this —
    the packet carries who sent it. This is what a group past that budget
    is carried by instead: the addresses of its members, on every host with
    a guest on the network, joined to the rules that name the group by
    `conj_id` in a conjunctive match.

    A member joining or leaving rewrites this one small resource per host
    and re-hashes no port, which is the difference between an address set
    that is usable and the O(n·m) shape every cloud that tried it warns
    about.
    """

    status = properties.property(types.String(max_length=32), default="NEW")
    group = properties.property(types.AllowNone(types.UUID()), default=None)
    conj_id = properties.property(types.Integer(min_value=1, max_value=0x7FFFFFFF))
    addresses = properties.property(
        types.TypedList(types.String(max_length=45)), default=list
    )

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_address_set"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(("uuid", "group", "conj_id", "addresses"))
