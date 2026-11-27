#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

"""Opt-in Core functionality that is disabled in release installations."""

from oslo_config import cfg

cfg.CONF.register_opts(
    [
        cfg.BoolOpt(
            "rawstor", default=False, help="Enable experimental RawStor integration"
        )
    ],
    group="features",
)


def rawstor_enabled() -> bool:
    return cfg.CONF.features.rawstor
