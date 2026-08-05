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

import pyotp

from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam.dm import models as iam_models


class TestOTPReplay(base.BaseIamResourceTest):
    """Replay protection against a real database.

    The unit tests cover the flow; these exist because the guarantee itself
    is a WHERE clause, and only Postgres can confirm it holds.
    """

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
        created = client.create_user(username="otp_replay", password="testtest")

        user = iam_models.User.objects.get_one(filters={"uuid": created["uuid"]})
        user.otp_secret = pyotp.random_base32()
        user.otp_enabled = True
        user.save()

        try:
            yield client, user
        finally:
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

            stored = iam_models.User.objects.get_one(filters={"uuid": user.uuid})
            assert stored.last_otp_counter == user.last_otp_counter

    def test_user_without_a_counter_is_accepted(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (_, user):
            assert user.last_otp_counter is None

            assert user.validate_otp(pyotp.TOTP(user.otp_secret).now()) is True

    def test_counter_is_not_exposed_over_the_api(
        self, user_api, user_api_client, auth_user_admin
    ):
        with self._otp_user(user_api_client, auth_user_admin) as (client, user):
            fetched = client.get_user(str(user.uuid))

            assert "last_otp_counter" not in fetched
