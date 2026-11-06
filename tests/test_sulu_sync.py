from datetime import datetime, timezone
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase, mock

import wallet_quota_pipeline as pipeline
from walletquota.config import AppSettings
from walletquota.models import Order, ProviderResult


class SuluSyncTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.values = {
            "PGSERVICE": "local-db",
            "PGDATABASE": "local-wallet-db",
            "PGPASSWORD": "local-only-password",
            "PGSERVICEFILE": "/local/services",
            "PGPASSFILE": "/local/passwords",
            "SULU_PGSERVICE": "sulu-reporting",
            "SULU_PGSERVICEFILE": "/sulu/services",
            "SULU_PGPASSFILE": "/sulu/passwords",
        }
        self.calls = []

    def cycle(self, fail=None):
        def psql(command, **options):
            path = Path(command[-1])
            target = path.name.split("_")[0]
            self.calls.append((target, path, options["env"]))
            return subprocess.CompletedProcess(command, int(fail == target))

        stamp = datetime.now(timezone.utc).isoformat()
        result = ProviderResult.success("showpay", (
            Order("showpay", "order-for-local-storage-only", Decimal("20")),
        ), {"order_type": 1, "started_at": stamp, "finished_at": stamp})
        settings = AppSettings.from_values(
            self.values, enabled=["showpay"], output_dir=self.root
        )
        self.calls = []
        with mock.patch.object(pipeline, "run_providers", return_value=[result]), \
                mock.patch.object(pipeline.subprocess, "run", side_effect=psql):
            return pipeline.run_cycle(settings, stdout=io.StringIO(), stderr=io.StringIO())

    def test_summary_and_full_exports_use_separate_connections_and_sql(self):
        with mock.patch.dict(os.environ, {"PGHOSTADDR": "local-only-host",
                             "PGOPTIONS": "local-only-option", "SOME_SECRET": "local-secret"}):
            self.assertEqual(self.cycle(), 0)
        (local, local_path, local_env), (remote, remote_path, remote_env) = self.calls
        self.assertEqual((local, remote), ("postgres", "sulu"))
        self.assertEqual(local_env["PGDATABASE"], "local-wallet-db")
        self.assertEqual(local_env["PGPASSWORD"], "local-only-password")
        self.assertEqual(remote_env["PGSERVICE"], "sulu-reporting")
        self.assertEqual(remote_env["PGSERVICEFILE"], "/sulu/services")
        self.assertEqual(remote_env["PGPASSFILE"], "/sulu/passwords")
        for key in ("PGDATABASE", "PGHOSTADDR", "PGOPTIONS", "PGPASSWORD", "SOME_SECRET"):
            self.assertNotIn(key, remote_env)
        self.assertIn("order-for-local-storage-only", local_path.read_text())
        self.assertNotIn("order-for-local-storage-only", remote_path.read_text())
        markers = list(remote_path.parent.glob("*.imported"))
        self.assertEqual(len(markers), 2)
        self.assertEqual(len({json.loads(p.read_text())["run_id"] for p in markers}), 1)

    def test_sulu_retry_reuses_queued_sql_and_never_repeats_local_import(self):
        self.assertEqual(self.cycle(fail="sulu"), 3)
        first_local = self.calls[0][1]
        first_remote = self.calls[1][1]
        first_sql = first_remote.read_bytes()
        self.assertEqual(self.cycle(), 0)
        paths = [path for _, path, _ in self.calls]
        self.assertNotIn(first_local, paths)
        self.assertEqual(paths.count(first_remote), 1)
        self.assertEqual(first_remote.read_bytes(), first_sql)
        self.assertFalse(list(self.root.glob("*/*.pending")))

    def test_local_import_failure_does_not_block_sulu(self):
        self.assertEqual(self.cycle(fail="postgres"), 3)
        first_local = self.calls[0][1]
        first_remote = self.calls[1][1]
        self.assertEqual(self.cycle(), 0)
        paths = [path for _, path, _ in self.calls]
        self.assertIn(first_local, paths)
        self.assertNotIn(first_remote, paths)

    def test_sulu_export_failure_retains_report_and_still_imports_local(self):
        with mock.patch.object(pipeline, "build_sulu_export", side_effect=ValueError):
            self.assertEqual(self.cycle(), 1)
        self.assertEqual([name for name, _, _ in self.calls], ["postgres"])
        self.assertTrue(list(self.root.glob("*/aggregate_*.json")))
        self.assertFalse(list(self.root.glob("*/sulu_*.pending")))

    def test_sulu_uses_no_default_password_file_or_local_service_override(self):
        del self.values["SULU_PGPASSFILE"], self.values["SULU_PGSERVICEFILE"]
        with mock.patch.dict(os.environ, {"PGPASSFILE": "/unrelated/passwords",
                             "PGSERVICEFILE": "/unrelated/services"}):
            self.assertEqual(self.cycle(), 0)
        env = self.calls[1][2]
        self.assertEqual(env["PGPASSFILE"], os.devnull)
        self.assertNotIn("PGSERVICEFILE", env)

    def test_opt_out_pauses_only_sulu_and_opt_in_skips_captures_from_pause(self):
        self.assertEqual(self.cycle(fail="sulu"), 3)
        first_remote = self.calls[1][1]
        self.values["SULU_PGSERVICE"] = " "
        self.assertEqual(self.cycle(), 0)
        self.assertEqual([name for name, _, _ in self.calls], ["postgres"])
        paused_capture = self.calls[0][1].parent
        self.assertFalse(list(paused_capture.glob("sulu_*")))
        self.assertTrue(list(first_remote.parent.glob("sulu_*.pending")))
        self.values["SULU_PGSERVICE"] = "sulu-reporting"
        self.assertEqual(self.cycle(), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertIn(first_remote, [path for _, path, _ in self.calls])
        self.assertFalse(list(paused_capture.glob("sulu_*")))
