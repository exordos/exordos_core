#    Copyright 2026 Genesis Corporation.
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

import datetime
import uuid as sys_uuid

import bazooka
from bazooka import exceptions as bazooka_exc
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.clients.backend import db as db_back
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import core as ua_core_drivers
import pytest

from exordos_core.common import constants as c
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam import service as iam_service
from exordos_core.user_api.iam.dm import models as iam_models

TOKEN_KIND = "em_core_iam_tokens"
DAY = 24 * 60 * 60


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _get_token(uuid):
    return iam_models.ManagedToken.objects.get_one(filters={"uuid": uuid})


def _regenerate_url(client, uuid):
    return client.build_resource_uri(["iam/tokens", uuid, "actions/regenerate/invoke"])


def _claims(token):
    # Verified with the key of the client the token names
    return token.iam_client.get_token_algorithm().decode(
        token.get_access_token(), ignore_audience=True
    )


class TestTokens:
    @pytest.fixture()
    def admin_client(self, user_api_client, auth_user_admin):
        return user_api_client(auth_user_admin)

    @pytest.fixture()
    def service_user(self, admin_client):
        return admin_client.create_user(
            username="token_user",
            password="12345678",
        )

    @pytest.fixture()
    def create_token(self, admin_client, service_user, default_client_uuid):
        url = admin_client.build_collection_uri(["iam/tokens"])

        def _create(**kwargs):
            body = {
                "user": f"/v1/iam/users/{service_user['uuid']}",
                "iam_client": f"/v1/iam/clients/{default_client_uuid}",
                "expiration_delta": DAY,
            }
            body.update(kwargs)
            return admin_client.post(url, json=body)

        return _create

    def test_create_token(self, create_token, service_user, default_client_id):
        response = create_token()
        output = response.json()

        assert response.status_code == 201
        assert output["user"] == f"/v1/iam/users/{service_user['uuid']}"
        assert output["expiration_delta"] == DAY
        assert output["auto_renew"] is True
        # Answered once, on the create: this is the only time an account
        # holding it sees the signed token
        assert output["access_token"] == _get_token(output["uuid"]).access_token
        # The refresh token id and the marker never leave the server
        assert "refresh_token_uuid" not in output
        assert "managed" not in output

        token = _get_token(output["uuid"])
        assert token.managed is True
        assert _claims(token)["aud"] == default_client_id
        lifetime = token.expiration_at - _now()
        assert datetime.timedelta(hours=23) < lifetime <= datetime.timedelta(days=1)

    def test_create_token_rejects_short_lifetime(self, create_token):
        with pytest.raises(bazooka_exc.BadRequestError):
            create_token(expiration_delta=59)

    def test_login_sessions_are_out_of_reach(self, admin_client, service_user):
        login_token = iam_models.Token(
            user=iam_models.User.objects.get_one(
                filters={"uuid": service_user["uuid"]}
            ),
            iam_client=iam_models.IamClient.objects.get_one(
                filters={"uuid": c.ZERO_UUID}
            ),
            expiration_delta=datetime.timedelta(seconds=10),
        )
        login_token.insert()
        url = admin_client.build_resource_uri(["iam/tokens", str(login_token.uuid)])

        tokens = admin_client.get(
            admin_client.build_collection_uri(["iam/tokens"])
        ).json()

        assert str(login_token.uuid) not in [t["uuid"] for t in tokens]
        with pytest.raises(bazooka_exc.NotFoundError):
            admin_client.get(url)
        with pytest.raises(bazooka_exc.NotFoundError):
            admin_client.put(url, json={"expiration_delta": DAY})
        with pytest.raises(bazooka_exc.NotFoundError):
            admin_client.delete(url)

    def test_token_authenticates_its_user(
        self, user_api, create_token, service_user, default_client_uuid
    ):
        output = create_token().json()
        access_token = _get_token(output["uuid"]).get_access_token()

        response = bazooka.Client().get(
            f"{user_api.get_endpoint()}v1/iam/clients/{default_client_uuid}/actions/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.json()["user"]["uuid"] == service_user["uuid"]

    def test_token_signed_by_own_client(self, user_api, admin_client, create_token):
        signing_key = admin_client.post(
            admin_client.build_collection_uri(["secret/passwords"]),
            json={
                "name": "token-signing-key",
                "project_id": str(c.EM_PROJECT_ID),
                "method": "MANUAL",
                "value": "a-signing-key-of-the-token-client",
            },
        ).json()
        iam_client = admin_client.create_iam_client(
            name="token-client",
            client_id="token-client",
            secret="12345678",
            signature_algorithm={
                "kind": "HS256",
                "secret_uuid": signing_key["uuid"],
                "previous_secret_uuid": None,
            },
        )
        output = create_token().json()

        # Switching the client moves the claims along with the signature
        admin_client.put(
            admin_client.build_resource_uri(["iam/tokens", output["uuid"]]),
            json={"iam_client": f"/v1/iam/clients/{iam_client['uuid']}"},
        )
        token = _get_token(output["uuid"])
        claims = _claims(token)

        assert claims["jti"] == str(token.uuid)
        assert claims["aud"] == "token-client"
        assert claims["iss"].endswith(f"iam/clients/{iam_client['uuid']}")
        response = bazooka.Client().get(
            f"{user_api.get_endpoint()}v1/iam/clients/{iam_client['uuid']}/actions/me",
            headers={"Authorization": f"Bearer {token.get_access_token()}"},
        )
        assert response.status_code == 200

    def test_explicit_claims_are_kept(self, create_token):
        output = create_token(
            issuer="https://issuer.example", audience="exporter"
        ).json()

        claims = _claims(_get_token(output["uuid"]))

        assert claims["iss"] == "https://issuer.example"
        assert claims["aud"] == "exporter"

    def test_fixed_term_token_is_not_renewed(self, admin_client, create_token):
        output = create_token(expiration_delta=120, auto_renew=False).json()
        token = _get_token(output["uuid"])
        token.expiration_at = _now() + datetime.timedelta(seconds=10)
        token.update()
        expiration_at = token.expiration_at

        iam_service.TokenRenewalService()._iteration()

        assert _get_token(output["uuid"]).expiration_at == expiration_at

    def test_renewal_cannot_be_turned_on_afterwards(self, admin_client, create_token):
        output = create_token(expiration_delta=120, auto_renew=False).json()

        with pytest.raises(bazooka_exc.ForbiddenError):
            admin_client.put(
                admin_client.build_resource_uri(["iam/tokens", output["uuid"]]),
                json={"auto_renew": True},
            )

        assert _get_token(output["uuid"]).auto_renew is False

    def test_read_hides_access_token(self, admin_client, create_token):
        output = create_token().json()

        tokens = admin_client.get(
            admin_client.build_collection_uri(["iam/tokens"])
        ).json()
        token = admin_client.get(
            admin_client.build_resource_uri(["iam/tokens", output["uuid"]])
        ).json()

        assert output["uuid"] in [t["uuid"] for t in tokens]
        assert all("access_token" not in t for t in tokens)
        assert "access_token" not in token

    def test_user_cannot_manage_tokens(
        self, user_api_client, auth_test1_user, service_user, default_client_uuid
    ):
        client = user_api_client(auth_test1_user)
        url = client.build_collection_uri(["iam/tokens"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(
                url,
                json={
                    "user": f"/v1/iam/users/{service_user['uuid']}",
                    "iam_client": f"/v1/iam/clients/{default_client_uuid}",
                },
            )

    def test_update_scope_moves_project(self, admin_client, create_token):
        organization = admin_client.create_organization(name="token-org")
        project = admin_client.create_project(
            organization_uuid=organization["uuid"],
            name="token-project",
        )
        output = create_token().json()
        assert output.get("project") is None

        response = admin_client.put(
            admin_client.build_resource_uri(["iam/tokens", output["uuid"]]),
            json={"scope": f"project:{project['uuid']}"},
        )

        assert response.status_code == 200
        assert response.json()["project"] == f"/v1/iam/projects/{project['uuid']}"

    def test_update_lifetime_restarts_expiration(self, admin_client, create_token):
        output = create_token().json()

        admin_client.put(
            admin_client.build_resource_uri(["iam/tokens", output["uuid"]]),
            json={"expiration_delta": 7 * DAY},
        )

        lifetime = _get_token(output["uuid"]).expiration_at - _now()
        assert lifetime > datetime.timedelta(days=6)

    def test_regenerate_answers_with_a_new_token(self, admin_client, create_token):
        output = create_token().json()
        previous = output["access_token"]

        regenerated = admin_client.post(
            _regenerate_url(admin_client, output["uuid"]),
            json={},
        ).json()

        token = _get_token(output["uuid"])
        # The token keeps its identity, and only the value it signs moves
        assert token.generation == 1
        assert regenerated["access_token"] == token.access_token
        assert regenerated["access_token"] != previous
        assert _claims(token)["jti"] == output["uuid"]

    def test_regenerate_refuses_the_previous_token(
        self, user_api, admin_client, create_token, default_client_uuid
    ):
        output = create_token().json()
        previous = output["access_token"]
        url = (
            f"{user_api.get_endpoint()}v1/iam/clients/{default_client_uuid}/actions/me"
        )

        # The token works right up to the regeneration
        bazooka.Client().get(url, headers={"Authorization": f"Bearer {previous}"})
        regenerated = admin_client.post(
            _regenerate_url(admin_client, output["uuid"]),
            json={},
        ).json()

        with pytest.raises(bazooka_exc.UnauthorizedError):
            bazooka.Client().get(url, headers={"Authorization": f"Bearer {previous}"})
        # ...and the one it answered with takes over
        bazooka.Client().get(
            url, headers={"Authorization": f"Bearer {regenerated['access_token']}"}
        )

    def test_regenerate_restarts_the_lifetime(self, admin_client, create_token):
        output = create_token().json()
        token = _get_token(output["uuid"])
        token.expiration_at = _now() + datetime.timedelta(minutes=1)
        token.update()

        admin_client.post(
            _regenerate_url(admin_client, output["uuid"]),
            json={},
        )

        token = _get_token(output["uuid"])
        lifetime = token.expiration_at - _now()
        assert datetime.timedelta(hours=23) < lifetime <= datetime.timedelta(days=1)
        # The lifetime itself is what it was issued as
        assert token.expiration_delta == datetime.timedelta(seconds=DAY)

    def test_renewal_keeps_the_generation(self, admin_client, create_token):
        output = create_token().json()

        token = _get_token(output["uuid"])
        token.renew(_now() + datetime.timedelta(minutes=1))

        # A renewal must leave the token already handed out in use, so it
        # is only a regeneration that moves the generation on
        assert _get_token(output["uuid"]).generation == 0

    def test_delete_token(self, admin_client, create_token):
        output = create_token().json()

        admin_client.delete(
            admin_client.build_resource_uri(["iam/tokens", output["uuid"]])
        )

        assert (
            iam_models.Token.objects.get_one_or_none(filters={"uuid": output["uuid"]})
            is None
        )


class TestOwnTokens:
    """An account issues and reads the tokens of its own, and no others."""

    @pytest.fixture()
    def owner_client(self, user_api_client, auth_test1_user):
        return user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_TOKEN_CREATE,
                iam_c.PERMISSION_TOKEN_READ,
                iam_c.PERMISSION_TOKEN_DELETE,
            ],
        )

    @staticmethod
    def _body(**kwargs):
        body = {"iam_client": f"/v1/iam/clients/{c.ZERO_UUID}", "expiration_delta": DAY}
        body.update(kwargs)
        return body

    def test_issues_a_token_for_itself(self, owner_client, auth_test1_user):
        url = owner_client.build_collection_uri(["iam/tokens"])

        response = owner_client.post(url, json=self._body())
        output = response.json()

        assert response.status_code == 201
        assert output["user"] == f"/v1/iam/users/{auth_test1_user.uuid}"
        assert output["access_token"]

    def test_cannot_issue_a_token_for_another_user(
        self, owner_client, user_api_client, auth_user_admin
    ):
        other = user_api_client(auth_user_admin).create_user(
            username="other_user",
            password="12345678",
        )
        url = owner_client.build_collection_uri(["iam/tokens"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            owner_client.post(
                url, json=self._body(user=f"/v1/iam/users/{other['uuid']}")
            )

    def test_reads_only_its_own_tokens(
        self, owner_client, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        admin_url = admin_client.build_collection_uri(["iam/tokens"])
        # Issued for the admin itself: the body names nobody, so the
        # token belongs to the account asking for it
        admin_token = admin_client.post(admin_url, json=self._body()).json()
        own = owner_client.post(
            owner_client.build_collection_uri(["iam/tokens"]), json=self._body()
        ).json()

        listed = owner_client.get(
            owner_client.build_collection_uri(["iam/tokens"])
        ).json()

        assert admin_token["user"] != own["user"]
        assert [t["uuid"] for t in listed] == [own["uuid"]]
        # The token of another account is not there to be read or deleted
        other_url = owner_client.build_resource_uri(["iam/tokens", admin_token["uuid"]])
        with pytest.raises(bazooka_exc.NotFoundError):
            owner_client.get(other_url)
        with pytest.raises(bazooka_exc.NotFoundError):
            owner_client.delete(other_url)
        # An admin sees both
        assert len(admin_client.get(admin_url).json()) == 2


class TestTokenRenewal:
    @pytest.fixture()
    def admin(self, user_api):
        return iam_models.User.objects.get_one(
            filters={"name": c.DEFAULT_ADMIN_USERNAME}
        )

    @pytest.fixture()
    def iam_client(self, user_api):
        return iam_models.IamClient.objects.get_one(filters={"uuid": c.ZERO_UUID})

    def _token(self, model, admin, iam_client, remaining):
        token = model(
            user=admin,
            iam_client=iam_client,
            expiration_delta=datetime.timedelta(hours=1),
            expiration_at=_now() + remaining,
        )
        token.insert()
        return token

    def test_renews_token_past_half_life(self, admin, iam_client):
        token = self._token(
            iam_models.ManagedToken, admin, iam_client, datetime.timedelta(minutes=10)
        )
        old_access_token = token.get_access_token()

        iam_service.TokenRenewalService()._iteration()

        renewed = _get_token(token.uuid)
        assert renewed.expiration_at - _now() > datetime.timedelta(minutes=59)
        assert renewed.get_access_token() != old_access_token

    def test_keeps_token_before_half_life(self, admin, iam_client):
        token = self._token(
            iam_models.ManagedToken, admin, iam_client, datetime.timedelta(minutes=50)
        )

        iam_service.TokenRenewalService()._iteration()

        assert _get_token(token.uuid).expiration_at == token.expiration_at

    def test_keeps_login_token(self, admin, iam_client):
        token = self._token(
            iam_models.Token, admin, iam_client, datetime.timedelta(minutes=10)
        )
        assert token.managed is False
        assert token.auto_renew is False

        iam_service.TokenRenewalService()._iteration()

        assert _get_token(token.uuid).expiration_at == token.expiration_at


class TestTokenCoreAgent:
    """The path a manifest token takes: the core agent database driver."""

    @pytest.fixture()
    def driver(self, user_api, tmp_path):
        spec = db_back.ModelSpec(model=iam_models.ManagedToken, kind=TOKEN_KIND)
        driver = ua_core_drivers.DatabaseCapabilityDriver(
            model_specs=[spec],
            target_fields_storage_path=str(tmp_path / "target_fields.json"),
        )
        driver.start()
        return driver

    @pytest.fixture()
    def resource(self, user_api):
        admin = iam_models.User.objects.get_one(
            filters={"name": c.DEFAULT_ADMIN_USERNAME}
        )
        uuid = sys_uuid.uuid4()
        value = {
            "uuid": str(uuid),
            "user": str(admin.uuid),
            "iam_client": str(c.ZERO_UUID),
            "expiration_delta": DAY,
        }
        return ua_models.TargetResource(
            uuid=uuid,
            kind=TOKEN_KIND,
            value=value,
            hash=ua_utils.calculate_hash(value),
        )

    def test_reports_access_token(self, driver, resource):
        # A login session shares the table and must stay out of the list
        admin = iam_models.User.objects.get_one(
            filters={"name": c.DEFAULT_ADMIN_USERNAME}
        )
        iam_models.Token(
            user=admin,
            iam_client=iam_models.IamClient.objects.get_one(
                filters={"uuid": c.ZERO_UUID}
            ),
        ).insert()
        created = driver.create(resource)
        listed = driver.list(TOKEN_KIND)

        token = _get_token(resource.uuid)
        assert token.managed is True
        assert created.value["access_token"] == token.get_access_token()
        assert [r.uuid for r in listed] == [resource.uuid]
        # A stable token hashes the same on every iteration
        assert listed[0].full_hash == created.full_hash
        assert listed[0].hash == resource.hash

    def test_renewal_changes_reported_state(self, driver, resource):
        created = driver.create(resource)

        token = _get_token(resource.uuid)
        token.renew(_now() + datetime.timedelta(minutes=1))
        listed = driver.list(TOKEN_KIND)

        # The target is still met, but consumers see the new access token
        assert listed[0].hash == resource.hash
        assert listed[0].full_hash != created.full_hash
