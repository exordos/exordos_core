# Rawstor TLS integration

Rawstor defaults to mutual TLS. The base integration deliberately selects
insecure transport so storage integration can merge before certificates.
This branch adds the generic SPIFFE issuer; application authorization and
runtime certificate delivery still need to be connected before enabling TLS.

When enabling TLS, remove `EXTRA_ARGS="--insecure"` from service configuration
and replace the bindings' `rawstor.initialize(insecure=True)` with TLS
initialization before the first `Target` or `Location` import. Credentials
must be provisioned first; mixing insecure mode and TLS settings is rejected.

Configure `RAWSTOR_TLS_TRUST_DOMAIN`, `RAWSTOR_TLS_CERT`, `RAWSTOR_TLS_KEY`
and `RAWSTOR_TLS_CA` for MDS, OST, vhost and the agent process using bindings.
Rawstor authenticates SPIFFE URI identities and signed Rawstor permissions;
an ordinary ACME certificate for a DNS name is insufficient.

The proposed issuer is an internal CA controlled by Core. Keep its private
key out of images, hypervisors and storage nodes. Issue credentials at runtime
and deliver private keys through the existing encrypted agent configuration.
Core provides a generic `spiffe` certificate method for issuance and renewal.
Rawstor authorization extensions and runtime delivery still need integration;
`rawstor.pki` documents the required signed permission format.

- OST certificates use `spiffe://TRUST_DOMAIN/ost/STORAGE_NODE_UUID`.
- MDS certificates use `spiffe://TRUST_DOMAIN/mds/MDS_UUID`. That UUID must
  equal the MDS witness UUID in topology, currently derived from the cluster
  UUID with UUIDv5 and the name `mds-witness`.
- The control-plane agent uses a provider admin certificate for account
  registration, inventories and structural operations. This identity does
  not authorize ordinary data writes.
- Each vhost uses `spiffe://TRUST_DOMAIN/account/PROJECT_UUID/client/NAME`.
  Issue role `none` with permissions scoped to its volume UUID: mask `3`
  grants read/write. Automatic repair that allocates replacement members
  also requires manage permission (mask `7`), which permits managing that
  object, including deletion. Choose this policy before enabling repair.

Use a shared trust domain for the installation. A single process may serve
several clusters; a process-wide `RAWSTOR_TLS_MDS_ID` pin cannot simply be
set to one cluster's identity for every connection.

For rotation, publish a versioned certificate/key pair atomically, then send
`SIGUSR1` to MDS, OST and vhost to reload TLS. Vhost uses `SIGHUP` for resize,
so TLS rotation should use `SIGUSR1`. Existing connections retain their TLS
session; reconnect or bound connection lifetime to complete rotation. Renew
before expiry and preserve identity and object permissions. Rotate CAs by
first distributing a bundle trusting both CAs, replacing leaf certificates,
and only then retiring the old CA after old sessions have drained.
