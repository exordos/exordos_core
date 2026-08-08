#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

"""Waiting for a repository that is coming, not for one that is not."""

from unittest import mock

import pytest

from exordos_core.cmd import bootstrap


def _repository(inventory):
    return mock.MagicMock(name="repo", **{"driver_spec.inventory_path": inventory})


def test_a_repository_whose_address_answers_is_left_to_the_wait():
    """Reachable is not the same as ready: its inventory still has to be
    read and its elements saved, and that is what the wait is for."""
    repo = _repository("http://repo.example.com/inventory.json")

    with mock.patch.object(bootstrap.urlrequest, "urlopen") as opened:
        bootstrap._refuse_an_unreachable_repository(repo)

    assert opened.call_args.args[0] == "http://repo.example.com/inventory.json"


def test_a_repository_nothing_answers_at_is_refused_at_once():
    """It cost a minute of polling and then an error naming the symptom.

    The case is a child realm handed the address of a repository on its
    parent's internal network, which its own overlay drops by design.
    """
    repo = _repository("http://10.20.0.1:8081/inventory.json")

    with mock.patch.object(
        bootstrap.urlrequest, "urlopen", side_effect=OSError("no route to host")
    ):
        with pytest.raises(RuntimeError) as exc:
            bootstrap._refuse_an_unreachable_repository(repo)

    # The address and the reason, because the next reader has to know which
    # of the two is wrong: what was asked, or where it was asked.
    assert "10.20.0.1:8081" in str(exc.value)
    assert "no route to host" in str(exc.value)


def test_a_repository_with_no_address_is_not_probed():
    """The bootstrap driver reads a directory on this machine; there is
    nothing to reach and nothing to refuse."""
    repo = _repository("")

    with mock.patch.object(bootstrap.urlrequest, "urlopen") as opened:
        bootstrap._refuse_an_unreachable_repository(repo)

    opened.assert_not_called()
