#    Copyright 2025-2026 Genesis Corporation.
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

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder
from restalchemy.dm import filters as dm_filters

from exordos_core.secret import constants as sc
from exordos_core.secret.dm import models

LOG = logging.getLogger(__name__)


class Secret(
    models.Secret,
    ua_models.InstanceMixin,
    ua_models.ReadinessMixin,
):
    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.SECRET_KIND

    def is_ready_to_actualize(self) -> bool:
        # A secret with neither a value nor a default has nothing to
        # deliver. Hold it in NEW instead of pushing an empty secret to
        # the data plane: an element consuming it waits for the value.
        return self.effective_value is not None


class Password(
    models.Password,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.PASSWORD_KIND


class Certificate(
    models.Certificate,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.CERTIFICATE_KIND


class RSAKey(
    models.RSAKey,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.RSA_KEY_KIND


class SSHHostKey(
    models.SSHHostKey,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """The key as it is delivered to a single node."""

    __init_resource_status__ = sc.SecretStatus.IN_PROGRESS.value

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.SSH_KEY_TARGET_KIND

    def get_resource_target_fields(self) -> tp.Set[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        # `agent_uuid` only tells the builder which node the key belongs
        # to. Keep it out of the target value: the data plane knows
        # nothing about it and its hash would never match ours.
        return {
            "uuid",
            "user",
            "authorized_keys",
            "target_public_key",
        }


class SSHKey(
    models.SSHKey,
    ua_models.InstanceWithDerivativesMixin,
):
    # A key is delivered per node: the instance resource is a common
    # 'master' key and every node gets a derivative of it.
    __derivative_model_map__ = {
        sc.SSH_KEY_TARGET_KIND: SSHHostKey,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.SSH_KEY_KIND


class SecretBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=Secret,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def can_update_instance_resource(
        self, instance: Secret, resource: ua_models.TargetResource
    ) -> bool:
        if instance.is_ready_to_update():
            return True

        # The value was cleared and no default stands behind it. Holding
        # the update would leave the old value on the data plane, so
        # withdraw it: without a target resource the agent drops the
        # stored secret, and the instance waits in NEW like one that
        # never had a value.
        resource.delete()
        return False

    def actualize_outdated_instance(
        self,
        current_instance: Secret,
        actual_instance: Secret,
    ) -> None:
        # The value only travels from the control plane to the data
        # plane, so the status is all there is to bring back.
        if current_instance.status != actual_instance.status:
            current_instance.status = actual_instance.status
            current_instance.save()


class PasswordBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=Password,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def actualize_outdated_instance(
        self,
        current_instance: Password,
        actual_instance: Password,
    ) -> None:
        status_updated = current_instance.status != actual_instance.status
        if status_updated:
            current_instance.status = actual_instance.status

        password_updated = current_instance.value != actual_instance.value
        if password_updated:
            current_instance.value = actual_instance.value

        if status_updated or password_updated:
            current_instance.save()


class CertificateBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=Certificate,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def actualize_outdated_instance(
        self,
        current_instance: Certificate,
        actual_instance: Certificate,
    ) -> None:
        status_updated = current_instance.status != actual_instance.status
        if status_updated:
            current_instance.status = actual_instance.status

        cert_updated = (
            current_instance.key != actual_instance.key
            or current_instance.cert != actual_instance.cert
            or current_instance.expiration_at != actual_instance.expiration_at
        )
        if cert_updated:
            current_instance.key = actual_instance.key
            current_instance.cert = actual_instance.cert
            current_instance.expiration_at = actual_instance.expiration_at

        # Detect domain changes from the DP
        domains_updated = sorted(current_instance.domains) != sorted(
            actual_instance.domains
        )
        if domains_updated:
            current_instance.domains = actual_instance.domains

        if status_updated or cert_updated or domains_updated:
            current_instance.save()


class SSHKeyBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=SSHKey,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def can_create_instance_resource(self, instance: SSHKey) -> bool:
        return self._can_deliver_instance(instance)

    def can_update_instance_resource(
        self,
        instance: SSHKey,
        resource: ua_models.TargetResource,
    ) -> bool:
        # The target is writable, so the owners have to be validated again
        # on every update, not only on creation.
        return self._can_deliver_instance(instance)

    def _can_deliver_instance(self, instance: SSHKey) -> bool:
        # Validate the owners exist
        # FIXME(akremenetsky): Only nodes as owners are supported for now.
        # It will be updated when sets appear.

        # FIXME(akremenetsky): Seems the key may be deleted since its
        # owners are absent. May be it will be better to control this
        # behavior via an additional option in the target model but for
        # now just delete this config.
        if not instance.target.are_owners_alive():
            LOG.error("SSH key %s has no owners, delete it.", instance.uuid)
            instance.delete()
            return False

        # Let's wait at least one node to be created
        return bool(instance.target_nodes())

    def create_instance_derivatives(
        self, instance: SSHKey
    ) -> tp.Collection[SSHHostKey]:
        return [
            SSHHostKey(
                # The key is the same on every node, the resource is not.
                uuid=sys_uuid.uuid5(instance.uuid, str(node)),
                user=instance.user,
                authorized_keys=instance.authorized_keys,
                target_public_key=instance.target_public_key,
                agent_uuid=node,
            )
            for node in instance.target_nodes()
        ]

    def post_update_instance_resource(
        self,
        instance: SSHKey,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = (),
    ) -> None:
        # An update does not have to touch a per node field, `name` for
        # instance. Then no derivative resource changes, so nothing becomes
        # outdated and the roll-up below is never called again. The status
        # has to be rolled up here too, otherwise such a key would stay in
        # the status forced here forever.
        self._roll_up_status(instance, self.create_instance_derivatives(instance))

    def actualize_outdated_instance_derivatives(
        self,
        instance: SSHKey,
        derivative_pairs: tp.Collection[tp.Tuple[SSHHostKey, tp.Optional[SSHHostKey]]],
    ) -> tp.Collection[SSHHostKey]:
        host_keys = tuple(t for t, _ in derivative_pairs)
        self._roll_up_status(instance, host_keys)

        return host_keys

    def _roll_up_status(
        self,
        instance: SSHKey,
        host_keys: tp.Collection[SSHHostKey],
    ) -> None:
        """Roll the per node statuses up into the status of the key."""
        statuses = self._host_key_statuses(host_keys)

        if all(s == sc.SecretStatus.ACTIVE for s in statuses):
            instance.status = sc.SecretStatus.ACTIVE.value
        elif any(s == sc.SecretStatus.NEW for s in statuses):
            instance.status = sc.SecretStatus.NEW.value
        elif any(s == sc.SecretStatus.IN_PROGRESS for s in statuses):
            instance.status = sc.SecretStatus.IN_PROGRESS.value

    def _host_key_statuses(self, host_keys: tp.Collection[SSHHostKey]) -> tp.List[str]:
        """Return what the data plane reports for every host key.

        A host key carries no status in its value, so the status cannot
        be read from the derivative itself: it lives on the resource the
        agent reports back.
        """
        actual_resources = {
            r.uuid: r
            for r in ua_models.Resource.objects.get_all(
                filters={
                    "uuid": dm_filters.In([str(k.uuid) for k in host_keys]),
                    "kind": dm_filters.EQ(sc.SSH_KEY_TARGET_KIND),
                }
            )
        }

        statuses = []
        for host_key in host_keys:
            resource = actual_resources.get(host_key.uuid)

            # The node has not answered yet, or it still carries an older
            # key. Either way the key is not delivered, the same way the
            # host resource itself is held back from `ACTIVE`.
            if resource is None or (
                resource.status == sc.SecretStatus.ACTIVE
                and resource.hash != host_key.to_ua_resource().hash
            ):
                statuses.append(sc.SecretStatus.IN_PROGRESS.value)
            else:
                statuses.append(resource.status)

        return statuses
