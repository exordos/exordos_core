#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.

from gcl_sdk.agents.universal.dm import models as ua_models

from exordos_core.user_api.security.dm import models


def test_rule_round_trip_through_agent_resource():
    value = {
        "uuid": "00000000-0000-0000-0000-000000000123",
        "name": "Reset password",
        "condition": {
            "kind": "uri_regex",
            "uri_regex": "^/v1/iam/clients/.*/actions/send_reset_password_code/invoke$",
            "method": "POST",
        },
        "action": {
            "kind": "grant_permission",
            "permission": "iam.iam_client.send_reset_password_code",
        },
    }
    resource = ua_models.Resource.from_value(value, "em_core_security_rules")

    rule = models.Rule.from_ua_resource(resource)
    restored = rule.dump_to_simple_view(skip=rule.get_resource_ignore_fields())

    assert restored["condition"]["uri_regex"] == value["condition"]["uri_regex"]
    assert restored["condition"]["method"] == "POST"
    assert restored["action"] == value["action"]
    assert restored["project_id"] is None
