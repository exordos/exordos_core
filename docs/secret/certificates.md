# Certificates

Certificates are a part for the Secret Manager service. The service allows to issue and manage certificates, store them in specified storage and use them for different purposes.

The certificate resource supports public ACME certificates through `dns_core`
and private service certificates through `internal_ca`.

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
- **cert** - the issued leaf certificate and its chain.
- **key** - the issued leaf private key.
- **ca_cert** - the public CA certificate for `internal_ca` resources. The CA
  private key is never exposed by the API or manifest renderer.

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

### internal_ca

The `internal_ca` provider issues certificates for services reachable only
inside the Core local network. It creates a private certificate authority in
the Secret Manager backend and returns a hostname-verified server certificate
for the requested DNS names.

```json
{
  "name": "internal-mail",
  "project_id": "00000000-0000-0000-0000-000000000000",
  "method": {
    "kind": "internal_ca"
  },
  "constructor": {
    "kind": "plain"
  },
  "email": "service@example.com",
  "domains": ["mail.internal.example"]
}
```

The CA is valid for ten years. Server certificates are valid for 90 days and
are renewed under the same CA when the configured expiration threshold is
reached. Core rotates the CA before a newly issued server certificate would
outlive it; this changes `ca_cert` so client trust configuration is reconciled
alongside the service certificate. Consumers should deliver `key` and `cert`
only to the service node, deliver `ca_cert` to clients, and reload the affected
services when rendered config resources change.
