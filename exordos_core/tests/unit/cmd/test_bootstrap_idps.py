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

from unittest import mock
import uuid

import pytest

from exordos_core.bootstrap import defaults
from exordos_core.user_api.iam.dm import models


@pytest.fixture
def idp_entry():
    return {
        "uuid": str(uuid.uuid4()),
        "name": "Test IdP",
        "iam_client": {
            "uuid": str(uuid.uuid4()),
            "name": "Test client",
            "client_id": "test-client",
        },
        "callback": {
            "kind": "callback_uri",
            "callback": "https://example.com/callback",
        },
    }


@pytest.mark.parametrize(
    "invalid_fields",
    [{"name": "x" * 256}, {"scope": "x" * 65}, {"nonce_required": "invalid"}, {}],
)
def test_invalid_idp_does_not_insert_client(idp_entry, invalid_fields):
    if invalid_fields:
        idp_entry.update(invalid_fields)
    else:
        del idp_entry["name"]
    with (
        mock.patch.object(
            models.Idp,
            "objects",
            mock.Mock(get_one_or_none=mock.Mock(return_value=None)),
        ),
        mock.patch.object(
            models.IamClient,
            "objects",
            mock.Mock(get_one_or_none=mock.Mock(return_value=None)),
        ),
        mock.patch.object(models.IamClient, "insert") as client_insert,
        mock.patch.object(models.Idp, "insert") as idp_insert,
    ):
        defaults.ensure_idps({"idps": [idp_entry]}, "c2FsdA==")

    client_insert.assert_not_called()
    idp_insert.assert_not_called()


def test_existing_idp_does_not_create_client(idp_entry):
    with (
        mock.patch.object(
            models.Idp,
            "objects",
            mock.Mock(get_one_or_none=mock.Mock(return_value=mock.sentinel.idp)),
        ),
        mock.patch.object(models.IamClient, "objects") as client_manager,
        mock.patch.object(models.IamClient, "insert") as client_insert,
        mock.patch.object(models.Idp, "insert") as idp_insert,
    ):
        defaults.ensure_idps({"idps": [idp_entry]}, "c2FsdA==")

    client_manager.get_one_or_none.assert_not_called()
    client_insert.assert_not_called()
    idp_insert.assert_not_called()


@pytest.mark.parametrize("client_exists", [False, True])
def test_valid_idp_uses_client(idp_entry, client_exists):
    client = (
        models.IamClient.restore(
            uuid=uuid.UUID(idp_entry["iam_client"]["uuid"]),
            name="Test client",
            client_id="test-client",
            salt="s" * 24,
            secret_hash="h" * 128,
        )
        if client_exists
        else None
    )
    with (
        mock.patch.object(
            models.Idp,
            "objects",
            mock.Mock(get_one_or_none=mock.Mock(return_value=None)),
        ),
        mock.patch.object(
            models.IamClient,
            "objects",
            mock.Mock(get_one_or_none=mock.Mock(return_value=client)),
        ),
        mock.patch.object(models.IamClient, "insert", autospec=True) as client_insert,
        mock.patch.object(models.Idp, "insert", autospec=True) as idp_insert,
    ):
        defaults.ensure_idps({"idps": [idp_entry]}, "c2FsdA==")

    idp_insert.assert_called_once()
    idp = idp_insert.call_args.args[0]
    assert idp.name == idp_entry["name"]
    assert idp.iam_client.uuid == uuid.UUID(idp_entry["iam_client"]["uuid"])
    if client_exists:
        client_insert.assert_not_called()
        assert idp.iam_client is client
    else:
        client_insert.assert_called_once_with(idp.iam_client)
