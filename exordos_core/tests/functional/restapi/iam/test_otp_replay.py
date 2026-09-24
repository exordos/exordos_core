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

import contextlib

import bazooka
from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pyotp
import pytest
from restalchemy.common import contexts as ra_contexts

from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam.dm import models as iam_models


class TestOTPReplay(base.BaseIamResourceTest):
    """Replay protection against a real database.

    The unit tests cover the flow; these exist because the guarantee itself
    is a WHERE clause, and only Postgres can confirm it holds.
    """

    USERNAME = "otp_replay"
    PASSWORD = "testtest"

    @contextlib.contextmanager
    def _otp_user(self, user_api_client, auth_user_admin):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
                iam_c.PERMISSION_USER_READ_ALL,
                iam_c.PERMISSION_USER_DELETE_ALL,
            ],
        )
        created = client.create_user(
            username=self.USERNAME,
            password=self.PASSWORD,
        )

        user = iam_models.User.objects.get_one(filters={"uuid": created["uuid"]})
        user.otp_secret = pyotp.random_base32()
        user.otp_enabled = True
        user.save()

        try:
            yield client, user
        finally:
            # A test may have deleted the user itself.
            if iam_models.User.objects.get_one_or_none(
                filters={"uuid": created["uuid"]}
            ):
                client.delete_user(created["uuid"])

    def test_code_is_accepted_once_and_rejected_afterwards(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            code = pyotp.TOTP(user.otp_secret).now()

            assert user.validate_otp(code) is True
            assert user.validate_otp(code) is False

    def test_a_concurrent_request_sees_the_burned_counter(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            code = pyotp.TOTP(user.otp_secret).now()
            assert user.validate_otp(code) is True

            # A second request handles its own copy of the row, so only the
            # database can tell it the code is already spent.
            same_user = iam_models.User.objects.get_one(
                filters={"uuid": user.uuid},
            )
            assert same_user.validate_otp(code) is False

    def test_counter_is_persisted(self, user_api, user_api_client, auth_user_admin):
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            assert user.validate_otp(pyotp.TOTP(user.otp_secret).now()) is True

            counter = iam_models.UserOtpCounter.objects.get_one(
                filters={"uuid": user.uuid},
            )
            assert counter.last_counter == user.last_otp_counter()

    def test_user_without_a_counter_is_accepted(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            assert user.last_otp_counter() is None

            assert user.validate_otp(pyotp.TOTP(user.otp_secret).now()) is True

    def test_login_does_not_touch_the_user_row(
        self, user_api, user_api_client, auth_user_admin
    ):
        # The counter lives apart from iam_users precisely so that signing
        # in does not look like a change to the account.
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            before = iam_models.User.objects.get_one(filters={"uuid": user.uuid})

            assert user.validate_otp(pyotp.TOTP(user.otp_secret).now()) is True

            after = iam_models.User.objects.get_one(filters={"uuid": user.uuid})
            assert after.updated_at == before.updated_at

    def test_counter_goes_away_with_its_user(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (client, user):
            assert user.validate_otp(pyotp.TOTP(user.otp_secret).now()) is True
            client.delete_user(str(user.uuid))

            assert user.last_otp_counter() is None

    def test_turning_otp_off_and_on_again_keeps_the_spent_steps(
        self, user_api, user_api_client, auth_user_admin, context_storage
    ):
        """Switching OTP off must not hand back the steps already spent.

        A time step comes from the clock, not from the secret, so the
        counter has to outlive both the old secret and the disable/enable
        cycle. Were it cleared, a code intercepted before the switch-off
        could be presented once more against the new secret.
        """
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            assert user.validate_otp(pyotp.TOTP(user.otp_secret).now()) is True
            spent = user.last_otp_counter()
            assert spent is not None

            # disable_otp and enable_otp re-check the password, and hashing
            # it needs the global salt the service keeps in its context.
            ctx = ra_contexts.ContextWithStorage(context_storage=context_storage)
            with ctx.context_manager():
                user.disable_otp(self.PASSWORD)
                assert user.last_otp_counter() == spent

                user.enable_otp(self.PASSWORD)
                assert user.last_otp_counter() == spent

                user.activate_otp(pyotp.TOTP(user.otp_secret).now())

            assert user.otp_enabled is True
            # Activation may carry the counter forward, never back.
            assert user.last_otp_counter() >= spent

            # Asked directly rather than through a generated code: whether a
            # fresh code still falls in the spent step depends on where the
            # wall clock happens to sit, and that must not decide the test.
            assert user._burn_otp_counter(spent) is False

    def test_a_replayed_code_is_rejected_by_the_login_endpoint(
        self,
        user_api,
        user_api_client,
        auth_user_admin,
        default_client_uuid,
        default_client_id,
        default_client_secret,
    ):
        """The burn has to outlive the request that made it.

        Every other test here calls validate_otp directly, which gets a
        session of its own and commits on the way out. Inside a request the
        burn joins that request's transaction instead, so a real login is
        the only thing that shows it is actually committed rather than
        rolled back with the handler.
        """
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            auth = iam_clients.GenesisCoreAuth(
                username=self.USERNAME,
                password=self.PASSWORD,
                client_uuid=default_client_uuid,
                client_id=default_client_id,
                client_secret=default_client_secret,
            )
            url = auth.get_token_url(f"{user_api.get_endpoint()}v1/")
            code = pyotp.TOTP(user.otp_secret).now()
            http = bazooka.Client()

            granted = http.post(
                url,
                auth.get_password_auth_params(),
                headers={iam_c.HEADER_OTP_CODE: code},
            )
            assert "access_token" in granted.json()

            with pytest.raises(bazooka_exc.ClientError):
                http.post(
                    url,
                    auth.get_password_auth_params(),
                    headers={iam_c.HEADER_OTP_CODE: code},
                )

    def test_the_burn_survives_a_rolled_back_caller(
        self, user_api, user_api_client, auth_user_admin
    ):
        """A spent code stays spent even if the caller's work is undone.

        Any request can roll back on a later error, and a read-only context
        rolls back by design. If the burn rode along inside the caller's
        transaction it would be undone with it, handing the same code back
        for the rest of its window.
        """
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            code = pyotp.TOTP(user.otp_secret).now()

            # A session in thread storage is what a request looks like from
            # down here, and this one ends the way a failing request does.
            with contextlib.suppress(RuntimeError):
                with ra_contexts.Context().session_manager():
                    assert user.validate_otp(code) is True
                    raise RuntimeError("the request fails after the check")

            assert user.last_otp_counter() is not None
            assert user.validate_otp(code) is False

    def test_counter_is_not_exposed_over_the_api(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (client, user):
            fetched = client.get_user(str(user.uuid))

            assert "last_otp_counter" not in fetched
