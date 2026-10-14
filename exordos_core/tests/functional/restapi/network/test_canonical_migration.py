# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

from pathlib import Path
import runpy
import uuid

from psycopg.errors import InvalidTextRepresentation
import pytest
from restalchemy.storage.sql import engines

from exordos_core.elements.dm import models as em
from exordos_core.user_api.network.dm import models as catalog


@pytest.mark.parametrize(
    "spelling", ["uppercase", "braces", "hex", "missing", "null", "invalid"]
)
def test_rule_preflight_uses_uuid_semantics(user_api, spelling):
    project = uuid.UUID(int=0)
    group = catalog.SecurityGroup(project_id=project)
    group.insert()
    rule = catalog.SecurityGroupRule(
        project_id=project, security_group=group.uuid, direction="ingress"
    )
    rule.insert()
    element = em.Element(
        name="migration_preflight", version="1.0.0", project_id=project
    )
    element.insert()
    values = {
        "uppercase": str(rule.uuid).upper(),
        "braces": "{" + str(rule.uuid) + "}",
        "hex": rule.uuid.hex,
        "null": None,
        "invalid": "$unresolved:uuid",
    }
    declaration = em.Resource(
        uuid=rule.uuid,
        element=element,
        name="rule",
        resource_link_prefix="$core.network.catalog.security_group_rules",
        value={} if spelling == "missing" else {"uuid": values[spelling]},
    )
    declaration.insert()
    step = runpy.run_path(
        str(Path(__file__).parents[5] / "migrations/0006-sdn-api-contract-15df7a.py")
    )["migration_step"]
    if spelling in ("null", "invalid"):
        # Invalid SQL UUID casts must also abort the transaction.
        exception = ValueError if spelling == "null" else InvalidTextRepresentation
        with pytest.raises(exception):
            with engines.engine_factory.get_engine().session_manager() as session:
                step._canonicalize_config(session)
    else:
        with engines.engine_factory.get_engine().session_manager() as session:
            step._canonicalize_config(session)
            # This historical migration predates removal of the inline field.
            session.execute(
                "ALTER TABLE compute_sets DROP COLUMN network_activation_barrier"
            )
