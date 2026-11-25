#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import datetime
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import pathlib
import ssl
import threading
import types
from unittest import mock
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from gcl_sdk.events import opts
from gcl_sdk.events.services import senders
import jinja2
from oslo_config import cfg
import pytest
import requests as http_requests
import yaml

from exordos_core.common import config
from exordos_core.elements.dm import models as element_models
from exordos_core.events import payloads


@pytest.mark.parametrize(
    "payload_class,code_field",
    [
        (payloads.RegistrationEventPayload, "confirmation_code"),
        (payloads.ResetPasswordEventPayload, "reset_code"),
        (payloads.EmailChangeEventPayload, "code"),
    ],
)
def test_sender_loads_managed_token_for_submission_and_polling(
    tmp_path, payload_class, code_field, monkeypatch
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
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "ca.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            endpoint = f"https://localhost:{server.server_port}"
            # A valid certificate still fails unless its issuer is trusted.
            with pytest.raises(http_requests.exceptions.SSLError):
                http_requests.get(endpoint, timeout=2, verify=True)
            monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert_path))
            settings = configs["core_events_conf"]["body"]["content"]
            element = element_models.Element(
                name="core_notification_email",
                version="3.1.2-dev",
                link="$core_notification_email",
            )
            resource = element_models.Resource(
                name="config",
                element=element,
                resource_link_prefix="$core.config.configs",
                value={},
            )
            engine = mock.Mock()
            engine.get_resource_by_link.return_value.get_parameter_value.return_value = f"localhost:{server.server_port}"
            settings = resource._render_value(settings, engine)
            settings = settings.replace("/etc/exordos_core/", f"{tmp_path}/")
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
                if payload_class is payloads.EmailChangeEventPayload:
                    payload = payload_class(
                        recipient="new@example.com", code="single-use-code"
                    )
                else:
                    payload = payload_class(
                        site_endpoint="https://example.com",
                        **{code_field: uuid.uuid4()},
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
