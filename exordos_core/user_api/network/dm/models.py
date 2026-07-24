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
