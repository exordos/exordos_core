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

"""What an upload says when the version is already in the repository.

Two questions live here and must not be confused: whether the driver can
upload at all, which is the driver's to answer, and whether *this* version is
already there, which is the repository's -- it owns the elements. Answering
the second one as if it were the first is how a duplicate upload came back as
"Upload is not supported by this repository driver", which is untrue twice
over and left the caller nothing to act on.
"""

from unittest import mock
import uuid as sys_uuid

import pytest
from restalchemy.storage import exceptions as ra_storage_exc

from exordos_core.repo.dm import models
from exordos_core.repo.drivers import database as database_driver


@pytest.fixture
def repository():
    """A stand-in, not `Repository.__new__(...)`.

    A restalchemy model built without its constructor has no properties of
    its own, so each assignment lands in the class's shared declaration and
    reads come back as whatever was written there last -- which this test
    met before it was written this way.
    """
    repo = mock.MagicMock(uuid=sys_uuid.uuid4(), project_id=sys_uuid.uuid4())
    repo.name = "local"
    return repo


def _upload(repo, existing):
    """Call the upload path with the elements the repository already holds."""
    with (
        mock.patch.object(models.RepoElement, "objects") as objects,
        mock.patch.object(models.RepoElement, "__init__", return_value=None),
        mock.patch.object(models.RepoElement, "insert"),
    ):
        objects.get_all.return_value = existing
        return models.Repository.upload(
            repo,
            element_name="dbaas",
            element_version="2.4.0",
            manifest={},
        )


def test_a_version_already_there_is_a_conflict(repository):
    with pytest.raises(ra_storage_exc.ConflictRecords) as raised:
        _upload(repository, existing=[mock.Mock()])

    # It says which element and which repository, because the caller's next
    # move (`ee update`, another version, another repository) depends on it.
    assert "dbaas" in str(raised.value)
    assert "2.4.0" in str(raised.value)
    assert "local" in str(raised.value)


def test_the_driver_answers_only_what_it_knows():
    """This driver stores what it is given, so it can always upload.

    It used to answer "no" for a version already present, and its caller
    turned that into a complaint about the driver -- so the one question it
    cannot answer is now not asked of it.
    """
    driver = database_driver.DatabaseProxyRepoDriver.__new__(
        database_driver.DatabaseProxyRepoDriver
    )

    assert driver.can_upload_element() is True
