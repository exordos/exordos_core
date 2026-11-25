#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from dataclasses import dataclass
import re
import uuid

from restalchemy.dm import filters
from restalchemy.dm import types
from restalchemy.storage.sql import engines

from exordos_core.common import exceptions
from exordos_core.common.dm import targets
from exordos_core.compute.dm import models as compute


PORT_START = 20000
PORT_END = 29999
NAME = re.compile(r"[A-Za-z0-9_-]{1,63}")
HTTP = "http"
TCP = "tcp"
ENDPOINT_TARGETS = {
    targets.NodeTarget.KIND: compute.Node,
    targets.NodeSetTarget.KIND: compute.NodeSet,
}


class EndpointSpec(types.SchemeDict):
    def __init__(self):
        super().__init__(
            {
                "port": types.Integer(min_value=1, max_value=65535),
                "protocol": types.Enum([HTTP, TCP]),
            }
        )

    @staticmethod
    def with_default(value):
        return {"protocol": HTTP, **value} if isinstance(value, dict) else value

    def validate(self, value):
        return super().validate(self.with_default(value))

    def from_simple_type(self, value):
        return super().from_simple_type(self.with_default(value))

    def to_simple_type(self, value):
        return super().to_simple_type(self.with_default(value))

    def to_openapi_spec(self, prop_kwargs):
        spec = super().to_openapi_spec(prop_kwargs)
        spec["properties"]["protocol"]["default"] = HTTP
        spec["required"] = ["port"]
        return spec


def endpoint_name(parameter):
    parts = parameter.split(":")[1:]
    if parts == ["address"]:
        return "default"
    if len(parts) == 3 and parts[0] == "endpoints" and parts[2] == "address":
        return parts[1]
    return None


def validate_endpoints(value, target):
    if value and target not in ENDPOINT_TARGETS:
        raise exceptions.ValidateException(
            err="Service endpoints require a Node or NodeSet."
        )
    if any(not NAME.fullmatch(name) for name in value):
        raise exceptions.ValidateException(err="Invalid service endpoint name.")
    protocols = {}
    for endpoint in value.values():
        port = endpoint["port"]
        if PORT_START <= port <= PORT_END:
            raise exceptions.ValidateException(
                err=f"Port {port} is reserved for dynamic endpoints."
            )
        protocol = endpoint.get("protocol", HTTP)
        if protocols.setdefault(port, protocol) != protocol:
            raise exceptions.ValidateException(
                err=f"Service port {port} cannot mix HTTP and TCP endpoints."
            )


def validate_target_protocols(service, session):
    if not service.endpoints:
        return
    kind = service.target.kind
    target = str(service.target.owners()[0])
    session.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"em_service_protocols:{target}",),
    )
    publications = session.execute(
        """
        SELECT endpoints FROM em_services
        WHERE uuid <> %s AND project_id = %s AND target->>%s = %s
        """,
        (str(service.uuid), str(service.project_id), kind, target),
    ).fetchall()
    protocols = {
        endpoint["port"]: endpoint.get("protocol", HTTP)
        for endpoint in service.endpoints.values()
    }
    for row in publications:
        for endpoint in row["endpoints"].values():
            port = endpoint["port"]
            if port in protocols and protocols[port] != endpoint.get("protocol", HTTP):
                raise exceptions.ValidateException(
                    err=f"Port {port} cannot mix HTTP and TCP endpoints on one target."
                )


def allocate_missing_provider_ports(service, session):
    if not service.endpoints:
        return
    target = service.target.owners()[0]
    uses = session.execute(
        """
        SELECT DISTINCT u.endpoint
        FROM em_endpoint_uses AS u
        WHERE u.service = %s AND u.endpoint = ANY(%s)
            AND NOT EXISTS (
                SELECT 1 FROM em_endpoint_addresses AS a
                WHERE a.target = %s AND a.service = u.service AND a.endpoint = u.endpoint
            )
        ORDER BY u.endpoint
        """,
        (str(service.uuid), list(service.endpoints), str(target)),
    ).fetchall()
    for use in uses:
        endpoint_port(
            target,
            service.uuid,
            use["endpoint"],
            allocate=True,
            session=session,
        )


def endpoint_port(target, service, endpoint, *, allocate=False, session=None):
    engine = engines.engine_factory.get_engine()
    with engine.session_manager(session=session) as session:
        key = str(target), str(service), endpoint
        query = (
            "SELECT port FROM em_endpoint_addresses "
            "WHERE target = %s AND service = %s AND endpoint = %s"
        )
        row = session.execute(query, key).fetchone()
        if row is not None:
            return row["port"]
        if not allocate:
            raise KeyError(f"Awaiting an address for endpoint {service}:{endpoint}")
        # A single reconciliation may allocate addresses on several NodeSets.
        # Consistent lock ordering avoids deadlocks with concurrent allocation.
        session.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("em_endpoint_addresses",),
        )
        row = session.execute(query, key).fetchone()
        if row is not None:
            return row["port"]
        row = session.execute(
            """
            INSERT INTO em_endpoint_addresses (target, service, endpoint, port)
            SELECT %s, %s, %s, candidate
            FROM generate_series(%s::integer, %s::integer) AS ports(candidate)
            WHERE NOT EXISTS (
                SELECT 1 FROM em_endpoint_addresses
                WHERE target = %s AND port = candidate
            )
            ORDER BY candidate LIMIT 1
            RETURNING port
            """,
            (*key, PORT_START, PORT_END, str(target)),
        ).fetchone()
        if row is None:
            raise exceptions.ValidateException(
                err=f"Dynamic endpoint port range exhausted on target {target}."
            )
        return row["port"]


@dataclass(frozen=True, order=True)
class EndpointUse:
    consumer: uuid.UUID
    consumer_kind: str
    service: uuid.UUID
    endpoint: str


def validate_reference(service, name, target_kind):
    declared = service.value
    if name not in declared.get("endpoints", {}):
        raise exceptions.ValidateException(
            err=f"Service {service.link} has no endpoint {name!r}."
        )
    if declared.get("target", {}).get("kind") not in ENDPOINT_TARGETS:
        raise exceptions.ValidateException(
            err=f"Service {service.link} must target a Node or NodeSet."
        )
    if target_kind not in ENDPOINT_TARGETS:
        raise exceptions.ValidateException(
            err="A config with an endpoint address must target a Node or NodeSet."
        )


class ConfigEndpoints:
    def __init__(self, project, target, *, allocate=False, validate_only=False):
        self.project = project
        self.target = target
        self.allocate = allocate
        self.validate_only = validate_only
        self.uses = set()

    def address(self, resource, name, engine):
        service = resource.original
        validate_reference(service, name, self.target.get("kind"))
        if self.validate_only:
            return ""
        declared = service.value
        provider_state = service._recursive_render(
            {"project_id": declared["project_id"], "target": declared["target"]}, engine
        )
        if str(provider_state["project_id"]) != str(self.project):
            raise exceptions.ValidateException(
                err="Endpoint must be in the config's project."
            )
        consumer_kind = self.target["kind"]
        consumer = uuid.UUID(str(self.target[consumer_kind]))
        provider_kind = provider_state["target"]["kind"]
        provider = uuid.UUID(str(provider_state["target"][provider_kind]))
        for kind, target in {(consumer_kind, consumer), (provider_kind, provider)}:
            owner = ENDPOINT_TARGETS[kind].objects.get_one_or_none(
                filters={"uuid": filters.EQ(target)}
            )
            if owner is not None and (
                str(owner.project_id) != str(self.project)
                or (kind == targets.NodeTarget.KIND and owner.node_set is not None)
            ):
                raise exceptions.ValidateException(
                    err="Endpoint targets must be standalone Nodes or NodeSets in the config's project."
                )
        port = endpoint_port(consumer, service.uuid, name, allocate=self.allocate)
        if self.allocate:
            endpoint_port(provider, service.uuid, name, allocate=True)
            self.uses.add(EndpointUse(consumer, consumer_kind, service.uuid, name))
        return f"127.0.0.1:{port}"


def sync_config_uses(config, uses):
    engine = engines.engine_factory.get_engine()
    with engine.session_manager() as session:
        rows = session.execute(
            "SELECT consumer, consumer_kind, service, endpoint "
            "FROM em_endpoint_uses WHERE config = %s",
            (str(config),),
        ).fetchall()
        current = {EndpointUse(**row) for row in rows}
        for use in sorted(current - uses):
            session.execute(
                "DELETE FROM em_endpoint_uses "
                "WHERE config = %s AND service = %s AND endpoint = %s",
                (str(config), str(use.service), use.endpoint),
            )
        for use in sorted(uses - current):
            session.execute(
                "INSERT INTO em_endpoint_uses "
                "(config, consumer, consumer_kind, service, endpoint) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    str(config),
                    str(use.consumer),
                    use.consumer_kind,
                    str(use.service),
                    use.endpoint,
                ),
            )
