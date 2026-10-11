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

The Exordos Core supports the following methods / providers to issue and manage certificates:

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
certificate. The API rejects invalid PEM and mismatched keys. Keep the usual `project_id`, `email`, and
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
