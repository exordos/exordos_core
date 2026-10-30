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
from unittest import mock
import uuid

from gcl_iam import exceptions as iam_e
import pyotp
import pytest
from restalchemy.common import exceptions as ra_exceptions

from exordos_core.user_api.iam.dm import models


class FakeCursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakeSession:
    """Stands in for the counter row the burn statement competes over.

    `stored` is None while the user has no row yet, which is where every
    user starts.
    """

    def __init__(self, stored=None):
        self.stored = stored
        self.statements = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement, values):
        self.statements.append((statement, values))
        _uuid, counter = values
        if self.stored is None or self.stored < counter:
            self.stored = counter
            return FakeCursor(1)
        return FakeCursor(0)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, session):
        self._session = session

    def get_session(self):
        return self._session


def current_timecode(secret):
    totp = pyotp.TOTP(secret)
    return totp.timecode(datetime.datetime.now(datetime.timezone.utc))


def make_user(secret, otp_enabled):
    return models.User.restore(
        uuid=uuid.uuid4(),
        name="otpuser",
        description="",
        email="otpuser@example.com",
        secret_hash="x" * 128,
        salt="s" * 24,
        otp_secret=secret,
        otp_enabled=otp_enabled,
    )


@pytest.fixture
def secret():
    return pyotp.random_base32()


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def engine(session):
    with mock.patch.object(
        models.User, "_get_engine", return_value=FakeEngine(session)
    ):
        yield


@pytest.fixture
def user(secret, engine):
    return make_user(secret, otp_enabled=True)


class TestValidateOtp:
    def test_accepts_a_fresh_code(self, user, secret):
        assert user.validate_otp(pyotp.TOTP(secret).now()) is True

    def test_rejects_the_same_code_twice(self, user, secret):
        code = pyotp.TOTP(secret).now()

        assert user.validate_otp(code) is True
        assert user.validate_otp(code) is False

    def test_records_the_consumed_time_step(self, user, secret, session):
        before = current_timecode(secret)

        user.validate_otp(pyotp.TOTP(secret).now())

        assert session.stored in (before, current_timecode(secret))

    def test_rejects_a_code_from_an_older_window(self, user, secret):
        stale = pyotp.TOTP(secret).at(
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=60)
        )

        assert user.validate_otp(stale) is False

    def test_rejects_an_empty_code_without_touching_the_counter(self, user, session):
        assert user.validate_otp(None) is False
        assert user.validate_otp("") is False
        assert session.statements == []

    def test_raises_when_otp_is_not_enabled(self, user, secret):
        user.otp_enabled = False

        with pytest.raises(iam_e.OTPNotEnabledError):
            user.validate_otp(pyotp.TOTP(secret).now())

    def test_loses_the_race_when_another_request_burned_first(
        self, user, secret, session
    ):
        code = pyotp.TOTP(secret).now()
        # A concurrent request carrying the same code got there first.
        session.stored = current_timecode(secret)

        assert user.validate_otp(code) is False

    def test_burn_is_a_single_guarded_statement(self, user, secret, session):
        # The whole guarantee lives in this WHERE clause: without it two
        # concurrent requests both read the old counter and both pass.
        user.validate_otp(pyotp.TOTP(secret).now())

        statement, values = session.statements[0]
        assert "ON CONFLICT (uuid) DO UPDATE" in statement
        assert "last_counter < EXCLUDED.last_counter" in statement
        assert values == (user.uuid, session.stored)

    def test_writes_only_to_the_counter_table(self, user, secret, session):
        # A login must not touch the user row, or it would keep bumping
        # iam_users.updated_at.
        user.validate_otp(pyotp.TOTP(secret).now())

        statement, _values = session.statements[0]
        assert "iam_user_otp_counters" in statement
        assert "iam_users" not in statement

    def test_burn_commits_on_its_own(self, user, secret, session):
        # A spent code has to stay spent even if the request that spent it
        # rolls back, so the burn must not ride on the caller's transaction.
        user.validate_otp(pyotp.TOTP(secret).now())

        assert session.committed is True
        assert session.closed is True

    def test_a_failing_burn_rolls_back_and_closes(self, user, secret, session):
        def boom(statement, values):
            raise RuntimeError("database is on fire")

        session.execute = boom

        with pytest.raises(RuntimeError):
            user.validate_otp(pyotp.TOTP(secret).now())

        assert session.rolled_back is True
        assert session.closed is True


class TestActivateOtp:
    def test_burns_the_activation_code(self, secret, engine, session):
        user = make_user(secret, otp_enabled=False)
        before = current_timecode(secret)

        with mock.patch.object(models.User, "save"):
            user.activate_otp(pyotp.TOTP(secret).now())

        assert user.otp_enabled is True
        assert session.stored in (before, current_timecode(secret))

    def test_rejects_a_wrong_code(self, secret, engine, session):
        user = make_user(secret, otp_enabled=False)

        with mock.patch.object(models.User, "save"):
            with pytest.raises(iam_e.OTPInvalidCodeError):
                user.activate_otp("000000")

        assert session.stored is None


class TestUserOtpCounter:
    def test_refuses_to_be_built_without_a_user_uuid(self):
        # The uuid is a user's; a generated one would name nobody.
        with pytest.raises(ra_exceptions.PropertyRequired):
            models.UserOtpCounter(last_counter=1)

    def test_keeps_the_uuid_it_is_given(self):
        user_uuid = uuid.uuid4()

        counter = models.UserOtpCounter(uuid=user_uuid, last_counter=1)

        assert counter.uuid == user_uuid
