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

import enum
import re
import uuid as sys_uuid

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.dm import types_network
from restalchemy.storage import exceptions as ra_storage_exc
from restalchemy.storage.sql import orm

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.common import utils as u
from exordos_core.quota.dm.models import QuotaModelMixin
from exordos_core.secret import utils as su


class LBStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class LBTypeCoreKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "core"

    cpu = properties.property(types.Integer(min_value=1, max_value=128), default=1)
    ram = properties.property(
        types.Integer(min_value=512, max_value=1024**3), default=512
    )
    disk_size = properties.property(
        # The original value was 8 but Libvirt on ZFS considers it as 10Gb.
        types.Integer(min_value=10, max_value=1024**3),
        default=10,
    )
    nodes_number = properties.property(
        types.Integer(min_value=1, max_value=16), default=1
    )


class LBTypeCoreAgentKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "core_agent"


class LB(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    QuotaModelMixin,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "net_lb"

    status = properties.property(
        types.Enum([status.value for status in LBStatus]),
        default=LBStatus.NEW.value,
    )
    ipsv4 = properties.property(
        types.TypedList(types.String(max_length=15)),
        default=lambda: [],
    )
    type = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(LBTypeCoreKind),
            types_dynamic.KindModelType(LBTypeCoreAgentKind),
        ),
        default=LBTypeCoreKind(),
        required=True,
    )

    def delete(self, session=None, **kwargs):
        u.remove_nested_dm(Vhost, "parent", self, session=session)
        u.remove_nested_dm(BackendPool, "parent", self, session=session)
        return super().delete(session=session, **kwargs)


class ChildModel(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    ua_models.TargetResourceMixin,
    orm.SQLStorableMixin,
):
    parent = relationships.relationship(LB, required=True, read_only=True)

    def touch_parent(self, session=None):
        # Now we enforce dataplane updates via parent model, so we don't need
        #  to implement explicit child entities' resources on dataplane level
        # TODO: optimize and bump only updated_at
        self.parent.update(force=True)

    def insert(self, session=None):
        super().insert(session=session)
        self.touch_parent(session=session)

    def update(self, session=None, force=False):
        super().update(session=session, force=force)
        self.touch_parent(session=session)

    def delete(self, session=None, **kwargs):
        res = super().delete(session=session, **kwargs)
        self.touch_parent(session=session)
        return res


class BackendHostKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "host"

    host = properties.property(
        types.String(min_length=1, max_length=260), required=True
    )
    port = properties.property(types.Integer(min_value=80, max_value=65535), default=80)
    weight = properties.property(types.Integer(min_value=0, max_value=1000), default=1)


class BalanceTypes(str, enum.Enum):
    RR = "roundrobin"


class BackendPool(ChildModel):
    __tablename__ = "net_lb_backendpools"

    status = properties.property(
        types.Enum([status.value for status in LBStatus]),
        default=LBStatus.ACTIVE.value,
    )
    endpoints = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(BackendHostKind),
            )
        ),
        required=True,
    )
    balance = properties.property(
        types.Enum([i.value for i in BalanceTypes]),
        default=BalanceTypes.RR.value,
    )

    def validate(self):
        if len(self.endpoints) == 0:
            raise ex_exceptions.ValidateException(err="No endpoints defined")

    def delete(self, session=None, **kwargs):
        # TODO: optimize this "foreign key" check
        for v in Vhost.objects.get_all(filters={"parent": dm_filters.EQ(self.parent)}):
            for r in Route.objects.get_all(filters={"parent": dm_filters.EQ(v.uuid)}):
                for action in r.condition.actions:
                    if action.kind == "backend" and action.pool.uuid == self.uuid:
                        raise ex_exceptions.ValidateException(
                            err="Backend pool in use, remove route first"
                        )
        return super().delete(session=session, **kwargs)


class CertKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "raw"

    crt = properties.property(types.String(min_length=1, max_length=100000))
    key = properties.property(types.String(min_length=1, max_length=100000))


class Protocol(str, enum.Enum):
    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"
    UDP = "udp"


PROTOCOL_CONFLICT_MAPPING = {
    "http": ("https", "tcp"),
    "https": ("http", "tcp"),
    "tcp": ("http", "https", "tcp"),
    "udp": ("udp",),
}


class LBExtSourceSSHKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "ssh_forward"

    host = properties.property(
        types.String(min_length=1, max_length=260), required=True
    )
    port = properties.property(types.Integer(min_value=1, max_value=65535), default=22)
    user = properties.property(types.String(min_length=1, max_length=32), required=True)
    private_key = properties.property(
        types.String(min_length=1, max_length=32768),
        required=True,
    )


class Vhost(ChildModel):
    __tablename__ = "net_lb_vhosts"

    enabled = properties.property(types.Boolean(), default=True)
    status = properties.property(
        types.Enum([status.value for status in LBStatus]),
        default=LBStatus.ACTIVE.value,
    )
    protocol = properties.property(
        types.Enum([proto.value for proto in Protocol]),
        default=Protocol.HTTP.value,
        read_only=True,
    )
    # TODO: should we allow ports less than 80? If yes - think how not to
    #  collide with VM internal services like ssh
    port = properties.property(types.Integer(min_value=80, max_value=65535), default=80)
    domains = properties.property(
        types.AllowNone(types.TypedList(types.String(min_length=1, max_length=255))),
        default=None,
    )
    cert = properties.property(
        types.AllowNone(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(CertKind),
            )
        ),
        default=None,
    )
    external_sources = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(LBExtSourceSSHKind),
            )
        ),
        default=lambda: [],
        required=True,
    )
    proxy_protocol_from = properties.property(
        types.AllowNone(types_network.IpWithMask()), default=None
    )

    def _validate(self, check_all=False):
        if self.proxy_protocol_from and self.protocol == Protocol.UDP.value:
            raise ex_exceptions.ValidateException(
                err="'proxy_protocol_from' is only supported for 'tcp'-based protocols."
            )
        if self.protocol.startswith("http"):
            if not self.domains:
                raise ex_exceptions.ValidateException(
                    err="L7 protocols have to get at least one value in `domains` field."
                )
            if self.protocol == Protocol.HTTPS.value:
                if self.cert is None:
                    raise ex_exceptions.ValidateException(
                        err="Certificate is required for HTTPS protocol."
                    )
                try:
                    x509.load_pem_x509_certificate(
                        self.cert.crt.encode("utf-8"), default_backend()
                    )
                except ValueError:
                    raise ex_exceptions.ValidateException(err="cert=crt is invalid.")
                try:
                    serialization.load_pem_private_key(
                        self.cert.key.encode("utf-8"),
                        password=None,
                        backend=default_backend(),
                    )
                except ValueError:
                    raise ex_exceptions.ValidateException(err="cert=key is invalid.")
        else:
            if self.domains:
                raise ex_exceptions.ValidateException(
                    err="L4 protocols don't support `domains` field yet."
                )
            if self.cert:
                raise ex_exceptions.ValidateException(
                    err="L4 protocols don't support `cert` field yet."
                )
        fltr = {
            "uuid": dm_filters.NE(self.uuid),
            "parent": dm_filters.EQ(self.parent),
            "port": dm_filters.EQ(self.port),
            "protocol": dm_filters.In(PROTOCOL_CONFLICT_MAPPING[self.protocol]),
        }
        for vhost in Vhost.objects.get_all(filters=fltr, limit=1):
            raise ex_exceptions.ValidateException(
                err="Protocol+port pair conflicts with another vhost %s."
                % str(vhost.uuid)
            )
        for source in self.external_sources:
            if source.kind == "ssh_forward" and not su.validate_openssh_key(
                source.private_key
            ):
                raise ex_exceptions.ValidateException(
                    err="Private key for external_source with type ssh_forward is invalid."
                )

    def insert(self, session=None):
        self._validate()
        super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate()
        super().update(session=session, force=force)

    def delete(self, session=None, **kwargs):
        u.remove_nested_dm(Route, "parent", self, session=session)
        return super().delete(session=session, **kwargs)


class PathType(types.BaseCompiledRegExpTypeFromAttr):
    pattern = re.compile(r"^\/(.*)$")


class AbstractBackendProtoKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    pass


class BackendProtoHTTPKind(AbstractBackendProtoKind):
    KIND = "http"


class BackendProtoHTTPSKind(AbstractBackendProtoKind):
    KIND = "https"
    verify = properties.property(types.Boolean(), default=True)


class AbstractRuleKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    # TODO: there may be `condition` field in future to select actions
    pass


class RuleRawBackendKind(AbstractRuleKind):
    KIND = "backend"

    pool = relationships.relationship(BackendPool, required=True)


class RuleHTTPBackendKind(AbstractRuleKind):
    KIND = "backend"

    pool = relationships.relationship(BackendPool, required=True)
    protocol = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(BackendProtoHTTPKind),
            types_dynamic.KindModelType(BackendProtoHTTPSKind),
        ),
        default=BackendProtoHTTPKind(),
    )


class RuleRedirectKind(AbstractRuleKind):
    KIND = "redirect"
    url = properties.property(types.Url(), required=True)
    code = properties.property(types.Integer(min_value=300, max_value=399), default=301)


class AllowedPathType(types.BaseCompiledRegExpTypeFromAttr):
    # /var/www/* with restriction of `..`
    pattern = re.compile(r"^(?!.*\/\.\.(?:\/.*|$))\/var\/www\/.*$")


class RuleStaticKind(AbstractRuleKind):
    KIND = "local_dir"
    path = properties.property(AllowedPathType(), required=True)
    is_spa = properties.property(types.Boolean(), default=True)


class ArchivedTarUrl(types.Url):
    def validate(self, value):
        if not super().validate(value):
            return False
        return value.endswith("tar.gz") or value.endswith("tar.zst")


class RuleStaticDownloadKind(AbstractRuleKind):
    KIND = "local_dir_download"
    url = properties.property(ArchivedTarUrl(), required=True)
    is_spa = properties.property(types.Boolean(), default=True)


class AbstractModifierKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    pass


class Headers(str, enum.Enum):
    HOST = "Host"
    X_FORWARDED_FOR = "X-Forwarded-For"
    X_FORWARDED_PORT = "X-Forwarded-Port"
    X_FORWARDED_PROTO = "X-Forwarded-Proto"
    X_FORWARDED_PREFIX = "X-Forwarded-Prefix"


class ModifierAutoHeaderKind(AbstractModifierKind):
    KIND = "auto_header"
    headers = properties.property(
        types.TypedList(types.Enum([h.value for h in Headers])),
        default=lambda: [
            Headers.HOST.value,
            Headers.X_FORWARDED_FOR.value,
            Headers.X_FORWARDED_PORT.value,
            Headers.X_FORWARDED_PROTO.value,
            Headers.X_FORWARDED_PREFIX.value,
        ],
    )


# prefix header can't be added automacitally for regex-based rules
class ModifierAutoHeaderForRegexKind(AbstractModifierKind):
    KIND = "auto_header"
    headers = properties.property(
        types.TypedList(
            types.Enum(
                [
                    Headers.HOST.value,
                    Headers.X_FORWARDED_FOR.value,
                    Headers.X_FORWARDED_PORT.value,
                    Headers.X_FORWARDED_PROTO.value,
                ]
            )
        ),
        default=lambda: [
            Headers.HOST.value,
            Headers.X_FORWARDED_FOR.value,
            Headers.X_FORWARDED_PORT.value,
            Headers.X_FORWARDED_PROTO.value,
        ],
    )


class ModifierInsertHeaderKind(AbstractModifierKind):
    KIND = "set_header"
    name = properties.property(types.String(min_length=1, max_length=100))
    value = properties.property(types.String(min_length=0, max_length=1000))


class Regex(types.String):
    def __init__(self, **kwargs):
        """
        Regex type.
        """
        openapi_type = kwargs.pop("openapi_type", "string")
        openapi_format = kwargs.pop("openapi_format", "regex")
        super().__init__(
            openapi_type=openapi_type, openapi_format=openapi_format, **kwargs
        )

    def validate(self, value):
        result = super().validate(value)
        try:
            re.compile(value)
        except re.error:
            return False
        return result


class ModifierRewriteUrlKind(AbstractModifierKind):
    KIND = "rewrite_url"
    regex = properties.property(Regex(min_length=1, max_length=10000))
    replacement = properties.property(types.String(min_length=1, max_length=10000))


# class ConnectionUrlCodeKind(ConnectionUrlKind):
#     KIND = "url"

#     uri = properties.property(types.Url(), required=True)
#     code = properties.property(
#         types.Integer(min_value=100, max_value=599), default=200
#     )


class AbstractRouteCondKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    allowed_ips = properties.property(
        types.TypedList(types_network.IpWithMask()),
        default=lambda: [types_network.IpWithMask().from_simple_type("0.0.0.0/0")],
    )


class RouteRawConditionKind(AbstractRouteCondKind):
    KIND = "raw"
    actions = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(RuleRawBackendKind),
            )
        ),
        required=True,
    )


class AbstractHTTPRouteCondKind(AbstractRouteCondKind):
    value = properties.property(PathType(), required=True)
    # healthcheck = properties.property(
    #     types.AllowNone(
    #         types_dynamic.KindModelSelectorType(
    #             types_dynamic.KindModelType(ConnectionUrlCodeKind),
    #         ),
    #     ),
    #     default=None,
    # )
    actions = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(RuleHTTPBackendKind),
                types_dynamic.KindModelType(RuleRedirectKind),
                types_dynamic.KindModelType(RuleStaticKind),
                types_dynamic.KindModelType(RuleStaticDownloadKind),
            )
        ),
        required=True,
    )
    modifiers = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(ModifierAutoHeaderKind),
                types_dynamic.KindModelType(ModifierInsertHeaderKind),
                types_dynamic.KindModelType(ModifierRewriteUrlKind),
            )
        ),
        default=lambda: [],
    )

    def __init__(self, modifiers=None, **kwargs):
        if modifiers is None:
            modifiers = [ModifierAutoHeaderKind()]
        super().__init__(modifiers=modifiers, **kwargs)


class RoutePrefixConditionKind(AbstractHTTPRouteCondKind):
    KIND = "prefix"


class RouteExactConditionKind(AbstractHTTPRouteCondKind):
    KIND = "exact"


class RouteRegexConditionKind(AbstractHTTPRouteCondKind):
    KIND = "regex"

    value = properties.property(Regex(), required=True)
    modifiers = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(ModifierAutoHeaderForRegexKind),
                types_dynamic.KindModelType(ModifierInsertHeaderKind),
                types_dynamic.KindModelType(ModifierRewriteUrlKind),
            )
        ),
        default=lambda: [],
    )

    def __init__(self, modifiers=None, **kwargs):
        if modifiers is None:
            modifiers = [ModifierAutoHeaderForRegexKind()]
        super().__init__(modifiers=modifiers, **kwargs)


class Route(ChildModel):
    __tablename__ = "net_lb_vhosts_routes"

    parent = relationships.relationship(Vhost, required=True, read_only=True)
    enabled = properties.property(types.Boolean(), default=True)
    status = properties.property(
        types.Enum([status.value for status in LBStatus]),
        default=LBStatus.ACTIVE.value,
    )
    condition = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(RoutePrefixConditionKind),
            types_dynamic.KindModelType(RouteExactConditionKind),
            types_dynamic.KindModelType(RouteRegexConditionKind),
            types_dynamic.KindModelType(RouteRawConditionKind),
        ),
        required=True,
    )

    def _validate(self, check_all=False):
        if self.parent.protocol.startswith("http"):
            if self.condition.kind == RouteRawConditionKind.KIND:
                raise ex_exceptions.ValidateException(
                    err="L7 protocols can't have `raw` routes."
                )
        else:
            if self.condition.kind != RouteRawConditionKind.KIND:
                raise ex_exceptions.ValidateException(
                    err="L4 protocols can have only `raw` routes."
                )

    def insert(self, session=None):
        self._validate()
        super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate()
        super().update(session=session, force=force)


# ---------------------------------------------------------------------------
# Border (realm egress/ingress gateway) -- a universal-agent capability.
# Deployment shapes mirror the LB above:
#   * node set          -> border_node pinned to that node's agent
#                          (distributed egress, e.g. a managed realm node);
#   * type core_agent   -> border_agent on the core node's agent (step 0);
#   * type core         -> a dedicated small VM gateway the platform
#                          provisions itself (small installations that
#                          should not turn the core node into the gateway).
# `node` wins over `type` (an explicit pin is the most specific intent).
# ---------------------------------------------------------------------------


class BorderTypeCoreAgentKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    KIND = "core_agent"


class BorderTypeCoreKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    """A dedicated VM-based border (calque of LBTypeCoreKind).

    A border is a single gateway; there is no nodes_number — an HA pair
    would need VRRP/conntrack sync, which this deliberately does not claim.
    The image must ship the universal agent with BorderCapabilityDriver.
    """

    KIND = "core"

    cpu = properties.property(types.Integer(min_value=1, max_value=128), default=1)
    ram = properties.property(
        types.Integer(min_value=512, max_value=1024**3), default=512
    )
    disk_size = properties.property(
        types.Integer(min_value=10, max_value=1024**3),
        default=10,
    )


class Border(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    ua_models.TargetResourceMixin,
):
    __tablename__ = "net_border"

    status = properties.property(
        types.Enum([status.value for status in LBStatus]),
        default=LBStatus.NEW.value,
    )
    # Optional target compute node. When set, the border capability is
    # scheduled to that node's agent (border_node / distributed egress, e.g. a
    # managed realm node) and `type` is ignored.
    node = properties.property(types.AllowNone(types.UUID()), default=None)
    type = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(BorderTypeCoreAgentKind),
            types_dynamic.KindModelType(BorderTypeCoreKind),
        ),
        default=BorderTypeCoreAgentKind(),
        required=True,
    )
    # Public IPv4s of a `core` (VM) border once its node is up — the
    # entrypoint consumers point DNAT clients at (like LB.ipsv4).
    ipsv4 = properties.property(
        types.TypedList(types.String(max_length=15)),
        default=lambda: [],
    )
    # Inline NAT rules, reconciled as part of the Border resource:
    #   snat_rules: [{source_cidr, mode: "masquerade"|"snat", snat_to}]
    #   forwards:   [{proto: "tcp"|"udp", public_ip, listen_port, to_host,
    #                 to_port}]
    snat_rules = properties.property(types.List(), default=lambda: [])
    forwards = properties.property(types.List(), default=lambda: [])


# --- Catalog: reference objects ----------------------------------------------
# Standalone, named definitions that ports and NFs cite instead of inlining
# (the firewall-vendor "objects" concept; AWS Elastic IP / GCP Address). Own
# lifecycle, referenced by uuid, shareable, referential integrity on delete.

FILTER_DIRECTIONS = ("ingress", "egress")
FILTER_PROTOCOLS = ("tcp", "udp", "icmp", "any")


def _ports_of(project_id):
    """Every port of this project — what a reference check has to walk.

    Scoped, because an unscoped `get_all()` walks every port in the
    installation on each delete of a group, an address or a function. Scoping
    it is also more correct than it looks: `validate_reference` already
    refuses a citation that reaches into another project, so a port that
    could hold this object is a port of the same project. A legacy row from
    before that check no longer blocks the owner's delete for ever — and if
    one exists, it compiles to deny-all rather than to something open.
    """
    from exordos_core.compute.dm import models as compute_models

    return compute_models.Port.objects.get_all(
        filters={"project_id": dm_filters.EQ(project_id)}
    )


def validate_reference(project_id, model, ref, field: str) -> None:
    """Refuse a reference that reaches into another project.

    Every SDN object cites the others by bare uuid — a network its
    functions, a port its groups, a function the next function — and none
    of those citations used to be checked. Two things followed: a caller
    could attach an object belonging to somebody else (and, through the
    compiler, have their hypervisor serve it), and the owner could no
    longer delete their own object, because referential integrity scans
    every project and answered 409 forever.

    A reference to something that does not exist is refused too. The
    compiler would only log and skip it, which reads to the caller as a
    function that is attached and simply does nothing.
    """
    if not ref:
        return
    try:
        sys_uuid.UUID(str(ref))
    except ValueError:
        # Not a reference at all (a `fip` may carry a literal IP).
        return
    target = model.objects.get_one_or_none(filters={"uuid": dm_filters.EQ(ref)})
    if target is None:
        raise ex_exceptions.ValidateException(
            err=f"{field} references {ref}, which does not exist"
        )
    if str(target.project_id) != str(project_id):
        raise ex_exceptions.ValidateException(
            err=f"{field} references {ref}, which belongs to another project"
        )


def validate_filter_rules(rules, project_id=None, generated=False) -> None:
    """Validate an allow-list ruleset (`splitter`).

    One validator for both surfaces that produce these rules — a catalog
    security group and a splitter function — because they compile through
    the same path into the same OpenFlow matches on the hypervisor.

    ``project_id`` is the owner of the ruleset, so a rule that names an
    identity group can be held to the same boundary every other reference
    is. Without it the check was existence only, and identity bits are
    allocated **per network**: a rule naming a group on somebody else's
    network compiles to a bit that on the port's own network belongs to an
    entirely different group. Nothing crosses tenants at the packet level,
    but the caller is granted something other than what they asked for,
    which is the part that matters.

    ``generated`` says the ruleset is the compiler's own output rather than
    a caller's input, which is what makes `remote_identity` admissible: it
    is the number a group travels as on the wire, and the compiler publishes
    it in the `splitter` it generates so that a graph pointing back at one
    can be re-read. From a caller it is a way around the group reference
    itself — a bit named directly is a membership claim with no group to
    check the project of.
    """
    for rule in rules or []:
        if not isinstance(rule, dict):
            raise ex_exceptions.ValidateException(err="rule must be an object")
        if rule.get("direction") not in FILTER_DIRECTIONS:
            raise ex_exceptions.ValidateException(
                err=f"rule.direction must be one of {FILTER_DIRECTIONS}"
            )
        if rule.get("protocol", "any") not in FILTER_PROTOCOLS:
            raise ex_exceptions.ValidateException(
                err=f"rule.protocol must be one of {FILTER_PROTOCOLS}"
            )
        port = rule.get("port")
        if port is not None and not (0 < int(port) <= 65535):
            raise ex_exceptions.ValidateException(err="rule.port must be in 1..65535")
        identity = rule.get("remote_identity")
        if identity is not None:
            if not generated:
                raise ex_exceptions.ValidateException(
                    err=(
                        "rule.remote_identity is what a group travels as on "
                        "the wire, not something to ask for: name the group "
                        "with rule.remote_group"
                    )
                )
            if isinstance(identity, bool) or not isinstance(identity, int):
                raise ex_exceptions.ValidateException(
                    err="rule.remote_identity must be an integer"
                )
            if not 0 < identity <= 0xFFFF:
                raise ex_exceptions.ValidateException(
                    err="rule.remote_identity is out of range"
                )
        remote_set = rule.get("remote_set")
        if remote_set is not None:
            # The other way a group is carried, and just as much a compiled
            # value: what an author names is the group, and which of the two
            # mechanisms answers for it is the compiler's decision.
            if not generated:
                raise ex_exceptions.ValidateException(
                    err=(
                        "rule.remote_set is how a group past its network's "
                        "identity bits is carried, not something to ask for: "
                        "name the group with rule.remote_group"
                    )
                )
            if isinstance(remote_set, bool) or not isinstance(remote_set, int):
                raise ex_exceptions.ValidateException(
                    err="rule.remote_set must be an integer"
                )
            if not 0 < remote_set <= 0x7FFFFFFF:
                raise ex_exceptions.ValidateException(
                    err="rule.remote_set is out of range"
                )
        group = rule.get("remote_group")
        if group is not None:
            # A group names its members, and what a packet carries is its
            # sender's identity — so a rule can ask "from that group" and
            # cannot ask "to that group": on the way out there is nothing in
            # the packet to compare against. Refused here rather than
            # compiled into a match that would grant everything or nothing.
            if rule.get("direction") != "ingress":
                raise ex_exceptions.ValidateException(
                    err="rule.remote_group is meaningful on ingress rules only"
                )
            if (
                IdentityGroup.objects.get_one_or_none(
                    filters={"uuid": dm_filters.EQ(group)}
                )
                is None
            ):
                raise ex_exceptions.ValidateException(
                    err="rule.remote_group names no identity group"
                )
            if project_id is not None:
                validate_reference(
                    project_id, IdentityGroup, group, "rule.remote_group"
                )
        remote_ip = rule.get("remote_ip")
        if remote_ip is not None and not u.is_cidr(remote_ip):
            # The compiler passes this through to the agent, which builds an
            # OpenFlow match out of it — where a comma starts another field.
            # Only an address or a prefix may get that far.
            raise ex_exceptions.ValidateException(
                err="rule.remote_ip must be an IP address or CIDR"
            )


class IdentityGroup(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """A name for a set of workloads, and a number the fabric can carry.

    What a port *is*, as opposed to what it may do: ports join a group, and
    a security-group rule names the group instead of listing the addresses
    of whoever is in it. The number is what travels — the low 16 bits of the
    skb mark on a hypervisor, VXLAN-GBP's 16 bits between them — so nothing
    has to distribute the membership, and a guest booting or dying writes to
    no other host.

    Allocated once and never reused while the group exists: a number that
    changed hands would silently hand one workload's access to another.
    """

    __tablename__ = "net_identity_groups"

    # Groups belong to a network, as tags do to a VPC: what a packet carries
    # is a *set* of bits, so the same bit means different groups on different
    # networks — which is safe exactly because a packet never crosses from
    # one to another. (When inter-network routing arrives, either the mark
    # does not cross the router or this becomes a set id; see
    # docs/network/security-group-remote-access.md.)
    network = properties.property(types.UUID(), required=True)
    # One bit of the mark, so a port can be in several groups at once and a
    # rule matches its bit without caring what else the sender is. Zero is
    # "unidentified" and matches no group rule — the fail-closed answer for
    # a port with no identity at all.
    #
    # None once the network's bits are gone: that group is carried the other
    # way, as a set of its members' addresses (`conj_id` below). Which of
    # the two a group gets is settled when it is created and never changes,
    # because the number a rule was compiled against is what the fabric
    # matches on.
    identity = properties.property(
        types.AllowNone(types.Integer(min_value=1, max_value=0x8000)),
        default=None,
    )
    # The other half of the hybrid: a group past its network's bit budget is
    # compiled as an OVN-style address set — the members' addresses on one
    # side of a conjunctive match, the rules naming the group on the other,
    # joined on this number. It is what a host uses to tie the two halves
    # together, so it has to be unique across the installation: every VNI's
    # guests share one `br-int`, and two sets meeting under one number would
    # be one set admitting both memberships.
    conj_id = properties.property(
        types.AllowNone(types.Integer(min_value=1, max_value=0x7FFFFFFF)),
        default=None,
    )

    # Sixteen bits, and a subnet's own default group takes one like any
    # other — so a network with two subnets has fourteen left for groups an
    # operator names. Past that a group is still made, and is carried as an
    # address set instead: the ceiling is on what rides in the packet, not
    # on how many groups a network may have.
    IDENTITY_BITS = 16

    @classmethod
    def allocate(cls, network, session=None) -> dict:
        """What this group is carried by: a bit if the network has one left.

        `{"identity": bit}` while the sixteen last, `{"conj_id": n}` after
        that — the same group, the same syntax in a rule, a different
        mechanism on the host. Which one is the compiler's decision and not
        the author's, which is what lets the ceiling be lifted without a
        network's existing rules meaning anything different.

        A group's answer is fixed at creation because a rule elsewhere is
        compiled against it. Moving one from a set to a freed bit would be
        a rewrite of everything naming it, and a window in which the rule
        matches a number nobody stamps yet.
        """
        try:
            return {"identity": cls.allocate_identity(network, session=session)}
        except ex_exceptions.ValidateException:
            return {"conj_id": cls.allocate_conj_id(session=session)}

    @classmethod
    def allocate_conj_id(cls, session=None) -> int:
        """The next free join number, counted over the whole installation.

        Not per network like a bit: bits are safe to reuse between networks
        because a packet never crosses from one to another, and a join
        number is not — the member half of the match lives on `br-int`,
        which every VNI on the host shares, so the same number in two
        networks would be one set with both memberships in it.

        Read-then-insert, so two creations at once can pick the same
        number and `UNIQUE (conj_id)` refuses the loser. That is the whole
        protection, and it is deliberately not repaired here: the insert
        that lost has aborted its transaction, so a retry in the same
        session would only fail differently. What the loser gets is a 409
        (`ConflictRecords`), which is an answer a caller can act on — and
        the one caller that is not a caller, the seed of a subnet's default
        group, takes the next number on its next pass.

        Handed out above the highest in use, so a freed number is not
        reused — unlike a bit, which is scarce enough that the lowest free
        one is worth finding. There are two billion of these and nothing
        depends on them being dense.
        """
        taken = [
            int(group.conj_id)
            for group in cls.objects.get_all(session=session)
            if group.conj_id is not None
        ]
        return max(taken) + 1 if taken else 1

    @classmethod
    def allocate_identity(cls, network, session=None) -> int:
        """The lowest free bit on this network, so a small one stays readable.

        Serialized on the **network row**, not on the group rows: scanning
        and then inserting is two steps, and two callers that scan together
        pick the same bit. Locking the groups looked like the fix and was
        not — `FOR UPDATE` over a set that is still empty locks nothing, so
        exactly the first two groups of a network (the common case: two
        subnets seeding their defaults at once) both chose bit 0 and the
        loser died on `UNIQUE(network, identity)` as a 500. The network row
        always exists, which is what makes it a serialization point; the same
        shape as `Address.insert` locking the subnet it allocates from.
        """
        from exordos_core.compute.dm import models as compute_models

        compute_models.Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(network)},
            session=session,
            locked=True,
        )
        taken = {
            int(group.identity)
            for group in cls.objects.get_all(
                filters={"network": dm_filters.EQ(network)},
                session=session,
            )
            if group.identity is not None
        }
        for bit in range(cls.IDENTITY_BITS):
            if (1 << bit) not in taken:
                return 1 << bit
        raise ex_exceptions.ValidateException(
            err=(
                "network %s already has %d identity groups, which is all the "
                "bits a packet carries" % (network, cls.IDENTITY_BITS)
            )
        )

    def insert(self, session=None):
        # A group's bit is taken out of its **network's** sixteen, so
        # creating one spends something that belongs to whoever owns the
        # network. Unchecked, that is a denial of service with a long reach:
        # sixteen groups on somebody else's network and their next subnet is
        # refused outright, while the subnets they already have compile to
        # deny-all as soon as a default group cannot be seeded for them.
        #
        # A public network is no exception, and this is the one place that
        # differs from the address ledger: publishing a network says others
        # may *allocate* in it, and a bit is not an address — there are
        # sixteen of them for the whole network, and they are the owner's to
        # spend.
        from exordos_core.compute.dm import models as compute_models

        validate_reference(
            self.project_id, compute_models.Network, self.network, "network"
        )
        return super().insert(session=session)

    def delete(self, session=None, **kwargs):
        # Referential integrity, as for a security group: a rule that names a
        # group which vanished would compile to a match on a number nobody
        # stamps, and the port it protects would quietly stop being
        # reachable by the workloads it was supposed to admit.
        for group in SecurityGroup.objects.get_all():
            for rule in group.rules or []:
                if str(rule.get("remote_group") or "") == str(self.uuid):
                    raise ra_storage_exc.ConflictRecords(
                        model="IdentityGroup",
                        msg=f"still named by security group {group.uuid}",
                    )
        # And the members, which a security group checks and this did not:
        # deleting a group out from under its ports takes their membership
        # away silently, and frees the bit for the next group to take —
        # after which the rules that admitted them admit somebody else.
        for port in _ports_of(self.project_id):
            config = port.config
            if getattr(config, "KIND", None) != "simple":
                continue
            if any(
                str(group) == str(self.uuid)
                for group in getattr(config, "identity_groups", None) or []
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="IdentityGroup",
                    msg=f"still joined by port {port.uuid}",
                )
        return super().delete(session=session, **kwargs)


class SecurityGroup(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """A named, shareable ruleset.

    Referenced by ports (the ``simple`` port kind's ``security_groups`` list,
    ENI-style) and lowered by the compiler into a per-port ``splitter`` NF.
    ``rules`` is an allow-list of constrained-schema matches, and it is what
    a group is: the pipeline it compiles to is a conntrack one, so
    "established/related -> allow" and "everything else -> drop" are
    properties of the model rather than knobs on a group.
    """

    __tablename__ = "net_security_groups"

    # rules: [{direction: ingress|egress, protocol: tcp|udp|icmp|any,
    #          port?: int, remote_ip?: cidr}] — plain JSONB[] like
    # net_border.snat_rules; validated in `validate`.
    rules = properties.property(types.List(), default=lambda: [])

    def _validate(self):
        # Named with a leading underscore (as Vhost._validate) so restalchemy
        # does not auto-invoke it on dump_to_simple_view; it runs server-side
        # in insert/update and maps to a 400 (ValidateException).
        validate_filter_rules(self.rules, project_id=self.project_id)

    def insert(self, session=None):
        self._validate()
        return super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate()
        return super().update(session=session, force=force)

    def delete(self, session=None, **kwargs):
        # Referential integrity: a group still referenced by a port must
        # not vanish — the compiler would silently emit an empty allow-list,
        # which the agent applies as "no filtering at all".
        for port in _ports_of(self.project_id):
            config = port.config
            if getattr(config, "KIND", None) != "simple":
                continue
            if any(str(sg) == str(self.uuid) for sg in config.security_groups or []):
                raise ra_storage_exc.ConflictRecords(
                    model="SecurityGroup",
                    msg=f"still referenced by port {port.uuid}",
                )
        return super().delete(session=session, **kwargs)


class AddressAllocation(str, enum.Enum):
    # Holding the IP against its subnet's pool, or handed back to it. A freed
    # row is a receipt, not a reservation: the address it names is available
    # again the moment it is freed, which is why the uniqueness that keeps
    # two rows off one IP is scoped to the reserved ones.
    RESERVED = "reserved"
    FREED = "freed"


class AddressOrigin(str, enum.Enum):
    AUTO = "auto"
    EXPLICIT = "explicit"
    FLOATING = "floating"


class Address(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    # Declarable from an element manifest as
    # `$core.network.catalog.addresses`; the core agent reads the model
    # through this mixin.
    ua_models.ResourceMixin,
):
    """An allocated address — the DB-backed IPAM ledger.

    Exclusive allocation (``UNIQUE(subnet, address)`` over the reserved rows)
    but shared reference: ``allocation`` (``reserved``/``freed``) says whether
    the address is held against its subnet's pool, and ``association`` says
    which port is using it right now — the Elastic-IP model, where the two
    have independent lifecycles. ``owner_port`` marks a port-owned address
    (allocated for a port, dies with it) as opposed to a standalone one a
    caller reserved and can move between ports.

    The two are what the allocator reads: ``_used_addresses`` counts the
    reserved rows and nothing else, so freeing an address really does hand
    it back, and ``association`` is set by the compiler when a port starts
    using one — which is what lets the model refuse to release, delete, or
    re-cite an address a guest is answering on.
    """

    __tablename__ = "net_addresses"

    subnet = properties.property(types.UUID(), required=True)
    # Optional on input: omit it and the address is IPAM-allocated from the
    # subnet (origin=auto); provide it for an explicit reservation.
    address = properties.property(
        types.AllowNone(types.String(min_length=2, max_length=45)),
        default=None,
    )
    allocation = properties.property(
        types.Enum([a.value for a in AddressAllocation]),
        default=AddressAllocation.RESERVED.value,
    )
    origin = properties.property(
        types.Enum([o.value for o in AddressOrigin]),
        default=AddressOrigin.EXPLICIT.value,
    )
    owner_port = properties.property(types.AllowNone(types.UUID()), default=None)
    association = properties.property(types.AllowNone(types.UUID()), default=None)

    def _reserved_addresses(self, subnet):
        # Never hand out the network address, broadcast, or the gateways
        # (subnet.routers[*].via) — this is what the in-memory Ipam gets
        # wrong today (it allocates 10.42.0.0). ``routers`` is a jsonfield
        # list of {"to", "via"} dicts.
        reserved = {str(subnet.cidr.network), str(subnet.cidr.broadcast)}
        for router in subnet.routers or []:
            via = router.get("via") if isinstance(router, dict) else None
            if via is not None:
                reserved.add(str(via))
        return reserved

    def _used_addresses(self):
        # Union of the catalog ledger and the legacy compute_ports column, so
        # the two allocators (this one and the reconcile Ipam) never collide
        # while ports still carry their address in their own column.
        #
        # Only the *reserved* rows: a freed one is the record of an address
        # that was handed back, and counting it would mean an address could
        # be released and never allocated again — which is releasing nothing.
        from exordos_core.compute.dm import models as compute_models

        used = {
            a.address
            for a in Address.objects.get_all(
                filters={
                    "subnet": dm_filters.EQ(self.subnet),
                    "allocation": dm_filters.EQ(AddressAllocation.RESERVED.value),
                }
            )
            if a.address is not None
        }
        for port in compute_models.Port.objects.get_all(
            filters={"subnet": dm_filters.EQ(self.subnet)}
        ):
            if port.ipv4 is not None:
                used.add(str(port.ipv4))
        return used

    def _resolve_subnet(self):
        """Fetch the target subnet, refusing another project's private one.

        An address row is owned by the caller (the controller forces
        ``project_id``), but its ``subnet`` may point anywhere. Without this a
        tenant who learns a subnet uuid could reserve — and thereby deny —
        addresses in another project's **private** subnet, a cross-tenant IPAM
        DoS (the port allocator and this ledger share a subnet-scoped pool and
        do not filter by project).

        Two things make a foreign subnet allocatable, and they are different
        sizes. Publishing the *network* opens every subnet of it — including
        the range the installation hands to nodes — and that was what a
        floating address for a realm required, because the address is
        allocated in the port's project while the pool belongs to the
        network's. Marking the *subnet* ``shared_pool`` says the one thing
        actually meant: addresses out of this pool are anybody's to take.
        """
        from exordos_core.compute.dm import models as compute_models

        subnet = compute_models.Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.subnet)}
        )
        if subnet is None:
            raise ex_exceptions.ValidateException(
                err=f"subnet {self.subnet} does not exist"
            )
        if str(subnet.project_id) != str(self.project_id) and not subnet.shared_pool:
            network = compute_models.Network.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(subnet.network)}
            )
            if network is None or network.access != "public":
                raise ex_exceptions.ValidateException(
                    err=(
                        f"subnet {self.subnet} belongs to another project "
                        "and is neither a shared pool nor on a public network"
                    )
                )
        return subnet

    def _allocate_free_address(self, subnet):
        blocked = self._used_addresses() | self._reserved_addresses(subnet)
        first = last = None
        if subnet.ip_range_pair is not None:
            first, last = subnet.ip_range_pair
        for host in subnet.cidr.iter_hosts():
            if first is not None and not (first <= host <= last):
                continue
            candidate = str(host)
            if candidate not in blocked:
                return candidate
        raise ex_exceptions.ValidateException(
            err=f"No free addresses left in subnet {self.subnet}"
        )

    def insert(self, session=None):
        subnet = self._resolve_subnet()
        if self.address is None:
            # Take a row lock on the subnet before scanning it for a free
            # address, so two concurrent auto-allocations in the same subnet
            # cannot read the same free-address set and both pick it. Without
            # it the race is only caught after the fact by
            # UNIQUE(subnet,address) — one side failing with a 409; the lock
            # turns it into a clean serial allocation, released at commit.
            from exordos_core.compute.dm import models as compute_models

            compute_models.Subnet.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.subnet)},
                session=session,
                locked=True,
            )
            self.address = self._allocate_free_address(subnet)
            # `origin` records *why* the address exists, not how its value
            # was chosen, and this is what keeps it true rather than merely
            # present: an address the allocator picked did not come from a
            # caller naming it, so leaving the `explicit` default on it
            # would say something false about who chose it. A `floating`
            # origin is left alone — the compiler allocating one for a port
            # is still why it exists.
            #
            # It decides nothing. Nothing branches on it, and the
            # once-only lookup for a port's floating address is by
            # `owner_port`, which is the ownership fact itself (see
            # `_public_address`). What it is for is answering "where did
            # this address come from" on a GET, which is worth having and
            # is only worth having while it is accurate.
            if self.origin == AddressOrigin.EXPLICIT.value:
                self.origin = AddressOrigin.AUTO.value
        return super().insert(session=session)

    def update(self, session=None, force=False):
        # The subnet gate must hold on update as well as insert. Without it a
        # caller allocates in its own subnet (which passes _resolve_subnet),
        # then PUTs ``{subnet: <foreign private uuid>}`` — re-opening the exact
        # cross-tenant IPAM DoS the insert path closes, because only insert and
        # delete were guarded.
        self._resolve_subnet()
        self._validate_allocation(session=session)
        return super().update(session=session, force=force)

    def _validate_allocation(self, session=None) -> None:
        """Guard the reserved <-> freed transition, both ways.

        Freeing is releasing: the address goes back to its subnet's pool and
        the next allocation may take it. So it cannot be done under a guest
        that is using the address (`association`) or under the port the
        address was allocated for (`owner_port`) — the same two facts that
        stop a delete, for the same reason, because freeing *is* the delete
        that keeps the receipt.

        Re-reserving is allocating: the address may have been handed to
        somebody else in between, and the reserved rows are the only ones
        uniqueness covers, so a re-reservation that skipped this check would
        be the one way to end up with two live claims on one IP.
        """
        stored = Address.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.uuid)}, session=session
        )
        if stored is None or stored.allocation == self.allocation:
            return
        if self.allocation == AddressAllocation.FREED.value:
            if self.association is not None:
                raise ex_exceptions.ValidateException(
                    err=f"address is still associated with {self.association}"
                )
            if self.owner_port is not None:
                raise ex_exceptions.ValidateException(
                    err=(
                        f"address was allocated for port {self.owner_port}; "
                        "it is released with the port"
                    )
                )
            return
        if self.address is not None and str(self.address) in self._used_addresses():
            raise ex_exceptions.ValidateException(
                err=(
                    f"address {self.address} was allocated to somebody else "
                    "while this one was freed"
                )
            )

    def delete(self, session=None, **kwargs):
        # Referential integrity (as SecurityGroup.delete): a port's `public`
        # slot cites the address by uuid, and the compiler that reads it only
        # skips what it cannot resolve — so deleting the object here would
        # take the guest's floating IP away without saying anything.
        for port in _ports_of(self.project_id):
            config = port.config
            if getattr(config, "KIND", None) != "simple":
                continue
            public = config.public or {}
            if isinstance(public, dict) and str(public.get("address")) == str(
                self.uuid
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Address",
                    msg=f"still the floating address of port {port.uuid}",
                )
        # Releasing an address that is still in use would hand it to the next
        # allocation while a port is still answering on it: allocation and
        # association have independent lifecycles, but you cannot release
        # what is associated.
        if self.association is not None:
            raise ra_storage_exc.ConflictRecords(
                model="Address",
                msg=f"still associated with {self.association}",
            )
        if self.owner_port is not None:
            raise ra_storage_exc.ConflictRecords(
                model="Address",
                msg=f"owned by port {self.owner_port}; delete the port instead",
            )
        return super().delete(session=session, **kwargs)


# --- Network functions -------------------------------------------------------


class NetworkFunctionKind(str, enum.Enum):
    # What a port composes...
    SPLITTER = "splitter"
    FIP = "fip"
    # ... and the services the installation runs for a network's guests.
    # They were config once — `[evpn]` options the compiler turned into
    # per-port descriptors nothing could see or override. As objects they
    # are attachable (a subnet's DHCP, a network's resolver and metadata
    # proxy), inspectable, and editable per network instead of per host.
    DHCP = "dhcp"
    DNS = "dns"
    PROXY = "proxy"


class NFProvenance(str, enum.Enum):
    # user-authored (editable) vs emitted by a simple port kind / stock set
    # (read-only via the API, managed by its generator).
    USER = "user"
    GENERATED = "generated"


class NetworkFunction(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """One typed thing done to a guest's traffic.

    The public surface is ``kind`` + ``config`` + ``provenance``; the
    intercept (how the fabric steals the traffic) and the realization
    (inline / local_daemon) are properties of the *kind*, resolved
    internally rather than asked for.

    A function is never made on its own: it exists because something that
    owns it does — a subnet's DHCP and default group, a network's resolver
    and metadata proxy, the expansion of a port. What a caller does with one
    is read it and edit it, which is the whole reason these are objects and
    not the ``[evpn]`` options they used to be.
    """

    __tablename__ = "net_nfs"

    # Kinds whose configuration is the installation's, not a caller's.
    #
    # The `proxy` function is the routing table of the only door an overlay
    # guest has. Its `forwards` and `ports` say which upstreams the
    # *hypervisor* will fetch on a guest's behalf — from the hypervisor's own
    # network, which is the management network the overlay exists to keep
    # guests out of. A tenant able to edit it points the host at anything the
    # host can reach, gets the answer relayed back into the guest, and needs
    # no other bug to do it: the function is seeded in the tenant's own
    # project, so it would otherwise be theirs to edit like any object of
    # theirs.
    #
    # So its contents come from the installation's configuration
    # (`[boot_api]`, `[evpn] proxy_forwards`) and it stays the
    # installation's: not creatable, not editable and not deletable through
    # the API, refreshed by the compiler on every pass. What a caller may
    # still do is switch it off for a port — that is the port's `dns`/`dhcp`
    # toggles and the port's business.
    PLATFORM_KINDS = frozenset({NetworkFunctionKind.PROXY.value})

    kind = properties.property(
        types.Enum([k.value for k in NetworkFunctionKind]),
        required=True,
    )
    config = properties.property(types.Dict(), default=lambda: {})
    provenance = properties.property(
        types.Enum([p.value for p in NFProvenance]),
        default=NFProvenance.USER.value,
    )
    # What a generated NF belongs to. A `simple` port owns the ones it
    # emits — read-only, edited through the port. A subnet owns
    # its DHCP and a network its resolver and metadata proxy: those are
    # seeded with the installation's defaults and then are the caller's to
    # edit, which is the whole point of them being objects.
    owner_port = properties.property(types.AllowNone(types.UUID()), default=None)
    owner_subnet = properties.property(types.AllowNone(types.UUID()), default=None)
    owner_network = properties.property(types.AllowNone(types.UUID()), default=None)
    # No status/facts field: nothing aggregates one, and a column that reads
    # `IDLE` however the function is actually doing tells the caller less
    # than an absent one.

    # Config keys each kind understands, with the type the compiler expects.
    # Validated server-side so a typo fails at 400 instead of silently
    # compiling into a data plane that does nothing.
    _CONFIG_SCHEMA = {
        # An allow-list and nothing else: what a group permits is the whole
        # of it, and "established is allowed, the rest is dropped" is the
        # pipeline it compiles to rather than a choice to be made per group.
        "splitter": {"rules": list},
        "fip": {"address": str},
        # `filename` is what a guest's iPXE ROM chain-loads; `lease_seconds`
        # overrides the responder's default lease.
        "dhcp": {"filename": str, "lease_seconds": int},
        # `forwarders` are this network's upstream resolvers (never a
        # host-wide union); `zone_suffix` names its internal zone;
        # `platform_names` are the names of the installation itself, which
        # resolve inside a tenant's namespace to the hypervisor serving it —
        # the only address of the platform a guest of an overlay can reach.
        "dns": {"forwarders": list, "zone_suffix": str, "platform_names": list},
        # Three kinds of grant, and the difference between them is who
        # checks the caller. `forwards` are prefixes of static things the
        # installation published — read-only, any guest. `ports` map a
        # platform port (boot, orchestration, status) to the core that
        # answers on it; those APIs authenticate nobody, so the proxy is
        # what holds a guest to its own business. `apis` are upstreams that
        # authenticate their own callers — a child realm calls its parent's
        # ecosystem with the realm's tokens, not as the node it runs on — so
        # they are relayed whole. Everything else is 404, never an open
        # relay.
        "proxy": {"forwards": list, "ports": dict, "apis": list},
    }

    def _validate_config(self):
        schema = self._CONFIG_SCHEMA.get(self.kind, {})
        for key, value in (self.config or {}).items():
            if key not in schema:
                raise ex_exceptions.ValidateException(
                    err=f"{self.kind} config does not accept {key!r} "
                    f"(known keys: {sorted(schema)})"
                )
            expected = schema[key]
            # bool is an int subclass; keep them apart so `port: true` fails.
            if expected is int and isinstance(value, bool):
                raise ex_exceptions.ValidateException(
                    err=f"config.{key} must be an integer"
                )
            if not isinstance(value, expected):
                raise ex_exceptions.ValidateException(
                    err=f"config.{key} must be of type {expected.__name__}"
                )
        if self.kind == NetworkFunctionKind.SPLITTER.value:
            validate_filter_rules(
                (self.config or {}).get("rules"),
                project_id=self.project_id,
                # A generated splitter is what the compiler published, and it
                # carries the wire form of what a rule named. A caller's is
                # not allowed to: `provenance` is read-only on the API, so
                # this cannot be claimed on the way in.
                generated=self.provenance == NFProvenance.GENERATED.value,
            )
        if self.kind == NetworkFunctionKind.FIP.value and not (self.config or {}).get(
            "address"
        ):
            raise ex_exceptions.ValidateException(
                err="fip requires config.address (an address-object uuid or a "
                "literal IP)"
            )

    def _validate(self):
        self._validate_config()
        # A `fip` cites a catalog address object (or carries a literal IP,
        # which resolves to no row and is left to the compiler).
        if self.kind == NetworkFunctionKind.FIP.value:
            validate_reference(
                self.project_id,
                Address,
                (self.config or {}).get("address"),
                "config.address",
            )

    def insert(self, session=None):
        self._validate()
        return super().insert(session=session)

    def _owned_by_a_generator(self) -> bool:
        """Is this function a port's expansion, rather than a service?

        A `simple` port's NFs restate the port: editing them directly would
        be edited away on the next compile, so they are read-only. The
        services a subnet and a network are seeded with are the opposite —
        they exist to be edited, and the compiler never rewrites one that
        is already there.
        """
        return (
            self.provenance == NFProvenance.GENERATED.value
            and self.owner_port is not None
        )

    def _managed_by_the_installation(self) -> bool:
        """Is this function the platform's own, whoever's project it is in?

        Unlike a generator's expansion, this does not depend on provenance:
        provenance records who last wrote a function, and the whole point
        here is that a caller writing one must not change who it belongs to.
        """
        return self.kind in self.PLATFORM_KINDS

    def _refuse_if_the_installations(self, verb: str) -> None:
        if self._managed_by_the_installation():
            raise ex_exceptions.ValidateException(
                err=(
                    f"{self.kind} functions are the installation's: they say "
                    "which upstreams a hypervisor will reach on a guest's "
                    f"behalf, so they come from its configuration and cannot "
                    f"be {verb}"
                )
            )

    def update(self, session=None, force=False):
        self._refuse_if_the_installations("edited")
        if self._owned_by_a_generator():
            raise ex_exceptions.ValidateException(
                err="generated NFs are read-only; edit them through their owner"
            )
        # A service the caller has changed is the caller's from now on —
        # provenance says who decides its contents, and it is no longer the
        # installation's default.
        if self.provenance == NFProvenance.GENERATED.value:
            self.provenance = NFProvenance.USER.value
        self._validate()
        return super().update(session=session, force=force)

    def delete(self, session=None, **kwargs):
        # Deleting it is editing it by another route: the compiler seeds a
        # fresh one from the configuration, so the only lasting effect would
        # be the window in between — during which the network's guests reach
        # nothing at all.
        self._refuse_if_the_installations("deleted")
        if self._owned_by_a_generator():
            raise ex_exceptions.ValidateException(
                err="generated NFs are read-only; remove their owner instead"
            )
        return super().delete(session=session, **kwargs)

    # --- generator-side API ------------------------------------------------
    # The compiler owns `provenance=generated` NFs: it publishes what a port
    # expanded into and collects them when the port changes or goes away. The
    # public update/delete above stay closed so callers edit the owner instead.

    def sync_generated(self, config: dict, session=None) -> None:
        """Bring a function back to what its generator says it should be.

        Provenance is restored along with the config: a platform function
        that a caller took over before the API was closed to them — or that
        a manifest wrote directly — is the installation's again the moment
        the installation writes it. Otherwise it would keep pointing the
        hypervisor wherever it was pointed, and nothing would ever look at
        it again.
        """
        if self.config == config and self.provenance == NFProvenance.GENERATED.value:
            return
        self.config = config
        self.provenance = NFProvenance.GENERATED.value
        super().update(session=session)

    def collect_generated(self, session=None) -> None:
        super().delete(session=session)
