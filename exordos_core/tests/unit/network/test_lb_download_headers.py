#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

import pytest

from exordos_core.user_api.network.dm import models


def test_download_headers_round_trip():
    action = models.RuleStaticDownloadKind(
        url="https://example.com/site.tar.gz",
        headers={"Authorization": "Bearer reader"},
    )
    raw = action.dump_to_simple_view()
    restored = models.RuleStaticDownloadKind.restore_from_simple_view(**raw)
    assert restored.headers == {"Authorization": "Bearer reader"}


def test_existing_download_has_no_headers():
    action = models.RuleStaticDownloadKind(url="https://example.com/site.tar.zst")
    assert action.headers == {}


@pytest.mark.parametrize(
    "value",
    [
        {"Bad\nName": "value"},
        {"Authorization": "token\r\nX-Injected: yes"},
        {"Authorization": "token\x00"},
        {"Authorization": None},
        {"Authorization": 1},
        {"Authorization": "x" * 8193},
        {1: "value"},
        [],
    ],
)
def test_download_headers_reject_invalid_values(value):
    assert not models.DownloadHeaders().validate(value)
