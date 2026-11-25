# Service endpoints

An Elements service on a NodeSet or standalone Node can publish named TCP
ports on its application's IPv4 loopback interface.

```yaml
$core.em.services:
  catalog:
    endpoints:
      default:
        port: 8080
      events:
        port: 8081
        protocol: tcp
```

The default `protocol: http` supports HTTP/1.x and cleartext HTTP/2 (h2c),
including gRPC. Link encrypts the network connection with mTLS in both modes.
On HTTP endpoints the receiving Link replaces `X-Forwarded-Client-Cert` with
the verified peer certificate's SPIFFE URI and fingerprint in Envoy format.
gRPC applications see this field as incoming metadata. Use a dedicated
application port bound to loopback and treat local processes that can reach
that port as part of the application's trust boundary.

Use `protocol: tcp` for opaque TCP, including traffic encrypted by the
application. The receiving Link closes earlier TCP sessions to an application
address when it starts serving that address in HTTP mode. Services on the same
target must use one protocol per application port.

In the text body of a Config on a NodeSet or standalone Node in the same project, use
`$core.em.services.$catalog:address` for the default endpoint and
`$core.em.services.$catalog:endpoints:events:address` for a named endpoint.
Both forms work inside an Elements `f"…"` string.

```yaml
body:
  kind: text
  content: |
    f"
    catalog_url: http://{$core.em.services.$catalog:address}
    catalog_events_addr: {$core.em.services.$catalog:endpoints:events:address}
    "
```

A reference provides the target with a stable loopback address and
authorizes its VMs to connect to that endpoint. References on the same target
share the address. Removing the last reference closes new connections
as each VM applies the change. Open sessions can finish.

Core reserves TCP ports 20000 through 29999 for dynamic endpoint listeners.
Assignments remain reserved for the lifetime of the target so a cached
address cannot be assigned to an unrelated endpoint. Choose application
ports outside that range. Ordinary connection retries
cover startup, configuration delivery and intervals with no available replicas.

A compatible eci_base image includes Link 0.0.5 and advertises the `link`
capability through Universal Agent. Core sends one `link` resource with the
listeners for each participating VM. The receiving proxy authenticates the
caller by SPIFFE identity. Replicas in one NodeSet share an identity. A
standalone Node has its own identity. Access to a VM's loopback listeners
belongs to the VM's trust boundary.

Core derives the SPIFFE ID from the VM's current membership and issues a
one-hour X.509-SVID and private key using the Secret Manager issuer. Core
encrypts the certificate, key and CA bundle for the VM and delivers them
in its Universal Agent payload. Core rotates them before expiry, including
when the listener configuration is unchanged. The `link` driver replaces the
protected PEM file atomically and reloads Link.
On reboot, systemd starts Link with the cached configuration and certificate.

The guest image build downloads the Link release package for its architecture
and verifies its pinned SHA-256 digest. To build offline, place the same package
beside `install-link.sh`.

Core builds each VM's set of targets from shared private network attachments
and running service replicas. New connections use round robin among those
replicas. If a replica's transport or application port fails before forwarding
begins, Link tries the next replica.

The [example manifest](../../exordos/manifests/examples/service-endpoints.yaml.j2)
installs two provider replicas and renders addresses for two ports into the
client's `/etc/endpoint-api/config.yaml`. Render `project_id` with the built-in
Elements project ID `12345678-c625-4fee-81d5-f691897b8142` and `image` with the
URL of the compatible base image.
