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

import logging
import typing as tp
import uuid as sys_uuid

from gcl_looper.services import basic
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.compute.dm import models as nm
from exordos_core.secret import constants as sc
from exordos_core.secret.dm import models

LOG = logging.getLogger(__name__)


class SecretServiceBuilder(basic.BasicService):
    def _get_new_certificates(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[models.Certificate]:
        return models.Certificate.get_new_certificates(limit=limit)

    def _get_changed_certificates(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[models.Certificate]:
        return models.Certificate.get_updated_certificates(limit=limit)

    def _get_deleted_certificates(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[ua_models.TargetResource]:
        return models.Certificate.get_deleted_certificates(limit=limit)

    def _get_new_ssh_keys(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[models.SSHKey]:
        return models.SSHKey.get_new_keys(limit=limit)

    def _get_changed_ssh_keys(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[models.SSHKey]:
        return models.SSHKey.get_updated_keys(limit=limit)

    def _get_deleted_ssh_keys(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.List[ua_models.TargetResource]:
        return models.SSHKey.get_deleted_keys(limit=limit)

    def _get_outdated_resources(
        self,
        kind: str,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.Dict[
        sys_uuid.UUID,  # Resource UUID
        tp.Tuple[ua_models.TargetResource, ua_models.Resource],
    ]:
        outdated = ua_models.OutdatedResource.objects.get_all(
            filters={"kind": dm_filters.EQ(kind)},
            limit=limit,
        )
        return {
            pair.target_resource.uuid: (
                pair.target_resource,
                pair.actual_resource,
            )
            for pair in outdated
        }

    def _get_outdated_secrets(
        self,
        model: models.Secret,
        uuids: tp.Collection[sys_uuid.UUID],
    ) -> tp.List[models.Secret]:
        return model.objects.get_all(
            filters={"uuid": dm_filters.In(str(p) for p in uuids)},
        )

    def _get_outdated_ssh_key_hosts(
        self,
        limit: int = c.DEFAULT_SQL_LIMIT,
    ) -> tp.Dict[
        sys_uuid.UUID,  # Master UUID
        tp.List[tp.Tuple[ua_models.TargetResource, ua_models.Resource]],
    ]:
        outdated = ua_models.OutdatedResource.objects.get_all(
            filters={"kind": dm_filters.EQ(sc.SSH_KEY_TARGET_KIND)},
            limit=limit,
        )
        key_map = {}
        for pair in outdated:
            key_map.setdefault(pair.target_resource.master, []).append(
                (pair.target_resource, pair.actual_resource)
            )

        return key_map

    def _get_outdated_ssh_keys(
        self,
        masters: tp.Collection[sys_uuid.UUID],
    ) -> tp.List[tp.Tuple[models.SSHKey, ua_models.TargetResource]]:
        ssh_key_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "uuid": dm_filters.In(m for m in masters),
                "kind": dm_filters.EQ(sc.SSH_KEY_KIND),
            },
            order_by={"uuid": "asc"},
        )

        ssh_keys = models.SSHKey.objects.get_all(
            filters={
                "uuid": dm_filters.In(m for m in masters),
            },
            order_by={"uuid": "asc"},
        )

        if len(ssh_keys) != len(ssh_key_resources):
            raise RuntimeError("Number of SSH keys and SSH key resources not equal")

        return list(zip(ssh_keys, ssh_key_resources))

    def _actualize_new_secrets(
        self, kind: str, secrets: tp.Collection[models.Secret]
    ) -> None:
        """Actualize new secrets."""
        # Just create resources for new secrets
        for secret in secrets:
            secret_resource = secret.to_ua_resource(kind)
            try:
                secret_resource.insert()
                secret.status = sc.SecretStatus.IN_PROGRESS.value
                secret.save()

                # Commit tracked_at ts
                secret_resource.tracked_at = secret.updated_at
                secret_resource.status = secret.status

                secret_resource.update()
                LOG.info("Certificate resource %s created", secret_resource.uuid)
            except Exception:
                LOG.exception("Error creating cert resource %s", secret.uuid)

    def _actualize_changed_secrets(
        self, kind: str, changed_secrets: tp.Dict[sys_uuid.UUID, models.Secret]
    ) -> None:
        """Actualize secrets changed by user."""
        if len(changed_secrets) == 0:
            return

        secret_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "uuid": dm_filters.In(str(p) for p in changed_secrets.keys()),
                "kind": dm_filters.EQ(kind),
            }
        )

        # Update every resource in accordance with the new secret
        for resource in secret_resources:
            secret = changed_secrets[resource.uuid]
            new_resource = secret.to_ua_resource(kind)

            # Update the original resource
            resource.update_value(new_resource)
            try:
                secret.status = sc.SecretStatus.IN_PROGRESS.value
                secret.save()

                # Commit the tracked_at timestamp
                resource.tracked_at = secret.updated_at
                resource.status = secret.status
                resource.update()
                LOG.debug("Secret resource %s updated", resource.uuid)
            except Exception:
                LOG.exception(
                    "Error updating secret resource %s",
                    resource.uuid,
                )

    def _actualize_outdated_secrets(
        self,
        kind: str,
        model: models.Secret,
        secret_handler: tp.Callable[
            [models.Secret, ua_models.TargetResource, ua_models.Resource], None
        ],
    ) -> None:
        """Actualize outdated secrets.

        It means some changes occurred in the system and the secrets
        are outdated now. For instance, their status is incorrect.
        """
        resource_map = self._get_outdated_resources(kind)

        if len(resource_map) == 0:
            return

        secrets = self._get_outdated_secrets(model, tuple(resource_map.keys()))

        for secret in secrets:
            target, actual = resource_map[secret.uuid]
            try:
                secret_handler(secret, target, actual)
                LOG.debug("Secret %s actualized", secret.uuid)
            except Exception:
                LOG.exception("Error actualizing secret %s", secret.uuid)

    def _actualize_deleted_secrets(
        self, secrets: tp.Collection[ua_models.TargetResource]
    ) -> None:
        """Actualize secrets deleted by user."""
        for secret in secrets:
            try:
                secret.delete()
                LOG.debug(
                    "Outdated resource(%s) %s deleted",
                    secret.kind,
                    secret.uuid,
                )
            except Exception:
                LOG.exception(
                    "Error deleting resource(%s) %s", secret.kind, secret.uuid
                )

    # Certificates

    def _actualize_new_certificates(self) -> None:
        """Actualize new certificates."""
        certs = self._get_new_certificates()
        self._actualize_new_secrets(sc.CERTIFICATE_KIND, certs)

    def _actualize_changed_certificates(self) -> None:
        """Actualize certificates changed by user."""
        changed_certs = {crt.uuid: crt for crt in self._get_changed_certificates()}
        self._actualize_changed_secrets(sc.CERTIFICATE_KIND, changed_certs)

    def _actualize_outdated_certificate(
        self,
        certificate: models.Certificate,
        target_resource: ua_models.TargetResource,
        actual_resource: ua_models.Resource,
    ) -> None:
        """Actualize outdated certificate."""
        certificate_updated = False
        status_updated = False
        actual_cert = models.Certificate.from_ua_resource(actual_resource)

        # `ACTIVE` only if the hash is the same
        if (
            actual_resource.status == sc.SecretStatus.ACTIVE
            and target_resource.hash == actual_resource.hash
        ):
            status_updated = True
        elif (
            actual_resource.status != sc.SecretStatus.ACTIVE
            and target_resource.status != actual_resource.status
        ):
            status_updated = True

        # Actualize certificate
        if (
            status_updated
            or actual_cert.key != certificate.key
            or actual_cert.cert != certificate.cert
            or actual_cert.expiration_at != certificate.expiration_at
        ):
            if status_updated:
                certificate.status = actual_cert.status
            certificate.key = actual_cert.key
            certificate.cert = actual_cert.cert
            certificate.expiration_at = actual_cert.expiration_at
            certificate.save()
            certificate_updated = True

        # Actualize resource
        if (
            certificate_updated
            or actual_resource.full_hash != target_resource.full_hash
        ):
            if status_updated:
                target_resource.status = actual_resource.status
            target_resource.full_hash = actual_resource.full_hash
            target_resource.tracked_at = certificate.updated_at
            target_resource.update()

    def _actualize_outdated_certificates(self) -> None:
        """Actualize outdated certificates.

        It means some changes occurred in the system and the certificates
        are outdated now. For instance, their status is incorrect.
        """
        self._actualize_outdated_secrets(
            sc.CERTIFICATE_KIND,
            models.Certificate,
            self._actualize_outdated_certificate,
        )

    def _actualize_deleted_certificates(self) -> None:
        """Actualize certificates deleted by user."""
        deleted_certificates = self._get_deleted_certificates()
        self._actualize_deleted_secrets(deleted_certificates)

    # SSH Keys

    def _actualize_new_ssh_key(
        self,
        key: models.SSHKey,
        target_nodes: tp.List[nm.Node],
    ) -> None:
        # Validate the owners exist
        # FIXME(akremenetsky): Only nodes as owners are supported for now.
        # It will be updated when sets appear.

        # FIXME(akremenetsky): Seems the key may be deleted since its
        # owners are absent. May be it will be better to control this
        # behavior via an additional option in the target model but for
        # now just delete this config.
        if not key.target.are_owners_alive():
            LOG.error("SSH key %s has no owners, delete it.", key.uuid)
            key.delete()
            return

        # FIXME(akremenetsky): Should we set status `IN_PROGRESS` here?
        # Let's wait at least one node to be created
        if len(target_nodes) == 0:
            return

        key_resource = key.to_ua_resource(sc.SSH_KEY_KIND)
        key_resource.insert()

        # Key for every node.
        # There is a 'master' key with the `SSH_KEY_KIND` kind It's a common
        # key for every nodes. There is a `SSH_KEY_TARGET_KIND` resources
        # that acts as slaves for per nodes.
        for node in target_nodes:
            key_host_resource = key.to_host_resource(
                master=key_resource.uuid,
                node=node.uuid,
                status=sc.SecretStatus.IN_PROGRESS,
            )
            key_host_resource.insert()

        key.status = sc.SecretStatus.IN_PROGRESS.value
        key.save()

        key_resource.tracked_at = key.updated_at
        key_resource.status = key.status
        key_resource.update()
        LOG.debug("SSH key resource %s created", key_resource.uuid)

    def _actualize_new_ssh_keys(
        self, keys: tp.Collection[models.SSHKey] = tuple()
    ) -> None:
        """Actualize new SSH keys."""
        keys = keys or self._get_new_ssh_keys()

        if len(keys) == 0:
            return

        # Collect all target nodes
        target_nodes = {n for key in keys for n in key.target_nodes()}
        nodes = {
            n.uuid: n
            for n in nm.Node.objects.get_all(
                filters={"uuid": dm_filters.In(target_nodes)}
            )
        }

        for key in keys:
            # Collect all available nodes for the key
            target_nodes = tuple(nodes[n] for n in key.target_nodes() if n in nodes)
            try:
                self._actualize_new_ssh_key(key, target_nodes)
            except Exception:
                LOG.exception("Error actualizing SSH key %s", key.uuid)

    def _actualize_changed_ssh_keys(self) -> None:
        """Actualize SSH keys changed by user."""
        changed_keys = self._get_changed_ssh_keys()

        if len(changed_keys) == 0:
            return

        # The simplest implementation. Update through recreation.
        keys_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "uuid": dm_filters.In(str(uc.uuid) for uc in changed_keys),
                "kind": dm_filters.EQ(sc.SSH_KEY_KIND),
            }
        )
        key_host_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "master": dm_filters.In(str(k.uuid) for k in keys_resources),
                "kind": dm_filters.EQ(sc.SSH_KEY_TARGET_KIND),
            }
        )

        for key in key_host_resources + keys_resources:
            key.delete()
            LOG.debug("Outdated resource SSH key %s deleted", key.uuid)

        # Now they are new configs
        self._actualize_new_ssh_keys(changed_keys)

    def _actualize_outdated_ssh_key(
        self,
        key: models.SSHKey,
        key_resource: ua_models.TargetResource,
        host_keys: tp.Collection[
            tp.Tuple[ua_models.TargetResource, ua_models.Resource]
        ],
    ) -> None:
        """Actualize outdated SSH keys."""
        # Update target keys with actual information from the DP.
        for target, actual in host_keys:
            target.full_hash = actual.full_hash

            # `ACTIVE` only if the hash is the same
            if actual.status == sc.SecretStatus.ACTIVE and target.hash == actual.hash:
                target.status = actual.status
            elif (
                actual.status != sc.SecretStatus.ACTIVE
                and target.status != actual.status
            ):
                target.status = actual.status
            target.update()
            LOG.debug("Outdated host SSH key %s actualized", target.uuid)

        # Actualize status if needed.
        status = None
        if all(r.status == sc.SecretStatus.ACTIVE for r, _ in host_keys):
            status = sc.SecretStatus.ACTIVE
        elif any(r.status == sc.SecretStatus.NEW for r, _ in host_keys):
            status = sc.SecretStatus.NEW
        elif any(r.status == sc.SecretStatus.IN_PROGRESS for r, _ in host_keys):
            status = sc.SecretStatus.IN_PROGRESS

        if status is not None and key.status != status:
            key.status = status.value
            key.update()
            key_resource.status = key.status
            key_resource.tracked_at = key.updated_at
            key_resource.update()

    def _actualize_outdated_ssh_keys(self) -> None:
        """Actualize outdated SSH keys.

        It means some changes occurred in the system and the key
        are outdated now. For instance, their status is key.
        """
        resource_map = self._get_outdated_ssh_key_hosts()

        if len(resource_map) == 0:
            return

        ssh_keys = self._get_outdated_ssh_keys(tuple(resource_map.keys()))

        for key, key_resource in ssh_keys:
            host_keys = resource_map[key_resource.uuid]
            try:
                self._actualize_outdated_ssh_key(key, key_resource, host_keys)
            except Exception:
                LOG.exception("Error actualizing SSH key %s", key.uuid)

    def _actualize_deleted_ssh_keys(self) -> None:
        """Actualize SSH keys deleted by user."""
        deleted_key_resources = self._get_deleted_ssh_keys()

        if len(deleted_key_resources) == 0:
            return

        key_host_resources = ua_models.TargetResource.objects.get_all(
            filters={
                "master": dm_filters.In(k.uuid for k in deleted_key_resources),
                "kind": dm_filters.EQ(sc.SSH_KEY_TARGET_KIND),
            }
        )

        for resource in key_host_resources + deleted_key_resources:
            try:
                resource.delete()
                LOG.debug("Outdated resource SSH key %s deleted", resource.uuid)
            except Exception:
                LOG.exception("Error deleting resource %s", resource.uuid)

    def _actualize_certificates(self) -> None:
        try:
            self._actualize_new_certificates()
        except Exception:
            LOG.exception("Error actualizing new certificates")

        try:
            self._actualize_changed_certificates()
        except Exception:
            LOG.exception("Error actualizing changed certificates")

        try:
            self._actualize_outdated_certificates()
        except Exception:
            LOG.exception("Error actualizing outdated certificates")

        try:
            self._actualize_deleted_certificates()
        except Exception:
            LOG.exception("Error actualizing deleted certificates")

    def _actualize_ssh_keys(self) -> None:
        try:
            self._actualize_new_ssh_keys()
        except Exception:
            LOG.exception("Error actualizing new SSH keys")

        try:
            self._actualize_changed_ssh_keys()
        except Exception:
            LOG.exception("Error actualizing changed SSH keys")

        try:
            self._actualize_outdated_ssh_keys()
        except Exception:
            LOG.exception("Error actualizing outdated SSH keys")

        try:
            self._actualize_deleted_ssh_keys()
        except Exception:
            LOG.exception("Error actualizing deleted SSH keys")

    def _iteration(self) -> None:
        with contexts.Context().session_manager():
            self._actualize_certificates()
            self._actualize_ssh_keys()
