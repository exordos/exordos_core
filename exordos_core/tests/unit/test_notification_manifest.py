# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").
from html.parser import HTMLParser
import pathlib
import subprocess
import types
from urllib.parse import parse_qs
from urllib.parse import urlparse
import uuid

import jinja2
import pytest
import yaml

from exordos_core.elements.dm import models
from exordos_core.elements.dm import utils

ROOT = pathlib.Path(__file__).parents[3] / "exordos/manifests"


@pytest.mark.parametrize("node_set_uuid", [uuid.uuid4(), uuid.uuid4()])
def test_email_configs_follow_exported_core_node_set(node_set_uuid):
    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
    core = yaml.safe_load(
        environment.get_template("core.yaml.j2").render(version="3.1.2-dev")
    )
    email = yaml.safe_load(
        environment.get_template("core_notification_email.yaml.j2").render(
            version="3.1.2-dev"
        )
    )
    assert email["requirements"]["core"]["from_version"] == core["version"]
    engine = models.ElementEngine()
    core_element = models.Element(name="core", version=core["version"], link="$core")
    email_element = models.Element(
        name=email["name"], version=email["version"], link="$core_notification_email"
    )
    for element in (core_element, email_element):
        engine.add_element(element)
    node_set = models.Resource(
        name="core_set",
        element=core_element,
        resource_link_prefix="$core.compute.sets",
        value={"uuid": str(node_set_uuid)},
    )
    engine.add_resource(node_set)
    engine.add_resource_by_export(
        models.Export(
            name="core_set", element=core_element, **core["exports"]["core_set"]
        )
    )
    imported = email["imports"]["core_set"]
    exported = engine.get_resource_by_export_link(
        manifest=models.Manifest(name=email["name"], version=email["version"]),
        from_element=engine.get_element(imported["element"]),
        link=f"{imported['element']}.{imported['link']}",
    )
    engine.add_resource(models.ImportedResource(email_element, exported, "core_set"))
    for name, config in email["resources"]["$core.config.configs"].items():
        resource = models.Resource(
            name=name,
            element=email_element,
            resource_link_prefix="$core.config.configs",
            value=config["target"],
        )
        assert resource.render_target_state(engine=engine)["node_set"] == str(
            node_set_uuid
        )


@pytest.mark.parametrize(
    "name,path,code_field",
    [
        ("email_confirmation_template", "/confirm-email", "confirmation_code"),
        ("reset_password_template", "/reset-password", "reset_code"),
    ],
)
def test_email_template_links_keep_user_and_confirmation_code(name, path, code_field):
    manifest = yaml.safe_load(
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template("core_notification_email.yaml.j2")
        .render(version="3.1.2-dev")
    )
    template = manifest["resources"]["$exordos_notification.types.email.templates"][
        name
    ]
    html = (
        jinja2.Environment(undefined=jinja2.StrictUndefined)
        .from_string(template["content"]["bodies"][0])
        .render(
            user=types.SimpleNamespace(uuid="recipient-uuid", username="Recipient"),
            site_endpoint="https://example.com",
            **{code_field: "confirmation-value"},
        )
    )
    links = []

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                links.append(dict(attrs)["href"])

    Links().feed(html)
    link = urlparse(links[0])
    assert link.scheme == "https" and link.netloc == "example.com"
    assert link.path == path
    assert parse_qs(link.query) == {
        "user_id": ["recipient-uuid"],
        "code": ["confirmation-value"],
    }


@pytest.mark.parametrize("trim_blocks", [False, True])
def test_core_email_depends_on_transport_and_supplies_its_own_template(trim_blocks):
    path = ROOT / "core_notification_email.yaml.j2"
    assert yaml.safe_load(path.read_text())["name"] == "core_notification_email"
    manifest = yaml.safe_load(
        jinja2.Environment(trim_blocks=trim_blocks, undefined=jinja2.StrictUndefined)
        .from_string(path.read_text())
        .render(version="2.0.1-dev+contract")
    )
    assert set(manifest["requirements"]) == {"core", "notification_email"}
    resources = manifest["resources"]
    assert "$mailaas.types.mail.instances" not in resources
    assert "$exordos_notification.types.email.providers" not in resources
    event_type = resources["$exordos_notification.types.email.event_types"][
        "email_confirmation"
    ]
    assert event_type["uuid"] == "e0fe8070-e59c-4347-8df1-b3f638c06565"
    template = resources["$exordos_notification.types.email.templates"][
        "email_confirmation_template"
    ]
    assert (
        template["provider"] == "$core_notification_email.imports.$email_provider:uuid"
    )
    assert template["event_type"] == (
        "$exordos_notification.types.email.event_types.$email_confirmation:uuid"
    )
    body = template["content"]["bodies"][0]
    assert "{{ confirmation_code }}" in body
    assert "{{ site_endpoint }}/confirm-email?user_id={{ user.uuid }}" in body
    assert manifest["imports"]["email_provider"]["element"] == "$notification_email"
    build = yaml.safe_load((ROOT.parent / "exordos.yaml").read_text())
    assert {"manifest": "manifests/core_notification_email.yaml.j2"} in build["build"][
        "elements"
    ]
    schema = yaml.safe_load((ROOT / "specification/base_spec.yaml").read_text())
    utils.validate_manifest(manifest, schema)


def test_email_sender_uses_platform_managed_token_and_minimal_permissions():
    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
    manifest = yaml.safe_load(
        environment.get_template("core_notification_email.yaml.j2").render(
            version="1.2.8-dev"
        )
    )
    resources = manifest["resources"]
    config = resources["$core.config.configs"]["core_events_conf"]
    assert config["mode"] == "0600"
    settings = config["body"]["content"]
    token_config = resources["$core.config.configs"]["core_events_token"]
    assert token_config["mode"] == "0600"
    assert (
        "exordos_api_token = {$core.iam.tokens.$core_event_sender:access_token}"
        in token_config["body"]["content"]
    )
    assert "exordos_api_token" not in settings
    assert "client_type = async" in settings
    assert "systemctl restart ec-gservice" in config["on_change"]["command"]
    assert resources["$core.iam.users"]["core_event_sender"]["type"] == "service"
    token = resources["$core.iam.tokens"]["core_event_sender"]
    assert token["auto_renew"] is True
    assert token["expiration_delta"] == 604800
    assert token["scope"] == ""
    assert "core_event_sender" not in resources.get("$core.secret.passwords", {})
    assert {v["name"] for v in resources["$core.iam.permissions"].values()} == {
        "exordos_notification.event_type_e0fe8070-e59c-4347-8df1-b3f638c06565.create",
        "exordos_notification.event_type_e0fe8070-e59c-4347-8df1-b3f638c06565.read",
        "exordos_notification.event_type_b7ac18fa-3935-4f52-848f-e64bbd19abfb.create",
        "exordos_notification.event_type_b7ac18fa-3935-4f52-848f-e64bbd19abfb.read",
    }


@pytest.mark.parametrize("name", ["core_event_type_mapping", "core_events_conf"])
@pytest.mark.parametrize("missing_peer", [False, True])
@pytest.mark.parametrize("restart_code", [0, 7])
def test_initial_config_hook_reports_restart_failures(
    tmp_path, name, missing_peer, restart_code
):
    manifest = yaml.safe_load(
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template("core_notification_email.yaml.j2")
        .render(version="3.1.2-dev")
    )
    configs = manifest["resources"]["$core.config.configs"]
    for config_name in ("core_event_type_mapping", "core_events_conf"):
        if missing_peer and config_name != name:
            continue
        path = tmp_path / configs[config_name]["path"].lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(configs[config_name]["body"]["content"])
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(f"#!/bin/sh\nexit {restart_code}\n")
    systemctl.chmod(0o700)
    command = configs[name]["on_change"]["command"].replace(
        "/etc/exordos_core/", f"{tmp_path}/etc/exordos_core/"
    )
    result = subprocess.run(
        ["/bin/sh", "-ec", command], env={"PATH": str(tmp_path)}, check=False
    )
    assert result.returncode == (0 if missing_peer else restart_code)


@pytest.mark.parametrize(
    "missing", [None, "core_event_type_mapping", "core_events_conf"]
)
@pytest.mark.parametrize("restart_code", [0, 7])
def test_email_token_rotation_restarts_sender_without_interrupting_core_api(
    tmp_path, missing, restart_code
):
    manifest = yaml.safe_load(
        jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT))
        .get_template("core_notification_email.yaml.j2")
        .render(version="1.2.11-dev")
    )
    configs = manifest["resources"]["$core.config.configs"]
    for name, config in configs.items():
        if name == missing:
            continue
        path = tmp_path / config["path"].lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config["body"]["content"])
    calls = tmp_path / "systemctl-calls"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}"\nexit {restart_code}\n'
    )
    systemctl.chmod(0o700)
    rotating = [
        config
        for config in configs.values()
        if "exordos_api_token =" in config["body"]["content"]
    ]
    assert len(rotating) == 1
    command = rotating[0]["on_change"]["command"]
    command = command.replace("/etc/exordos_core/", f"{tmp_path}/etc/exordos_core/")
    result = subprocess.run(
        ["/bin/sh", "-ec", command],
        env={"PATH": str(tmp_path)},
        check=False,
    )
    actual = calls.read_text().splitlines() if calls.exists() else []
    assert actual == ([] if missing else ["restart ec-gservice"])
    assert result.returncode == (0 if missing else restart_code)
