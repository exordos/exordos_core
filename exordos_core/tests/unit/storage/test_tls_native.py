#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

"""Optional end-to-end check against the packaged RawStor daemons."""

import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import time
from types import SimpleNamespace
import uuid

from cryptography import x509
from gcl_sdk.agents.universal.drivers import rawstor_cluster
from gcl_sdk.agents.universal.drivers import rawstor_tls
import pytest

from exordos_core.common import rawstor_pki
from exordos_core.common import spiffe


@pytest.mark.skipif(
    not os.environ.get("RAWSTOR_TLS_TEST_BIN"),
    reason="Requires RawStor 0.3.6 native daemons",
)
def test_native_mds_ost_tls_and_leaf_reload(tmp_path):
    binaries = Path(os.environ["RAWSTOR_TLS_TEST_BIN"])
    ca = spiffe.create_authority(rawstor_pki.TRUST_DOMAIN)
    processes = []
    streams = []

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def credentials(identity):
        issued = spiffe.issue_certificate(
            ca.key, ca.cert, "spiffe://rawstor.local/" + identity, 7
        )
        return {
            "cert": issued.cert,
            "key": issued.key.decode(),
            "ca": ca.cert,
        }

    def start(name, args, creds):
        context = rawstor_tls.temporary_bindings(creds)
        client = context.__enter__()
        streams.append(context)
        env = dict(
            os.environ,
            **rawstor_tls.environment(client.directory),
            RAWSTOR_OPTS_IO_ATTEMPTS="1",
            RAWSTOR_MDS_OPTS_INFO_INTERVAL="100",
        )
        logfile = (tmp_path / (name + ".log")).open("w")
        streams.append(logfile)
        p = subprocess.Popen(
            [str(binaries / name), *args],
            env=env,
            stdout=logfile,
            stderr=subprocess.STDOUT,
        )
        processes.append(p)
        return p, client

    ost, mds = uuid.uuid4(), uuid.uuid4()
    ost_port, mds_port = port(), port()
    backing = tmp_path / "data"
    backing.mkdir()
    topology = tmp_path / "topology"
    topology.write_text(
        f"{ost} ost://127.0.0.1:{ost_port} 100 data,witness host1\n{mds} ost://127.0.0.1:{mds_port} 100 witness host2\n"
    )
    try:
        start(
            "rawstor-ost",
            ["-b", f"127.0.0.1:{ost_port}", "--workers", "1", f"file://{backing}"],
            credentials("ost/" + str(ost)),
        )
        server, server_files = start(
            "rawstor-mds",
            [
                "-b",
                f"127.0.0.1:{mds_port}",
                "-d",
                str(tmp_path / "mds.db"),
                "-t",
                str(topology),
                "--workers",
                "1",
            ],
            credentials("mds/" + str(mds)),
        )
        with rawstor_tls.temporary_bindings(
            credentials("admin/" + str(uuid.uuid4()))
        ) as client:
            location = client.Location(f"mds://127.0.0.1:{mds_port}/")
            deadline = time.monotonic() + 15
            while True:
                try:
                    location.info()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            account = uuid.uuid4()
            job = rawstor_cluster.MetaStorageVolume(
                uuid=uuid.uuid4(),
                cluster=uuid.uuid4(),
                project_id=account,
                storage_pool=str(uuid.uuid4()),
                storage_location=f"mds://127.0.0.1:{mds_port}/",
                storage_policy={"mirrors": 1, "failure_domain": "ost"},
                size=1,
            )
            cluster = SimpleNamespace(
                load_driver=lambda: SimpleNamespace(bindings=client)
            )
            job.dump_to_dp(cluster)
            job.dump_to_dp(cluster)
            target = client.Target(f"mds://{account}@127.0.0.1:{mds_port}/{job.uuid}")
            assert target.spec().size == 1 << 30
            other = client.Target(
                f"mds://{account}@127.0.0.1:{mds_port}/{uuid.uuid4()}"
            )
            other.create(
                size=1 << 30, width=1, chunk_size=1 << 30, failure_domain="ost"
            )
            assert sum(item.spec().size for item in location.objects()) == 2 << 30
            volume_id = uuid.UUID(target.uri.rsplit("/", 1)[1])
            identity = f"spiffe://rawstor.local/account/{account}/client/{volume_id}"
            issued = spiffe.issue_certificate(
                ca.key,
                ca.cert,
                identity,
                7,
                extensions=rawstor_pki.permissions(identity, volume_id),
            )
            leaf = {
                "cert": issued.cert,
                "key": issued.key.decode(),
                "ca": ca.cert,
            }
            with rawstor_tls.temporary_bindings(leaf) as restricted:
                assert restricted.Target(target.uri).spec().size == 1 << 30
                with pytest.raises(PermissionError):
                    restricted.Target(other.uri).remove()
            job.size = 2
            job.update_on_dp(cluster)
            assert target.spec().size == 2 << 30
            replacement = credentials("mds/" + str(mds))
            current = Path(server_files.directory) / "current"
            old = current.with_name("old")
            current.rename(old)
            current.symlink_to(old.name)
            generation = current.with_name("renewed")
            generation.mkdir(mode=0o700)
            for name, field in [
                ("cert.crt", "cert"),
                ("cert.key", "key"),
                ("ca.crt", "ca"),
            ]:
                (generation / name).write_text(replacement[field])
            staged = current.with_name("next")
            staged.symlink_to(generation.name)
            staged.replace(current)
            server.send_signal(signal.SIGUSR1)
            context = ssl.create_default_context(
                cafile=str(Path(client.directory) / "current/ca.crt")
            )
            context.check_hostname = False
            context.load_cert_chain(
                str(Path(client.directory) / "current/cert.crt"),
                str(Path(client.directory) / "current/cert.key"),
            )
            expected = x509.load_pem_x509_certificate(
                replacement["cert"].encode()
            ).serial_number
            deadline = time.monotonic() + 5
            while True:
                with socket.create_connection(("127.0.0.1", mds_port)) as sock:
                    with context.wrap_socket(sock, server_hostname="unused") as secured:
                        actual = x509.load_der_x509_certificate(
                            secured.getpeercert(binary_form=True)
                        ).serial_number
                if actual == expected:
                    break
                assert time.monotonic() < deadline, (
                    "MDS did not reload its renewed leaf"
                )
                time.sleep(0.05)
            assert target.spec().size == 2 << 30
            job.delete_from_dp(cluster)
            with pytest.raises(FileNotFoundError):
                target.spec()
            other.remove()
    finally:
        for p in reversed(processes):
            p.terminate()
        for p in processes:
            p.wait(timeout=10)
        for stream in reversed(streams):
            if hasattr(stream, "__exit__"):
                stream.__exit__(None, None, None)
            else:
                stream.close()
