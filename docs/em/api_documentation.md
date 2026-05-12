# API Documentation

## Resources

| Entity             | Api                                                | Manifest                       |
|--------------------|----------------------------------------------------|--------------------------------|
| MachinePool        | /v1/compute/hypervisors/                           | $core.compute.hypervisors      |
| Node               | /v1/compute/nodes/                                 | $core.compute.nodes            |
| NodeSet            | /v1/compute/sets/                                  | $core.compute.sets             |
| Volume             | /v1/compute/volumes/                               | $core.compute.volumes          |
| Config             | /v1/config/configs/                                | $core.config.configs           |
| Domain             | /v1/dns/domains/                                   | $core.dns.domains              |
| Record             | /v1/dns/domains/{DomainUuid}/records/              | $core.dns.domains.records      |
| Element            | /v1/em/elements/                                   | $core.em.elements              |
| Export             | /v1/em/elements/{ElementUuid}/exports/             | $core.em.elements.exports      |
| Import             | /v1/em/elements/{ElementUuid}/imports/             | $core.em.elements.imports      |
| Resource           | /v1/em/elements/{ElementUuid}/resources/           | $core.em.elements.resources    |
| Manifest           | /v1/em/manifests/                                  | $core.em.manifests             |
| Service            | /v1/em/services/                                   | $core.em.services              |
| IamClient          | /v1/iam/clients/                                   | $core.iam.clients              |
| Idp                | /v1/iam/idp/                                       | $core.iam.idp                  |
| OrganizationMember | /v1/iam/organization_members/                      | $core.iam.organization_members |
| Organization       | /v1/iam/organizations/                             | $core.iam.organizations        |
| PermissionBinding  | /v1/iam/permission_bindings/                       | $core.iam.permission_bindings  |
| Permission         | /v1/iam/permissions/                               | $core.iam.permissions          |
| Project            | /v1/iam/projects/                                  | $core.iam.projects             |
| RoleBinding        | /v1/iam/role_bindings/                             | $core.iam.role_bindings        |
| Role               | /v1/iam/roles/                                     | $core.iam.roles                |
| User               | /v1/iam/users/                                     | $core.iam.users                |
| LB                 | /v1/network/lb/                                    | $core.network.lb               |
| BackendPool        | /v1/network/lb/{LBUuid}/backend_pools/             | $core.network.lb.backend_pools |
| Vhost              | /v1/network/lb/{LBUuid}/vhosts/                    | $core.network.lb.vhosts        |
| Route              | /v1/network/lb/{LBUuid}/vhosts/{VhostUuid}/routes/ | $core.network.lb.vhosts.routes |
| Certificate        | /v1/secret/certificates/                           | $core.secret.certificates      |
| Password           | /v1/secret/passwords/                              | $core.secret.passwords         |
| RSAKey             | /v1/secret/rsa_keys/                               | $core.secret.rsa_keys          |
| SSHKey             | /v1/secret/ssh_keys/                               | $core.secret.ssh_keys          |
| Rule               | /v1/security/rules/                                | $core.security.rules           |
| Profile            | /v1/vs/profiles/                                   | $core.vs.profiles              |
| Value              | /v1/vs/values/                                     | $core.vs.values                |
| Variable           | /v1/vs/variables/                                  | $core.vs.variables             |
