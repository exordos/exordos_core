#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import grp
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
import pathlib
import pwd
import threading
import types
from unittest import mock
import uuid

from gcl_sdk.agents.universal import constants as ua_constants
from gcl_sdk.agents.universal.drivers import render as ua_render
from gcl_sdk.common import utils as sdk_utils
from gcl_sdk.events import opts
from gcl_sdk.events.services import senders
import jinja2
from oslo_config import cfg
import pytest
import yaml

from exordos_core.cmd import bootstrap
from exordos_core.common import config
from exordos_core.config.dm import models as config_models
from exordos_core.events import payloads


@pytest.mark.parametrize(
    "payload_class,code_field",
    [
        (payloads.RegistrationEventPayload, "confirmation_code"),
        (payloads.ResetPasswordEventPayload, "reset_code"),
    ],
)
def test_upgraded_sender_uses_rendered_token_for_submission_and_polling(
    tmp_path, monkeypatch, payload_class, code_field
):
    requests = []
    source = pathlib.Path(__file__).parents[4] / "exordos/manifests"
    manifest = yaml.safe_load(
        jinja2.Environment(loader=jinja2.FileSystemLoader(source))
        .get_template("core_notification_email.yaml.j2")
        .render(version="3.1.2-dev")
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(("POST", self.path, self.headers["Authorization"], body))
            self._reply({})

        def do_GET(self):
            requests.append(("GET", self.path, self.headers["Authorization"], None))
            self._reply({"status": "ACTIVE"})

        def _reply(self, body):
            content = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    base = tmp_path / "exordos_core.conf"
    base.write_text("[events]\nenabled = false\n")
    dropins = tmp_path / "exordos_core.d"
    dropins.mkdir()
    mapping = tmp_path / "event_type_mapping.yaml"
    configs = manifest["resources"]["$core.config.configs"]
    mapping.write_text(configs["core_event_type_mapping"]["body"]["content"])
    token_path = dropins / "91-notification-token.conf"
    first_token = "synthetic-first-token"
    replacement_token = "synthetic-replacement-token"
    token_render = config_models.Render(
        path=str(token_path),
        mode=configs["core_events_token"]["mode"],
        owner=pwd.getpwuid(os.getuid()).pw_name,
        group=grp.getgrgid(os.getgid()).gr_name,
        content=f"[events]\nexordos_api_token = {first_token}\n",
    )
    monkeypatch.setattr(
        ua_render.RenderAgentDriver, "RENDER_META_PATH", str(tmp_path / "render.json")
    )
    ua_render.RenderAgentDriver().create(token_render.to_ua_resource("render"))

    ua_config = tmp_path / "universal_agent.conf"
    persisted_config = tmp_path / "persist" / ua_config.name
    ua_config.write_text("[universal_agent]\ncaps_drivers = SSHKeyCapabilityDriver\n")
    monkeypatch.setattr(bootstrap, "UA_CONFIG_PATH", str(ua_config))
    monkeypatch.setattr(bootstrap, "UA_CONFIG_DATA_PATH", str(persisted_config))
    with mock.patch.object(bootstrap.subprocess, "run"):
        bootstrap._ensure_ua_config_current()

    agent_conf = cfg.ConfigOpts()
    agent_conf.register_opt(cfg.ListOpt("caps_drivers"), group="universal_agent")
    agent_conf(["--config-file", str(ua_config)])
    assert "RenderAgentDriver" in agent_conf.universal_agent.caps_drivers
    driver_class = sdk_utils.load_from_entry_point(
        ua_constants.EP_UNIVERSAL_AGENT, "RenderAgentDriver"
    )
    driver = driver_class()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            settings = configs["core_events_conf"]["body"]["content"]
            settings = settings.replace("/etc/exordos_core/", f"{tmp_path}/")
            settings = settings.replace(
                "http://notification-cp.local.genesis-core.tech:8080",
                f"http://127.0.0.1:{server.server_port}",
            )
            (dropins / "90-notification-events.conf").write_text(settings)
            for token in (first_token, replacement_token):
                if token == replacement_token:
                    token_render.content = f"[events]\nexordos_api_token = {token}\n"
                    driver.update(token_render.to_ua_resource("render"))
                    assert token_path.read_text() == token_render.content
                    assert token_path.stat().st_mode & 0o777 == 0o600
                conf = cfg.ConfigOpts()
                opts.register_event_opts(conf)
                config.parse(
                    ["--config-file", str(base), "--config-dir", str(dropins)],
                    conf=conf,
                )
                assert conf.events.event_type_mapping_filepath == str(mapping)
                sender = senders.EventSenderService.build_from_config(conf=conf)
                payload = payload_class(
                    site_endpoint="https://example.com", **{code_field: uuid.uuid4()}
                )
                event = sender._notification_client.build_user_event(
                    context=None,
                    user=types.SimpleNamespace(uuid=uuid.uuid4()),
                    payload=payload,
                )
                with (
                    mock.patch.object(event, "save") as save,
                    mock.patch.object(
                        sender, "_fetch_new_events", return_value=[event]
                    ),
                    mock.patch.object(
                        sender, "_fetch_in_progress_events", return_value=[event]
                    ),
                ):
                    sender._iteration()
                post, get = requests[-2:]
                assert post[:3] == ("POST", "/v1/events/", f"Bearer {token}")
                assert get[:3] == (
                    "GET",
                    f"/v1/events/{event.uuid}",
                    f"Bearer {token}",
                )
                assert post[3]["uuid"] == str(event.uuid)
                assert post[3]["event_params"] == event.event_data.to_simple_dict()
                assert event.status == "ACTIVE"
                assert save.call_count == 2
            assert len(requests) == 4
        finally:
            server.shutdown()
            thread.join()
