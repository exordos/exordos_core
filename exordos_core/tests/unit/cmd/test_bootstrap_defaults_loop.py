#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

"""The defaults loop: a task still waiting must not hold the others."""

import types
from unittest import mock

import pytest

from exordos_core.cmd import bootstrap

# What `_set_defaults_vs` reads out of the spec it is handed.
SPEC = {
    "profile": "small",
    "stand": {"bootstraps": [{"ports": [{"ip": "10.20.0.2"}]}]},
}


def _defaults(done, **behaviours):
    """Stand-in for `bootstrap_defaults`: every task completes at once
    unless a test gives it a behaviour of its own. Named functions, because
    the loop reports what is still pending by name."""
    stub = types.SimpleNamespace()
    for name in (
        "activate_profile",
        "set_core_ip_var",
        "set_core_root_disk_size_var",
        "set_core_data_disk_size_var",
        "set_ecosystem_endpoint_var",
        "set_disable_telemetry_var",
        "set_realm_uuid_var",
        "set_realm_secret_var",
        "set_realm_access_token_var",
        "set_realm_refresh_token_var",
        "set_hs256_jwks_encryption_key_var",
        "set_iam_default_client_uuid_var",
        "set_iam_default_client_id_var",
        "set_iam_default_client_secret_var",
    ):
        if name in behaviours:
            behaviour = behaviours[name]
        else:

            def behaviour(*args, _n=name):
                done.append(_n)
                return True

        behaviour.__name__ = name
        setattr(stub, name, behaviour)
    return stub


def _fake_clock(monkeypatch, step=0.5):
    """A clock the loop moves itself, so a test never really waits."""
    now = {"t": 0.0}
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(
        bootstrap.time, "sleep", lambda _s: now.update(t=now["t"] + step)
    )
    return now


def test_the_others_run_while_one_is_still_waiting(monkeypatch):
    """`activate_profile` waits for rows an asynchronous service seeds. It
    is first in the list, and taken strictly in order it left the other
    thirteen unrun — then spent the budget on its own account and had the
    whole bootstrap retried from the top."""
    done = []

    def activate_profile(*args):
        done.append("activate_profile")
        return False  # the profile has not been seeded yet

    stub = _defaults(done, activate_profile=activate_profile)
    monkeypatch.setattr(bootstrap, "bootstrap_defaults", stub)
    monkeypatch.setattr(bootstrap, "CONF", {"iam": mock.MagicMock()})
    _fake_clock(monkeypatch, step=61.0)

    with pytest.raises(TimeoutError) as exc:
        bootstrap._set_defaults_vs(SPEC)

    assert "set_core_ip_var" in done, "a task behind the waiting one gets its turn"
    # ...and the refusal names what is actually still pending, not the head
    # of a queue everything else was stuck behind.
    assert "activate_profile" in str(exc.value)
    assert "set_core_ip_var" not in str(exc.value)


def test_progress_anywhere_keeps_the_wait_alive(monkeypatch):
    """The deadline bounds silence, not duration: while something is still
    completing, what is left is still worth waiting for."""
    done = []
    profile_answers = iter([False, True])
    secret_answers = iter([False, False, True])

    def activate_profile(*args):
        return next(profile_answers)

    def set_realm_secret_var(*args):
        return next(secret_answers)

    activate_profile.__name__ = "activate_profile"
    set_realm_secret_var.__name__ = "set_realm_secret_var"
    stub = _defaults(
        done,
        activate_profile=activate_profile,
        set_realm_secret_var=set_realm_secret_var,
    )
    monkeypatch.setattr(bootstrap, "bootstrap_defaults", stub)
    monkeypatch.setattr(bootstrap, "CONF", {"iam": mock.MagicMock()})
    # Every sleep is longer than the whole budget, so nothing but the reset
    # that progress earns can carry the loop to the end.
    _fake_clock(monkeypatch, step=200.0)

    bootstrap._set_defaults_vs(SPEC)

    assert len(done) == 12, "the tasks that were ready went on the first pass"
