#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import pathlib
import threading
import types
from unittest import mock
import uuid

from gcl_sdk.events import opts
from gcl_sdk.events.services import senders
import jinja2
from oslo_config import cfg
import pytest
import yaml

from exordos_core.common import config
from exordos_core.events import payloads


@pytest.mark.parametrize(
    "payload_class,code_field",
    [
        (payloads.RegistrationEventPayload, "confirmation_code"),
        (payloads.ResetPasswordEventPayload, "reset_code"),
    ],
)
def test_sender_loads_managed_token_for_submission_and_polling(
    tmp_path, payload_class, code_field
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
            # Each new sender process must pick up the replacement managed token.
            for token in ("synthetic-first-token", "synthetic-replacement-token"):
                (dropins / "91-notification-token.conf").write_text(
                    f"[events]\nexordos_api_token = {token}\n"
                )
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
