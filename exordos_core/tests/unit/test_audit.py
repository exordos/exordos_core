#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from contextlib import ExitStack
from importlib import import_module
from unittest import mock
import uuid

from oslo_config import cfg
import pytest
from restalchemy.storage.sql import orm

from exordos_core.common import audit
from exordos_core.compute.dm import models as compute
from exordos_core.secret.dm import models as secrets
from exordos_core.user_api.iam.dm import models as iam


@pytest.mark.parametrize(
    "module",
    [
        "compute",
        "config",
        "secret",
        "vs",
        "quota",
        "repo",
        "elements",
        "user_api.iam",
        "user_api.dns",
        "user_api.network",
        "user_api.security",
    ],
)
def test_resource_audit_identity(module):
    module = import_module(f"exordos_core.{module}.dm.models")
    for model in vars(module).values():
        if (
            isinstance(model, type)
            and issubclass(model, audit.AuditLogSQLStorableMixin)
            and model.__module__ == module.__name__
            and getattr(model, "__tablename__", None)
        ):
            assert model.get_audit_service_name() == "core"
            assert model.get_audit_resource_type() == model.__tablename__


@pytest.mark.parametrize("action", ["insert", "update", "delete"])
def test_audit_event_shares_resource_transaction(action):
    resource = compute.PlacementDomain(name="test")
    session = mock.Mock()
    engine = mock.MagicMock()
    engine.session_manager.return_value.__enter__.return_value = session
    with (
        mock.patch.object(resource, "_get_engine", return_value=engine),
        mock.patch.object(resource, "is_dirty", return_value=True),
        mock.patch.object(resource, "_write_audit_event") as write,
        mock.patch.object(orm.SQLStorableMixin, action) as mutation,
    ):
        getattr(resource, action)()
    assert mutation.call_args.kwargs["session"] is session
    assert write.call_args.kwargs["session"] is session
    assert (
        write.call_args.kwargs["action"]
        == {
            "insert": "create",
            "update": "update",
            "delete": "delete",
        }[action]
    )
    assert (write.call_args.kwargs["snapshot"] is None) == (action == "delete")


def test_secret_snapshots_contain_metadata_only():
    secret = secrets.Secret(
        name="test",
        project_id=uuid.uuid4(),
        value="canary",
        default_value="default-canary",
    )
    snapshot = secret.dump_to_audit_snapshot()
    assert snapshot["name"] == "test"
    assert snapshot["project_id"] == str(secret.project_id)
    assert {"value", "default_value"}.isdisjoint(snapshot)


def test_iam_snapshot_does_not_render_computed_credentials(monkeypatch):
    monkeypatch.setattr(iam.ModelWithSecret, "_global_salt", "test-salt")
    client = iam.IamClient(name="test", client_id="test-client", secret="canary")
    snapshot = client.dump_to_audit_snapshot()
    assert snapshot["client_id"] == "test-client"
    assert {"secret", "secret_hash", "salt"}.isdisjoint(snapshot)


def test_nested_credentials_and_opaque_payloads_are_removed():
    value = {
        "name": "keep",
        "storage": [{"password": "canary", "bucket": "keep"}],
        "private_key": "canary",
        "body": "arbitrary secret",
        "value": "secret",
    }
    assert audit._without_secrets(value) == {
        "name": "keep",
        "storage": [{"bucket": "keep"}],
    }


@pytest.mark.parametrize(
    "workers, enabled", [(1, False), (1, True), (2, False), (2, True)]
)
def test_api_sender_lifecycle(workers, enabled):
    with mock.patch.object(cfg, "CONF", cfg.ConfigOpts()):
        from exordos_core.cmd import user_api
    conf = mock.MagicMock()
    conf.__getitem__.return_value.workers = workers
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(user_api, "CONF", conf))
        for obj, name in [
            (user_api.config, "parse"),
            (user_api.infra_log, "configure"),
            (user_api.sdk_migrations, "apply_migrations"),
            (user_api.engines.engine_factory, "configure_postgresql_factory"),
            (user_api.sdk_clients, "build_client"),
            (user_api.utils, "get_context_storage"),
            (user_api.app, "build_wsgi_application"),
            (user_api.iam_drivers, "DirectDriver"),
        ]:
            stack.enter_context(mock.patch.object(obj, name))
        api = stack.enter_context(
            mock.patch.object(user_api.bjoern_service, "BjoernService")
        )
        hub = stack.enter_context(mock.patch.object(user_api.hub, "ProcessHubService"))
        stack.enter_context(
            mock.patch.object(
                user_api.audit_opts,
                "get_audit_delivery_config",
                return_value=mock.Mock(enabled=enabled),
            )
        )
        build = stack.enter_context(
            mock.patch.object(
                user_api.audit_senders.AuditSenderService, "build_from_config"
            )
        )
        user_api.main()
        if enabled:
            build.assert_called_once_with(conf=conf, iter_min_period=3)
            hub.return_value.add_service.assert_any_call(build.return_value)
            build.return_value.add_setup.call_args.args[0]()
        else:
            build.assert_not_called()
        if workers > 1 or enabled:
            hub.return_value.start.assert_called_once()
            api.return_value.start.assert_not_called()
        else:
            api.return_value.start.assert_called_once()
            hub.return_value.start.assert_not_called()


def test_json_model_preserves_storage_serialization():
    resource = secrets.Certificate(
        name="test",
        project_id=uuid.uuid4(),
        domains=["example.com"],
        key="private-canary",
    )
    snapshot = resource.dump_to_audit_snapshot()
    assert snapshot["domains"] == ["example.com"]
    assert "key" not in snapshot
    assert resource._get_prepared_data()["domains"] == '["example.com"]'


def test_clean_update_does_not_record_event():
    resource = compute.PlacementDomain(name="test")
    with (
        mock.patch.object(resource, "is_dirty", return_value=False),
        mock.patch.object(resource, "_write_audit_event") as write,
        mock.patch.object(orm.SQLStorableMixin, "update") as update,
    ):
        resource.update()
    write.assert_not_called()
    update.assert_not_called()


def test_optional_manifest_config(tmp_path):
    from pathlib import Path

    from gcl_sdk.audit import opts
    import yaml

    root = Path(__file__).parents[3]
    manifest = yaml.safe_load(
        (root / "exordos/manifests/core_audit.yaml.j2").read_text()
    )
    assert set(manifest["requirements"]) == {"core", "audit"}
    core = yaml.safe_load((root / "exordos/manifests/core.yaml.j2").read_text())
    exported = {item["link"] for item in core["exports"].values()}
    for item in manifest["imports"].values():
        if item["element"] == "$core":
            assert item["link"] in exported
    assert "audit" not in core.get("requirements", {})
    resources = manifest["resources"]
    token = resources["$core.iam.tokens"]["core_audit_token"]
    assert token["auto_renew"] is True
    config = resources["$core.config.configs"]["core_audit_config"]
    assert config["mode"] == "0600"
    assert config["target"]["node_set"] == "70c88222-b4d9-46c3-9340-aa5bfaaa4b94"
    text = config["body"]["content"].strip().removeprefix('f"').removesuffix('"')
    text = text.replace(
        "{{ audit_endpoint | default('http://audit.local.genesis-core.tech:8080/') }}",
        "http://audit.example/",
    ).replace("{$core.iam.tokens.$core_audit_token:access_token}", "test-token")
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "audit.conf").write_text(text)
    conf = cfg.ConfigOpts()
    opts.register_audit_delivery_opts(conf)
    assert conf.audit_delivery.enabled is False
    conf(["--config-dir", str(directory)])
    assert conf.audit_delivery.enabled is True
    assert conf.audit_delivery.auth_token == "test-token"
    assert conf.audit_delivery.endpoint == "http://audit.example/"
