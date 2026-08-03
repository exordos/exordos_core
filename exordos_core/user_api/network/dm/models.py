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
import ipaddress
import logging
import re
import typing as tp
import uuid

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from gcl_sdk.agents.universal.dm import models as ua_models
import netaddr
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
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.quota.dm.models import QuotaModelMixin
from exordos_core.secret import utils as su

LOG = logging.getLogger(__name__)


class LBStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class AbstractLBType(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    def validate_placement(self, project_id: uuid.UUID, session=None) -> None:
        pass


class LBTypeCoreKind(AbstractLBType):
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


class LBTypeCoreAgentKind(AbstractLBType):
    KIND = "core_agent"


class LBTypeExternalNodeKind(AbstractLBType):
    """An LB served by an existing compute node of the LB's project.

    The node must ship nginx and the universal agent with LBCapabilityDriver
    (paas_lb_node capability), e.g. a managed realm node.
    """

    KIND = "external_node"

    external_node = properties.property(types.UUID(), required=True)

    def validate_placement(self, project_id: uuid.UUID, session=None) -> None:
        # The LB dataplane runs as root on the node, so it may only be
        # placed on a node of the LB's own project.
        nodes = compute_models.Node.objects.get_all(
            filters={
                "uuid": dm_filters.EQ(self.external_node),
                "project_id": dm_filters.EQ(project_id),
            },
            limit=1,
            session=session,
        )
        if not nodes:
            raise ex_exceptions.ValidateException(
                err=f"Node {self.external_node} is not found in the LB project."
            )


# A `core_agent` LB renders into the core node's nginx, shared by the LBs of
# every project: a writable dir there, behind an auth check its owner picks,
# would reach the other projects' files. `core` LBs get VMs of their own.
WRITABLE_ON_CORE_AGENT = "A `core_agent` LB can't have a writable `local_dir`."
# Each LB node keeps `/var/www` on its own disk: a write would reach one.
WRITABLE_ON_MANY_NODES = "A writable `local_dir` needs an LB with a single node."


class LB(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    models.ModelWithTags,
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
            types_dynamic.KindModelType(LBTypeExternalNodeKind),
        ),
        default=LBTypeCoreKind(),
        required=True,
    )

    def insert(self, session=None):
        self.type.validate_placement(project_id=self.project_id, session=session)
        super().insert(session=session)

    def update(self, session=None, force=False):
        # Builders save the LB on every iteration; check the node only when
        # the placement changes, so a gone node doesn't block status updates.
        props = self.properties.properties
        if props["type"].is_dirty() or props["project_id"].is_dirty():
            self.type.validate_placement(project_id=self.project_id, session=session)
        if props["type"].is_dirty():
            err = self.writable_dir_error()
            if err and self._has_writable_routes(session=session):
                raise ex_exceptions.ValidateException(err=err)
        super().update(session=session, force=force)

    def writable_dir_error(self):
        """Why this LB can't serve a writable `local_dir`, if it can't."""
        if self.type.kind == LBTypeCoreAgentKind.KIND:
            return WRITABLE_ON_CORE_AGENT
        if self.type.kind == LBTypeCoreKind.KIND and self.type.nodes_number > 1:
            return WRITABLE_ON_MANY_NODES
        return None

    def _has_writable_routes(self, session=None):
        vhosts = Vhost.objects.get_all(
            filters={"parent": dm_filters.EQ(self.uuid)}, session=session
        )
        if not vhosts:
            return False
        routes = Route.objects.get_all(
            filters={"parent": dm_filters.In([v.uuid for v in vhosts])},
            session=session,
        )
        return any(r.is_writable() for r in routes)

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
                for action in (
                    *r.condition.actions,
                    *getattr(r.condition, "modifiers", ()),
                ):
                    if (
                        action.kind in ("backend", "auth_request")
                        and action.pool.uuid == self.uuid
                    ):
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


# `/<prefix>/<project_id>/...` is the layout the upload check authorizes.
WRITABLE_PREFIX = re.compile(r"/[^/]+/")


class DavMethods(str, enum.Enum):
    # COPY/MOVE take their target from the `Destination` header, which the
    # auth_request check never sees; MKCOL is covered by create_full_put_path.
    PUT = "PUT"
    DELETE = "DELETE"


class RuleStaticKind(AbstractRuleKind):
    KIND = "local_dir"
    path = properties.property(AllowedPathType(), required=True)
    is_spa = properties.property(types.Boolean(), default=True)
    # Makes the dir writable over WebDAV; guard it with an auth_request.
    dav_methods = properties.property(
        types.TypedList(types.Enum([m.value for m in DavMethods])),
        default=lambda: [],
    )


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


class ModifierSetHeaderKind(AbstractModifierKind):
    KIND = "set_header"
    name = properties.property(types.String(min_length=1, max_length=100))
    value = properties.property(types.String(min_length=0, max_length=1000))


class ModifierSetRespHeaderKind(AbstractModifierKind):
    KIND = "set_resp_header"
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


class AuthRequestPathType(types.BaseCompiledRegExpTypeFromAttr):
    # The data plane renders it unquoted into nginx config.
    pattern = re.compile(r"^/[A-Za-z0-9._~/-]*$")


class ModifierAuthRequestKind(AbstractModifierKind):
    """Authorize every request of the route by a subrequest to `pool`+`path`.

    The backend answers 2xx to allow and 401/403 to deny; it gets the
    original method, URI and `Authorization` but not the body. Core serves
    such a check for element repositories as the repository action
    `authorize_upload`.
    """

    KIND = "auth_request"
    pool = relationships.relationship(BackendPool, required=True)
    path = properties.property(AuthRequestPathType(), required=True)


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
                types_dynamic.KindModelType(ModifierSetHeaderKind),
                types_dynamic.KindModelType(ModifierSetRespHeaderKind),
                types_dynamic.KindModelType(ModifierRewriteUrlKind),
                types_dynamic.KindModelType(ModifierAuthRequestKind),
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
                types_dynamic.KindModelType(ModifierSetHeaderKind),
                types_dynamic.KindModelType(ModifierSetRespHeaderKind),
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

        auth_requests = [
            m
            for m in getattr(self.condition, "modifiers", ())
            if m.kind == ModifierAuthRequestKind.KIND
        ]
        # nginx refuses a second auth_request in one location.
        if len(auth_requests) > 1:
            raise ex_exceptions.ValidateException(
                err="A route can have only one `auth_request` modifier."
            )
        # The LB's data plane gets only its own pools.
        if auth_requests and auth_requests[0].pool.parent.uuid != (
            self.parent.parent.uuid
        ):
            raise ex_exceptions.ValidateException(
                err="The `auth_request` pool must belong to the route's LB."
            )
        if not self.is_writable():
            return
        if not auth_requests:
            raise ex_exceptions.ValidateException(
                err="A writable `local_dir` needs an `auth_request` modifier."
            )
        # nginx rewrites before the auth check, which authorizes the
        # original URI: a rewrite could move the write elsewhere.
        if any(m.kind == ModifierRewriteUrlKind.KIND for m in self.condition.modifiers):
            raise ex_exceptions.ValidateException(
                err="A writable `local_dir` can't have a `rewrite_url` modifier."
            )
        err = self.parent.parent.writable_dir_error()
        if err:
            raise ex_exceptions.ValidateException(err=err)
        # nginx aliases a slash-less prefix, so `/repo<x>/...` lands in
        # `<path>/<x>/...`, a path the upload check never sees; and the check
        # takes the segment after a one-segment prefix as the project.
        if self.condition.kind != RoutePrefixConditionKind.KIND or not (
            WRITABLE_PREFIX.fullmatch(self.condition.value)
        ):
            raise ex_exceptions.ValidateException(
                err="A writable `local_dir` needs a one-segment prefix route "
                "like `/repo/`."
            )

    def is_writable(self):
        return any(
            a.kind == RuleStaticKind.KIND and a.dav_methods
            for a in self.condition.actions
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
# Named objects that ports and NFs cite by uuid instead of inlining; deleting
# one that is still cited is refused.

FILTER_DIRECTIONS = ("ingress", "egress")
FILTER_PROTOCOLS = ("tcp", "udp", "icmp", "any")


def _ports_of(project_id):
    """Every port of this project — what a reference check has to walk.

    Project-scoped is enough: `validate_reference` refuses cross-project
    citations, so no port of another project can cite this object.
    """
    from exordos_core.compute.dm import models as compute_models

    return compute_models.Port.objects.get_all(
        filters={"project_id": dm_filters.EQ(project_id)}
    )


def validate_reference(project_id, model, ref, field: str) -> None:
    """Refuse a reference that reaches into another project, or nowhere.

    A cross-project citation would let a caller attach somebody else's
    object and block its owner's delete; a dangling one would compile to
    nothing while looking attached.
    """
    if not ref:
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


def is_floating_pool(subnet, network=None) -> bool:
    """Does this subnet hand out floating addresses?

    Only an address-only subnet (`placeable` false) of a network no tenant
    can make: a flat one (the operator's) or a public one (publishing needs
    `network.network.share`). The hypervisor answers for a floating address
    on its own segment and opens the fabric guard to it, so a tenant's own
    overlay subnet, whose CIDR is whatever its owner typed, cannot be one.
    """
    from exordos_core.compute.dm import models as compute_models

    if subnet is None or getattr(subnet, "placeable", True):
        return False
    if network is None:
        network = compute_models.Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet.network)}
        )
    if network is None:
        return False
    return not nc.is_overlay(network.driver_spec) or network.access == "public"


def may_allocate_in(project_id, subnet, network=None) -> bool:
    """May this project take addresses from this subnet?

    Its own, or another's that is a `shared_pool` or on a public network.
    Otherwise a tenant could exhaust another project's pool (cross-tenant
    IPAM DoS).
    """
    from exordos_core.compute.dm import models as compute_models

    if str(subnet.project_id) == str(project_id) or subnet.shared_pool:
        return True
    if network is None:
        network = compute_models.Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet.network)}
        )
    return network is not None and network.access == "public"


def validate_floating_pool(project_id, ref, field: str) -> None:
    """Refuse a floating-address source that is not a pool this project may use.

    Usable means what `Address` allows allocating in (`may_allocate_in`).
    """
    from exordos_core.compute.dm import models as compute_models

    subnet = compute_models.Subnet.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(ref)}
    )
    if subnet is None:
        raise ex_exceptions.ValidateException(
            err=f"{field} references {ref}, which does not exist"
        )
    network = compute_models.Network.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(subnet.network)}
    )
    if not is_floating_pool(subnet, network):
        raise ex_exceptions.ValidateException(
            err=(
                f"{field} references subnet {ref}, which is not a floating "
                "pool: an address-only subnet of a flat or public network"
            )
        )
    if not may_allocate_in(project_id, subnet, network):
        raise ex_exceptions.ValidateException(
            err=(
                f"{field} references subnet {ref}, which belongs to another "
                "project and is neither a shared pool nor on a public network"
            )
        )


FILTER_RULE_KEYS = frozenset(
    {"direction", "protocol", "port", "remote_ip", "remote_group"}
)


def validate_filter_rules(rules, project_id) -> None:
    """Validate an allow-list ruleset (security group or `splitter` NF).

    ``project_id`` holds `remote_group` to the owner's project: identity
    bits are per network, so a foreign group's bit would mean a different
    group on the port's own network.
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
        if port is not None and not (
            isinstance(port, int) and not isinstance(port, bool) and 0 < port <= 65535
        ):
            raise ex_exceptions.ValidateException(err="rule.port must be in 1..65535")
        unknown = set(rule) - FILTER_RULE_KEYS
        if unknown:
            # A key the compiler does not read is not a restriction: a
            # misspelt `remote` would compile to the rule without its peer,
            # an allow wider than the one that was written. A raw
            # `remote_identity` bit is a membership claim with no group to
            # check.
            raise ex_exceptions.ValidateException(
                err=f"rule has keys nothing reads: {sorted(unknown)}"
            )
        group = rule.get("remote_group")
        if group is not None:
            # A packet carries its sender's identity only, so "to that
            # group" has nothing to match on egress.
            if rule.get("direction") != "ingress":
                raise ex_exceptions.ValidateException(
                    err="rule.remote_group is meaningful on ingress rules only"
                )
            validate_reference(project_id, IdentityGroup, group, "rule.remote_group")
        remote_ip = rule.get("remote_ip")
        if remote_ip is not None:
            # Passed verbatim into an OpenFlow match, where a comma would
            # start another field; the data plane compiles IPv4 only, and
            # one rule it cannot compile fails the port's whole bundle.
            try:
                ipaddress.IPv4Network(str(remote_ip), strict=False)
            except ValueError:
                raise ex_exceptions.ValidateException(
                    err="rule.remote_ip must be an IPv4 address or CIDR"
                )


class IdentityGroup(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """A named set of ports, carried on the wire as one bit of a 16-bit mark.

    Security-group rules name the group instead of member addresses; the bit
    travels in the skb mark and VXLAN-GBP, so membership is never
    distributed. A bit is never reused while its group exists.
    """

    __tablename__ = "net_identity_groups"

    # Bits are per network: the same bit means different groups on
    # different networks, safe only while packets never cross networks.
    network = properties.property(types.UUID(), required=True)
    # One bit (a port may be in several groups); zero matches no group rule.
    # Immutable: compiled rules match on it.
    identity = properties.property(
        types.Integer(min_value=1, max_value=0x8000),
        required=True,
    )
    # Subnet default groups take bits too; past sixteen creation is refused.
    IDENTITY_BITS = 16

    @classmethod
    def allocate_identity(cls, network, session=None) -> int:
        """The lowest free bit on this network.

        Serialized by locking the network row: `FOR UPDATE` over the group
        rows locks nothing while the set is empty, so the first two groups
        would race to bit 0.
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
        }
        for bit in range(cls.IDENTITY_BITS):
            if (1 << bit) not in taken:
                return 1 << bit
        raise ex_exceptions.ValidateException(err=cls.out_of_bits(network))

    @classmethod
    def out_of_bits(cls, network) -> str:
        return (
            "network %s has no identity bit left: all %d a packet carries are "
            "taken by its identity groups, a subnet's default group included; "
            "delete a group to free one" % (network, cls.IDENTITY_BITS)
        )

    def insert(self, session=None):
        # The network's sixteen bits are its owner's, public network or not:
        # a stranger spending them would starve the owner's subnets of
        # default groups.
        from exordos_core.compute.dm import models as compute_models

        validate_reference(
            self.project_id, compute_models.Network, self.network, "network"
        )
        return super().insert(session=session)

    def delete(self, session=None, **kwargs):
        # A rule naming a vanished group would match a bit nobody stamps.
        for group in SecurityGroup.objects.get_all():
            for rule in group.rules or []:
                if str(rule.get("remote_group") or "") == str(self.uuid):
                    raise ra_storage_exc.ConflictRecords(
                        model="IdentityGroup",
                        msg=f"still named by security group {group.uuid}",
                    )
        # Members too: the freed bit would pass to the next group, and the
        # rules that admitted them would admit somebody else.
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
    """A named, shareable allow-list, cited by ports' ``security_groups``.

    Compiled into a per-port ``splitter`` NF on a conntrack pipeline, so
    established traffic is allowed and the rest dropped by construction.
    """

    __tablename__ = "net_security_groups"

    # rules: [{direction: ingress|egress, protocol: tcp|udp|icmp|any,
    #          port?: int, remote_ip?: cidr, remote_group?: uuid}]
    rules = properties.property(types.List(), default=lambda: [])

    def _validate(self):
        # Underscored so restalchemy does not call it on dump_to_simple_view.
        validate_filter_rules(self.rules, project_id=self.project_id)

    def insert(self, session=None):
        self._validate()
        return super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate()
        return super().update(session=session, force=force)

    def delete(self, session=None, **kwargs):
        # A dangling reference compiles to an empty allow-list, which the
        # agent applies as no filtering at all.
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


class ProxyDestinationNodeKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    """Where a door leads, as a node of the caller's.

    Not an address: the hypervisor fetches it from the management network,
    so a caller-supplied address would be an open relay. A node is
    ownership-checked, its address looked up, and its door goes with it.
    """

    KIND = "node"

    node = properties.property(types.UUID(), required=True)


class ProxyDestinationAddressKind(
    types_dynamic.AbstractKindModel, models.SimpleViewMixin
):
    """Where a door leads, as a bare address — operator-only.

    For services not modelled as nodes; gated by its own permission. An
    address, not a URL, so no name is resolved after the check.
    """

    KIND = "address"

    address = properties.property(types_network.IPAddress(), required=True)


class ProxyService(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    """A door from an overlay network onto a service of the installation.

    The per-host proxy relays HTTP on ``port`` and stamps the caller's node
    uuid (`NODE_UUID_HEADER`). The header is a second factor, never a
    credential: the upstream still authenticates its callers.

    Compiled into the network's `proxy` NF, which stays the installation's
    (`NetworkFunction._refuse_if_the_installations`).
    """

    __tablename__ = "net_proxy_services"

    network = properties.property(types.UUID(), required=True)
    # Same port on both ends; claimed by number whatever address the guest
    # dialled.
    port = properties.property(
        types.Integer(min_value=1, max_value=65535),
        required=True,
    )
    destination = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(ProxyDestinationNodeKind),
            types_dynamic.KindModelType(ProxyDestinationAddressKind),
        ),
        required=True,
    )
    # Optional name resolving to the metadata address inside this network.
    domain = properties.property(
        types.AllowNone(types.String(min_length=1, max_length=255)),
        default=None,
    )

    def _validate(self):
        from exordos_core.compute.dm import models as compute_models

        validate_reference(
            self.project_id, compute_models.Network, self.network, "network"
        )
        network = compute_models.Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.network)}
        )
        if network is not None and not nc.is_overlay(network.driver_spec):
            # A flat network already routes to the service; a door would be
            # a second path through the hypervisor.
            raise ex_exceptions.ValidateException(
                err=(
                    f"network {self.network} is not an overlay; its guests "
                    "reach the installation directly and need no door"
                )
            )
        if self.port in nc.PLATFORM_PROXY_PORTS:
            raise ex_exceptions.ValidateException(
                err=(
                    f"port {self.port} is the platform's own; a door there "
                    "would answer for every agent on this network"
                )
            )
        if getattr(self.destination, "KIND", None) == "node":
            validate_reference(
                self.project_id,
                compute_models.Node,
                self.destination.node,
                "destination.node",
            )
        taken = ProxyService.objects.get_all(
            filters={
                "uuid": dm_filters.NE(self.uuid),
                "network": dm_filters.EQ(self.network),
                "port": dm_filters.EQ(self.port),
            },
            limit=1,
        )
        for other in taken:
            raise ex_exceptions.ValidateException(
                err=(
                    f"port {self.port} on this network is already the door to "
                    f"{other.uuid}"
                )
            )
        self._validate_destination_is_the_only_path()

    def _validate_destination_is_the_only_path(self) -> None:
        """A door has to be the *only* way a guest reaches the service.

        The stamped identity is trustworthy only because guests cannot go
        around the proxy. Checked here and again on every pass by
        `upstream`, since a second path can appear later.
        """
        reason = self._second_path()
        if reason:
            raise ex_exceptions.ValidateException(err=reason)

    def _second_path(self) -> tp.Optional[str]:
        """Why this destination is reachable other than through the door.

        * an overlay guest: unreachable from the hypervisor's root namespace;
        * a floating address: `ensure_published_reachable` opens it to every
          guest on the host.

        Checks every port the node actually has, not its declaration.
        """
        from exordos_core.compute.dm import models as compute_models

        if getattr(self.destination, "KIND", None) != "node":
            # A bare address is the operator's (privileged) call.
            return None
        node = compute_models.Node.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.destination.node)}
        )
        if node is None:
            return None
        ports = compute_models.Port.objects.get_all(
            filters={"node": dm_filters.EQ(getattr(node, "uuid", node))}
        )
        for port in ports:
            if self._is_overlay_subnet(port.subnet):
                return (
                    f"destination.node {self.destination.node} is a guest of "
                    "an overlay, so no hypervisor can reach it; a service "
                    "behind a door has to sit on a network the installation "
                    "itself routes to"
                )
            published = Address.objects.get_all(
                filters={
                    "association": dm_filters.EQ(port.uuid),
                    "allocation": dm_filters.EQ(AddressAllocation.RESERVED.value),
                    "origin": dm_filters.EQ(AddressOrigin.FLOATING.value),
                },
                limit=1,
            )
            for address in published:
                return (
                    f"destination.node {self.destination.node} answers on the "
                    f"floating address {address.address}, which its guests can "
                    "reach directly — a door beside it would identify nobody"
                )
        return None

    @staticmethod
    def _is_overlay_subnet(subnet_uuid) -> bool:
        from exordos_core.compute.dm import models as compute_models

        subnet = compute_models.Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet_uuid)}
        )
        if subnet is None:
            return False
        network = compute_models.Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet.network)}
        )
        return network is not None and nc.is_overlay(network.driver_spec)

    def upstream(self) -> tp.Optional[str]:
        """Where the hypervisor fetches, or None while nothing is there yet.

        The address of the node's primary port, never its `default_network`:
        the node's owner can write that one. A node without an addressed
        port (still being built) is left out of the compiled set until it
        has one.
        """
        from exordos_core.compute.dm import models as compute_models

        kind = getattr(self.destination, "KIND", None)
        if kind == "address":
            return "http://%s:%d" % (self.destination.address, self.port)
        reason = self._second_path()
        if reason:
            # Shut while a second path exists; reopens when it goes away.
            LOG.warning("Door %s stays shut: %s", self.uuid, reason)
            return None
        for port in compute_models.Port.objects.get_all(
            filters={"node": dm_filters.EQ(self.destination.node)},
            order_by={"index": "asc"},
        ):
            if port.ipv4 is not None:
                return "http://%s:%d" % (port.ipv4, self.port)
        return None

    def insert(self, session=None):
        self._validate()
        return super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate()
        return super().update(session=session, force=force)


class AddressAllocation(str, enum.Enum):
    # A freed row is a receipt; uniqueness covers reserved rows only.
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
    # Declarable in manifests as `$core.network.catalog.addresses`.
    ua_models.ResourceMixin,
):
    """An allocated address — the DB-backed IPAM ledger (Elastic-IP model).

    ``allocation`` says whether the address is held against the subnet's
    pool (unique among reserved rows); ``association`` is the port using it
    now, set by the compiler. ``owner_port`` marks an address allocated for
    a port that dies with it. An associated or port-owned address cannot be
    freed or deleted.
    """

    __tablename__ = "net_addresses"

    subnet = properties.property(types.UUID(), required=True)
    # Omitted: allocated from the subnet (origin=auto).
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
        # Network, broadcast and gateways (``routers[*].via``).
        reserved = {str(subnet.cidr.network), str(subnet.cidr.broadcast)}
        for router in subnet.routers or []:
            via = router.get("via") if isinstance(router, dict) else None
            if via is not None:
                reserved.add(str(via))
        return reserved

    def _used_addresses(self):
        # Ledger (reserved rows only) plus compute_ports.ipv4, so this and
        # the reconcile Ipam never collide.
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

    def _validate_address_value(self, subnet) -> None:
        """An address must be an IP inside the subnet.

        One non-IP row makes `ipam.ledger_reservations` raise and silently
        disables reservations for the whole subnet.
        """
        if self.address is None:
            return
        if not u.is_ip(self.address):
            raise ex_exceptions.ValidateException(
                err=f"address {self.address!r} is not an IP address"
            )
        if subnet.cidr is None:
            return
        if netaddr.IPAddress(str(self.address)) not in subnet.cidr:
            raise ex_exceptions.ValidateException(
                err=(
                    f"address {self.address} is not in {subnet.cidr}, the "
                    "subnet it would be held against"
                )
            )

    def _validate_address_free(self, subnet) -> None:
        """An address asked for by value must be free to hold.

        Not the subnet's network, broadcast or a gateway, and no port's.
        Another reserved row is the unique index's to refuse.
        """
        from exordos_core.compute.dm import models as compute_models

        if self.address is None:
            return
        if str(self.address) in self._reserved_addresses(subnet):
            raise ex_exceptions.ValidateException(
                err=f"address {self.address} is the subnet's own, not a host's"
            )
        taken = compute_models.Port.objects.get_all(
            filters={
                "subnet": dm_filters.EQ(self.subnet),
                "ipv4": dm_filters.EQ(netaddr.IPAddress(str(self.address))),
            },
            limit=1,
        )
        for port in taken:
            raise ex_exceptions.ValidateException(
                err=f"address {self.address} is already port {port.uuid}'s"
            )

    def _resolve_subnet(self):
        """Fetch the target subnet, refusing one this project may not use."""
        from exordos_core.compute.dm import models as compute_models

        subnet = compute_models.Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.subnet)}
        )
        if subnet is None:
            raise ex_exceptions.ValidateException(
                err=f"subnet {self.subnet} does not exist"
            )
        if not may_allocate_in(self.project_id, subnet):
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
        self._validate_address_value(subnet)
        self._validate_address_free(subnet)
        if self.address is None:
            # Lock the subnet so concurrent allocations do not pick the same
            # free address.
            from exordos_core.compute.dm import models as compute_models

            compute_models.Subnet.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.subnet)},
                session=session,
                locked=True,
            )
            self.address = self._allocate_free_address(subnet)
            # Informational only; `floating` stays as the compiler set it.
            if self.origin == AddressOrigin.EXPLICIT.value:
                self.origin = AddressOrigin.AUTO.value
        return super().insert(session=session)

    def update(self, session=None, force=False):
        # Or a PUT of a foreign subnet would bypass the insert gate.
        subnet = self._resolve_subnet()
        self._validate_address_value(subnet)
        stored = Address.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.uuid)}, session=session
        )
        if stored is None or str(stored.address) != str(self.address):
            self._validate_address_free(subnet)
        self._validate_allocation(stored)
        return super().update(session=session, force=force)

    def _validate_allocation(self, stored) -> None:
        """Guard the reserved <-> freed transition, both ways.

        Freeing is a delete that keeps the receipt, so it has the delete's
        guards. Re-reserving is allocating: the address may have been taken
        meanwhile, and uniqueness covers reserved rows only.
        """
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
        # A port's `public` slot cites it; the compiler would silently skip it.
        for port in _ports_of(self.project_id):
            config = port.config
            if getattr(config, "KIND", None) != "simple":
                continue
            public = config.public or {}
            if str(public.get("address")) == str(self.uuid):
                raise ra_storage_exc.ConflictRecords(
                    model="Address",
                    msg=f"still the floating address of port {port.uuid}",
                )
        # Releasing it would hand it out while a port still answers on it.
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
    # An allow-list: a subnet's default group...
    SPLITTER = "splitter"
    # ... and the services the installation runs for a network's guests.
    DHCP = "dhcp"
    DNS = "dns"
    PROXY = "proxy"


class NFProvenance(str, enum.Enum):
    # user-authored vs seeded by the installation (and refreshed by it until
    # a caller edits it).
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

    How the fabric intercepts and realizes it follows from ``kind``. A
    function is seeded for its owner (a subnet's DHCP and default group, a
    network's resolver and metadata proxy); callers read and edit it.
    """

    __tablename__ = "net_nfs"

    kind = properties.property(
        types.Enum([k.value for k in NetworkFunctionKind]),
        required=True,
    )
    config = properties.property(types.Dict(), default=lambda: {})
    provenance = properties.property(
        types.Enum([p.value for p in NFProvenance]),
        default=NFProvenance.USER.value,
    )
    # What the function was seeded for.
    owner_subnet = properties.property(types.AllowNone(types.UUID()), default=None)
    owner_network = properties.property(types.AllowNone(types.UUID()), default=None)

    # Config keys per kind, so a typo is a 400 rather than a silent no-op.
    _CONFIG_SCHEMA = {
        "splitter": {"rules": list},
        # `filename` is what a guest's iPXE ROM chain-loads; `lease_seconds`
        # overrides the responder's default lease.
        "dhcp": {"filename": str, "lease_seconds": int},
        # `platform_names` resolve to the serving hypervisor, the only
        # platform address an overlay guest can reach.
        "dns": {"forwarders": list, "zone_suffix": str, "platform_names": list},
        # `forwards`: published static prefixes, read-only. `ports`: platform
        # APIs that authenticate nobody, so the proxy scopes the guest.
        # `apis`: upstreams that authenticate callers, relayed whole.
        # `services`: ProxyService doors, told who is calling. All else 404.
        "proxy": {
            "forwards": list,
            "ports": dict,
            "apis": list,
            "services": dict,
        },
    }

    def _validate(self):
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
                (self.config or {}).get("rules"), project_id=self.project_id
            )
        if self.kind == NetworkFunctionKind.DNS.value:
            self._validate_forwarders()

    def _validate_forwarders(self) -> None:
        """A caller's resolver has to be a public IPv4 address.

        The responder relays from the network's namespace through the
        hypervisor's own routing, past the fabric guard, so any other
        address would be a relay into the underlay. The installation's
        seeded resolvers stay: a forwarder already there is kept as is.
        """
        if self.provenance == NFProvenance.GENERATED.value:
            return
        stored = NetworkFunction.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.uuid)}
        )
        kept = set((stored.config or {}).get("forwarders") or []) if stored else set()
        for forwarder in (self.config or {}).get("forwarders") or []:
            if forwarder in kept:
                continue
            try:
                address = ipaddress.IPv4Address(str(forwarder))
            except ValueError:
                raise ex_exceptions.ValidateException(
                    err=f"forwarder {forwarder!r} is not an IPv4 address"
                )
            if not address.is_global:
                raise ex_exceptions.ValidateException(
                    err=(
                        f"forwarder {forwarder} is not a public address; the "
                        "resolver relays from the hypervisor, not from your "
                        "network"
                    )
                )

    def insert(self, session=None):
        self._validate()
        return super().insert(session=session)

    def _refuse_if_the_installations(self, verb: str) -> None:
        """Refuse a caller's write to the platform's own function.

        `proxy` names the upstreams the hypervisor fetches from the
        management network for a guest; a tenant editing it (it lives in
        their project) would get an open relay. So it is not editable or
        deletable via the API and is refreshed from config on every pass.
        By kind, not provenance: a caller's write must not change ownership.
        """
        if self.kind == NetworkFunctionKind.PROXY.value:
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
        # Edited means the caller's from now on.
        if self.provenance == NFProvenance.GENERATED.value:
            self.provenance = NFProvenance.USER.value
        self._validate()
        return super().update(session=session, force=force)

    def delete(self, session=None, **kwargs):
        # The compiler would reseed it; meanwhile guests reach nothing.
        self._refuse_if_the_installations("deleted")
        return super().delete(session=session, **kwargs)

    # --- generator-side API ------------------------------------------------

    def sync_generated(self, config: dict, session=None) -> None:
        """Bring a function back to what its generator says it should be."""
        if self.config == config:
            return
        self.config = config
        super().update(session=session)

    def collect_generated(self, session=None) -> None:
        super().delete(session=session)
