---
icon: lucide/badge-check
---
# Certificates

Certificates are a part for the Secret Manager service. The service allows to issue and manage certificates, store them in specified storage and use them for different purposes.

The `dns_core` method issues and renews certificates using DNS challenges via Core DNS, which must be
available from the internet. The `manual` method imports an existing certificate and private key.

Examples:

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/certificates/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "my-cert",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": {
        "kind": "dns_core"
    },
    "constructor": {
        "kind": "plain"
    },
    "email": "user@exordos.com",
    "domains": ["test0.cdns.exordos.com"]
}'
```

The main fields are:

- **name** - name of the certificate.
- **project_id** - it's a project the certificate belongs.
- **method** - the method (provider) to issue and manage the certificate.
- **constructor** - In the context of the certificates, the constructor object creates and stores the certificate. The `plain` means create and store in the plain format.
- **email** - the email address to use for the certificate.
- **domains** - the list of domains to use for the certificate.

Also it's possible to specify domains with wildcards.

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/certificates/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "my-cert",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": {
        "kind": "dns_core"
    },
    "constructor": {
        "kind": "plain"
    },
    "email": "user@exordos.com",
    "domains": ["*.test1.cdns.exordos.com", "test1.cdns.exordos.com"]
}'
```

## Methods / Providers

The Exordos supports the following methods / providers to issue and manage certificates:

### dns_core

The `dns_core` provider allows to issue and manage certificates via Core DNS. It means Core DNS service should be available from the internet to accept ACME challenges. The main logic to communicate with Let's Encrypt is implemented in the [GCL CertBot plugin](https://github.com/infraguys/gcl_certbot_plugin) look at it for more information but the main steps are:

- Create or get private client key.
- Initiate a client with the key.
- Request a certificate for domains.
- Pass the DNS challenge.
- Some final preparation.

### manual

Use `method: {kind: manual}` to import a PEM certificate (leaf first, optionally followed by its chain)
and its matching, unencrypted PEM private key. Both `cert` and `key` are required when creating a manual
certificate. The API rejects invalid PEM, mismatched keys, and malformed or unrelated chain entries.
Adjacent certificates must have matching issuer names and valid signatures; this does not establish
trust in a public root. Keep the usual `project_id`, `email`, and
`domains` fields; set `domains` to the names covered by your certificate.

```yaml
resources:
  $core.secret.certificates:
    manual_cert:
      name: manual-cert
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      email: "admin@example.com"
      method:
        kind: manual
      domains:
        - "manual.example.com"
      cert: |
        -----BEGIN CERTIFICATE-----
        <your PEM certificate and optional chain>
        -----END CERTIFICATE-----
      key: |
        -----BEGIN PRIVATE KEY-----
        <your matching unencrypted PEM private key>
        -----END PRIVATE KEY-----
```

See [the complete manifest example](https://github.com/exordos/exordos_core/blob/master/exordos/manifests/examples/certificates.yaml)
for a valid demonstration pair. Replace both PEM blocks before using it for a service.

Manual certificates are stored and delivered without ACME issuance, renewal, or revocation. Expiration
is derived from the certificate. Renew them by updating `cert` and `key` together in the manifest or API.
A partial API update can reuse the existing key if it still matches. Removing a manual certificate
removes its stored copy without revoking it. Imported PEM fields are accepted only for `manual`.

## SPIFFE workload certificates

The generic `spiffe` method issues X.509-SVIDs for service-to-service mutual
TLS, independently of any storage or other application. It uses an internal
CA, requires no public DNS challenge, and renews certificates through the
existing certificate reconciliation loop.

```json
{
  "name": "payments-api",
  "project_id": "12345678-0000-0000-0000-000000000000",
  "email": "infra@example.org",
  "domains": [],
  "method": {
    "kind": "spiffe",
    "spiffe_id": "spiffe://production.example.org/services/payments-api",
    "validity_days": 30
  }
}
```

Create this resource through `/v1/secret/certificates/`, like a DNS or manual
certificate. `key`, `cert` and `expiration_at` are generated outputs. The PEM
`cert` contains the leaf followed by the public CA certificate. Deliver the
leaf/key to the workload and distribute the CA to its peers as trust material;
never trust a peer merely because it supplied its own CA in a TLS chain.

The method accepts a canonical, lowercase trust domain and a non-root workload
path. It rejects ports, user information, query/fragment components, percent
encoding, empty path segments and `.`/`..` segments. Path case is preserved.
DNS SANs are not requested: supply `domains: []`. The certificate contains
exactly one URI SAN, critical when the subject is empty, critical key usage,
`CA=false`, and both TLS client/server EKUs as required by the
[X.509-SVID specification](https://github.com/spiffe/spiffe/blob/main/standards/X509-SVID.md).

One CA is persisted for each `(project_id, trust_domain)` pair in the
issuer-only `storage_spiffe_authorities` table. Different projects get different
roots even if they use the same trust-domain name. Do not combine their roots
into one bundle when they must remain isolated. The project owner controls
identity paths within its own authority. Cross-project infrastructure identities
must be issued under an administrator-controlled project, not by trusting
arbitrary project certificates.

CA private keys never appear in certificate resources, manifests or returned
leaf chains. They remain in the certificate driver's database. Restrict database
access and protect its backups as signing-key material. All issuer replicas
must use the same database. The migration creates the issuer table; no separate
SPIFFE daemon, SPIRE server or Workload API is introduced.

`validity_days` defaults to 30 and accepts 1 through 365. The existing renewal
threshold defaults to 14 days and must be shorter than the lifetime. Renewal
creates a fresh key and serial while retaining the SPIFFE ID and CA. Changes
to identity, project or lifetime reissue immediately; ordinary metadata updates
keep the current key. Consumers must apply the new pair together and reload
their TLS context when a renewed resource is delivered.

Deleting a leaf resource does not delete its CA or revoke an already issued
certificate. Peers must enforce certificate expiry; use shorter lifetimes when
prompt revocation is needed. CA keys have a ten-year lifetime. This version
does not silently rotate an expiring CA: issuance fails before a leaf could
outlive it. CA rollover requires an explicit overlap of old and new trusted
roots and is separate from automatic leaf renewal.

Application-specific authorization extensions and Rawstor integration belong
to separate profiles layered on this issuer; the generic method does not grant
application permissions or accept arbitrary caller-supplied extensions.
