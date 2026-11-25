#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import base64
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import hashlib
import ipaddress
import logging
import uuid

from gcl_looper.services import basic
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.api import crypto
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.common import contexts
from restalchemy.dm import filters
from restalchemy.storage import exceptions as storage_exceptions
from restalchemy.storage.sql import utils as sql_utils

from exordos_core.agent.universal.drivers.secret.dm import models as issuer
from exordos_core.common import constants
from exordos_core.common import spiffe
from exordos_core.common.dm import targets
from exordos_core.compute import constants as compute_constants
from exordos_core.compute.dm import models as compute
from exordos_core.elements import endpoints as endpoint_spec
from exordos_core.elements.builders.service import CommonBuilder
from exordos_core.elements.dm import models as elements
from exordos_core.elements.dm import res_models

LOG = logging.getLogger(__name__)
LINK_KIND = "link"
LINK_OWNER = uuid.UUID("30a76b42-f59d-465a-9f96-29a951377b22")
IDENTITY_CONTEXT = b"exordos-link-identity-v1"
IDENTITY_LIFETIME = timedelta(hours=1)


@dataclass(frozen=True)
class Attachment:
    network: uuid.UUID
    subnet: uuid.UUID
    address: str
    overlay: bool


class LinkService(basic.BasicService):
    def _iteration(self) -> None:
        with contexts.Context().session_manager() as session:
            try:
                with sql_utils.savepoint():
                    self._reconcile(session)
            except Exception:
                LOG.exception("Error applying service endpoints")

            if self._iteration_number % 30 == 0:
                try:
                    with sql_utils.savepoint():
                        self._collect_retired_addresses(session)
                except Exception:
                    LOG.exception("Error cleaning up retired endpoint addresses")

    @staticmethod
    def supports_endpoints(agent):
        capabilities = agent.capabilities.get("capabilities")
        return (
            agent.status == "ACTIVE"
            and isinstance(capabilities, list)
            and LINK_KIND in capabilities
        )

    def _reconcile(self, session):
        listeners, identities = self._endpoint_listeners(session)
        node_keys = {}
        if listeners:
            keys = ua_models.NodeEncryptionKey.objects.get_all(
                filters={"uuid": filters.In(listeners)}
            )
            node_keys = {key.uuid: key for key in keys}
        existing = {}
        for resource in ua_models.TargetResource.objects.get_all(
            filters={
                "kind": filters.EQ(LINK_KIND),
                "master": filters.EQ(LINK_OWNER),
            }
        ):
            if resource.agent is None:
                # The machine deletion hook detaches target resources for rescheduling.
                resource.delete()
            else:
                existing[resource.agent] = resource

        for node in sorted(listeners.keys() | existing.keys()):
            current = existing.get(node)
            entries = [
                {**listener, "peer_ids": sorted(listener["peer_ids"])}
                for _, listener in sorted(listeners.get(node, {}).items())
            ]
            try:
                with sql_utils.savepoint():
                    if entries and node in node_keys:
                        self._apply_link(
                            node, entries, identities[node], node_keys[node], current
                        )
                    elif current is not None:
                        current.delete()
            except Exception:
                LOG.exception("Error applying endpoint configuration on %s", node)

    def _endpoint_listeners(self, session):
        uses = self._bindings(session)
        if not uses:
            return {}, {}
        services = {
            service.uuid: service
            for service in elements.Service.objects.get_all(
                filters={"uuid": filters.In({use["service"] for use in uses})}
            )
        }
        owners = {
            (use[side + "_kind"], use[side]): use["project_id"]
            for use in uses
            for side in ("provider", "consumer")
        }
        members = defaultdict(set)
        node_owners = {}
        for kind, field in (
            (targets.NodeTarget.KIND, "uuid"),
            (targets.NodeSetTarget.KIND, "node_set"),
        ):
            ids = {owner for owner_kind, owner in owners if owner_kind == kind}
            if not ids:
                continue
            for node in compute.Node.objects.get_all(filters={field: filters.In(ids)}):
                owner = (kind, getattr(node, field))
                project = owners[owner]
                if field == "uuid":
                    if node.node_set is not None or node.project_id != project:
                        continue
                elif node.project_id not in (
                    project,
                    compute_constants.NODE_SET_PROJECT,
                ):
                    continue
                node_owners[node.uuid] = (project, kind, owner[1])
                members[owner].add(node.uuid)
        if not node_owners:
            return {}, {}
        capable = {
            agent.uuid
            for agent in ua_models.UniversalAgent.objects.get_all(
                filters={
                    "uuid": filters.In(node_owners),
                    "status": filters.EQ("ACTIVE"),
                }
            )
            if agent.uuid == agent.node and self.supports_endpoints(agent)
        }
        if not capable:
            return {}, {}
        for eligible in members.values():
            eligible.intersection_update(capable)
        attachments = self._active_attachments(
            session, {node: node_owners[node] for node in capable}
        )
        ready = self._ready_service_nodes(services.values(), members)
        listeners = defaultdict(dict)
        for use in uses:
            service = services.get(use["service"])
            if service is None:
                continue
            provider = (use["provider_kind"], use["provider"])
            if (
                provider != (service.target.kind, service.target.owners()[0])
                or service.project_id != use["project_id"]
            ):
                continue
            endpoint = service.endpoints.get(use["endpoint"])
            if endpoint is None:
                continue
            app_port = endpoint["port"]
            protocol = endpoint.get("protocol", endpoint_spec.HTTP)
            project = service.project_id
            consumer = (use["consumer_kind"], use["consumer"])
            for client in members[consumer]:
                listen = f"127.0.0.1:{use['client_port']}"
                outbound = {
                    "role": "outbound",
                    "listen": listen,
                    "targets": [],
                    "peer_ids": [self._spiffe_id(project, provider[1], provider[0])],
                }
                listeners[client][listen] = outbound
                for server in ready.get(service.uuid, ()):
                    pair = self._shared_transport(
                        attachments.get(client, ()), attachments.get(server, ())
                    )
                    if pair is None:
                        continue
                    source, address = pair
                    transport = f"{address}:{use['server_port']}"
                    outbound["targets"].append({"address": transport, "source": source})
                    inbound = listeners[server].setdefault(
                        transport,
                        {
                            "role": "inbound",
                            "protocol": protocol,
                            "listen": transport,
                            "targets": [f"127.0.0.1:{app_port}"],
                            "peer_ids": set(),
                        },
                    )
                    inbound["peer_ids"].add(
                        self._spiffe_id(project, consumer[1], consumer[0])
                    )
        identities = {
            node: self._spiffe_id(project, owner, kind)
            for node, (project, kind, owner) in node_owners.items()
            if node in listeners
        }
        return listeners, identities

    @staticmethod
    def _ready_service_nodes(services, members):
        units_by_service = {
            service.uuid: {
                CommonBuilder.service_uuid_by_node_path(node, service.path): node
                for node in members[service.target.kind, service.target.owners()[0]]
            }
            for service in services
            if service.target_status == "enabled"
        }
        units = {unit for by_unit in units_by_service.values() for unit in by_unit}
        if not units:
            return {}
        running = {
            resource.uuid
            for resource in ua_models.Resource.objects.get_all(
                filters={
                    "uuid": filters.In(units),
                    "kind": filters.EQ(res_models.ServiceNode.get_resource_kind()),
                    "status": filters.EQ("ACTIVE"),
                }
            )
        }
        return {
            service: sorted(by_unit[unit] for unit in by_unit.keys() & running)
            for service, by_unit in units_by_service.items()
        }

    @staticmethod
    def _active_attachments(session, owners):
        ports = session.execute(
            """
            SELECT p.node, p.ipv4, p.project_id, s.uuid AS subnet,
                s.project_id AS subnet_project, n.uuid AS network,
                n.driver_spec->>'driver' AS driver
            FROM compute_ports AS p
            JOIN compute_subnets AS s ON s.uuid = p.subnet
            JOIN compute_networks AS n ON n.uuid = s.network
            WHERE p.node = ANY(%s::uuid[]) AND p.status = 'ACTIVE'
                AND p.ipv4 IS NOT NULL AND s.next_server IS NULL
                AND n.driver_spec->>'driver' IN ('flat_bridge', 'ovs_evpn')
            """,
            ([str(node) for node in owners],),
        ).fetchall()
        attachments = defaultdict(set)
        for port in ports:
            project, kind, _ = owners[port["node"]]
            if port["project_id"] != project and (
                kind != targets.NodeSetTarget.KIND
                or port["project_id"] != compute_constants.NODE_SET_PROJECT
            ):
                continue
            address = ipaddress.ip_address(port["ipv4"])
            if (
                address.version != 4
                or address.is_loopback
                or address.is_multicast
                or address.is_unspecified
                or address.is_link_local
            ):
                continue
            overlay = port["driver"] == "ovs_evpn"
            if port["subnet_project"] != project and not (
                not overlay and port["subnet"] == constants.MAIN_SUBNET_UUID
            ):
                continue
            attachments[port["node"]].add(
                Attachment(port["network"], port["subnet"], str(address), overlay)
            )
        return attachments

    @staticmethod
    def _shared_transport(client, server):
        client_ips = Counter(attachment.address for attachment in client)
        server_ips = Counter(attachment.address for attachment in server)
        # Each IP must identify one interface for source-address binding.
        candidates = (
            (not source.overlay, source.network, source.address, target.address)
            for source in client
            for target in server
            if source.network == target.network
            and (source.overlay or source.subnet == target.subnet)
            and client_ips[source.address] == server_ips[target.address] == 1
        )
        pair = min(candidates, default=None)
        return pair[2:] if pair else None

    def _apply_link(self, node, entries, spiffe_uri, node_key, current):
        resource_uuid = uuid.uuid5(node, LINK_KIND)
        cached = current.value.get("identity") if current else None
        identity = self._encrypted_identity(spiffe_uri, node_key, cached)
        if identity is None:
            if current is not None:
                current.delete()
            return
        digest = ua_utils.calculate_hash(
            {"listeners": entries, "identity_revision": identity["revision"]}
        )
        value = {"listeners": entries, "identity": identity}
        if current is None:
            ua_models.TargetResource(
                uuid=resource_uuid,
                res_uuid=ua_models.TargetResource.gen_res_uuid(
                    resource_uuid, LINK_KIND
                ),
                kind=LINK_KIND,
                master=LINK_OWNER,
                agent=node,
                value=value,
                hash=digest,
                full_hash=digest,
            ).insert()
        elif current.hash != digest:
            current.value = value
            current.hash = current.full_hash = digest
            current.update()
        else:
            return
        for entry in entries:
            if entry["role"] == "outbound" and not entry["targets"]:
                LOG.info(
                    "No reachable service endpoint for %s at %s", node, entry["listen"]
                )

    @staticmethod
    def _encrypted_identity(spiffe_uri, node_key, cached):
        now = datetime.now(timezone.utc)
        key_updated = node_key.updated_at.isoformat()
        if cached and (
            cached["spiffe_id"] != spiffe_uri
            or cached["key_updated_at"] != key_updated
            or datetime.fromisoformat(cached.get("expiration_at", cached["renew_at"]))
            <= now
        ):
            cached = None
        if cached and datetime.fromisoformat(cached["renew_at"]) > now:
            return cached

        try:
            with sql_utils.savepoint("link_identity"):
                authority = issuer.SPIFFEAuthority.get_or_create(
                    constants.ZERO_UUID, spiffe.LINK_TRUST_DOMAIN
                )
                issued = spiffe.issue_svid(
                    authority.key.encode(),
                    authority.cert,
                    spiffe_uri,
                    IDENTITY_LIFETIME,
                )
        except (ValueError, storage_exceptions.ConflictRecords):
            LOG.exception("Error issuing Link identity %s", spiffe_uri)
            # Listener changes must still reach a guest when a renewal fails.
            return cached
        pem = issued.cert.encode() + issued.key
        nonce, nonce_b64 = crypto.generate_nonce_base64()
        sealed = crypto.encrypt_chacha20_poly1305(
            base64.b64decode(node_key.private_key), pem, nonce, IDENTITY_CONTEXT
        )
        return {
            "spiffe_id": spiffe_uri,
            "key_updated_at": key_updated,
            "renew_at": (issued.expiration_at - IDENTITY_LIFETIME / 2).isoformat(),
            "expiration_at": issued.expiration_at.isoformat(),
            "revision": hashlib.sha256(pem).hexdigest(),
            "nonce": nonce_b64,
            "sealed": base64.b64encode(sealed).decode(),
        }

    @staticmethod
    def _spiffe_id(project, target, kind):
        segment = "node" if kind == targets.NodeTarget.KIND else "nodeset"
        return (
            f"spiffe://{spiffe.LINK_TRUST_DOMAIN}/project/{project}/{segment}/{target}"
        )

    @staticmethod
    def _bindings(session):
        return session.execute(
            """
            WITH owners AS NOT MATERIALIZED (
                SELECT uuid, project_id, 'node_set' AS kind FROM compute_sets
                UNION ALL
                SELECT uuid, project_id, 'node' AS kind FROM nodes WHERE node_set IS NULL
            )
            SELECT DISTINCT u.consumer, u.consumer_kind, u.service, u.endpoint,
                s.project_id, p.uuid AS provider, p.kind AS provider_kind,
                local.port AS client_port, remote.port AS server_port
            FROM em_endpoint_uses AS u
            JOIN em_services AS s ON s.uuid = u.service
            JOIN owners AS c ON c.uuid = u.consumer AND c.kind = u.consumer_kind
                AND c.project_id = s.project_id
            JOIN owners AS p ON p.kind = s.target->>'kind'
                AND p.uuid = (s.target->>p.kind)::uuid
                AND p.project_id = s.project_id
            JOIN em_endpoint_addresses AS local ON
                (local.target, local.service, local.endpoint) =
                (u.consumer, u.service, u.endpoint)
            JOIN em_endpoint_addresses AS remote ON
                remote.target = p.uuid AND
                (remote.service, remote.endpoint) = (u.service, u.endpoint)
            WHERE s.endpoints ? u.endpoint
            ORDER BY u.service, u.endpoint, u.consumer
            """
        ).fetchall()

    @staticmethod
    def _collect_retired_addresses(session):
        session.execute("""
            DELETE FROM em_endpoint_addresses AS a
            WHERE NOT EXISTS (SELECT 1 FROM compute_sets WHERE uuid = a.target)
              AND NOT EXISTS (SELECT 1 FROM nodes WHERE uuid = a.target)
              AND NOT EXISTS (SELECT 1 FROM em_resources WHERE uuid = a.target)
              AND NOT EXISTS (
                  SELECT 1 FROM em_endpoint_uses AS u
                  WHERE (u.consumer, u.service, u.endpoint) = (a.target, a.service, a.endpoint)
              )
        """)
