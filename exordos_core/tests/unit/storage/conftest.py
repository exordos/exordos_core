#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

import pytest


@pytest.fixture(scope="session", autouse=True)
def initialize_rawstor_transport():
    # Mocking lazy Location/Target attributes loads the native extension, too.
    try:
        import rawstor
    except ModuleNotFoundError as exc:
        if exc.name != "rawstor":
            raise
        return
    rawstor.initialize(insecure=True)


@pytest.fixture(autouse=True)
def rawstor_feature(enable_rawstor):
    pass
