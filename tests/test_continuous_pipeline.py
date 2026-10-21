import importlib
import hashlib
import io
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase, mock

from walletquota.config import AppSettings
from walletquota.models import Order, ProviderResult


SECRET = "PIPELINE_FIXTURE_SECRET"


class StaticProvider:
    def __init__(self, name, partial=False):
        self.name = name
        self.calls = 0
        orders = (Order(name, name + "-fixture", Decimal("10.25")),)
        self.result = (
            ProviderResult.partial(name, orders, {}, "Incomplete fixture")
            if partial else ProviderResult.success(name, orders, {})
        )

    def run(self):
        self.calls += 1
        return self.result


def hoyo_result(empty=False, partial=False):
    return {
        "provider": "hoyopay", "status": "partial" if partial else "success",
        "complete": not partial,
        "metadata": {
            "started_at": "2026-01-02T01:00:00+00:00",
            "finished_at": "2026-01-02T01:00:01+00:00",
        },
        "pools": [{"order_type": 1}, {"order_type": 2}],
        "orders": [] if empty else [{
            "provider": "hoyopay", "order_id": "hoyo-fixture",
            "amount": "12.345", "attributes": {"token": SECRET},
        }],
        "accounts": [],
    }


class ContinuousPipelineTests(TestCase):
    def setUp(self):
        try:
            self.pipeline = importlib.import_module("wallet_quota_pipeline")
        except ModuleNotFoundError:
            self.fail("The one-cycle pipeline entry point has not been implemented")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reports = self.root / "reports"
        self.values = {
            "ENABLED_PROVIDERS": "atgpay,millerpay,penguinpay",
            "HOYOPAY_PHONE": "fictional-phone", "HOYOPAY_PASSWORD": SECRET,
            "HOYOPAY_PAGE_SIZE": "17", "PGPASSWORD": SECRET,
            "PGDATABASE": "fixture_db", "PGHOST": "fixture-host",
            "PGPORT": "6543", "PGUSER": "fixture-user",
            "PGPASSFILE": "/fixture/.pgpass", "PGSERVICE": "fixture-service",
            "PGSERVICEFILE": "/fixture/service.conf", "PGSSLMODE": "require",
            "PGOPTIONS": SECRET, "UNRELATED_SETTING": SECRET,
        }
        self.env_file = self.root / "wallet.env"
        self.env_file.write_text("\n".join(
            key + "=" + value for key, value in self.values.items()
        ), encoding="utf-8")
        self.providers = [StaticProvider(name) for name in (
            "atgpay", "millerpay", "penguinpay"
        )]

    def invoke(self, include=True, result=None, runner=None, env_file=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        factories = {
            provider.name: (lambda values, overrides, p=provider: p)
            for provider in self.providers
        }
        args = ["--env-file", str(env_file or self.env_file),
                "--output-dir", str(self.reports), "--verbose"]
        if include:
            args.append("--include-hoyo")
        with mock.patch.object(self.pipeline, "PROVIDER_FACTORIES", factories), \
                mock.patch.object(self.pipeline.hoyo, "collect", return_value=(
                    hoyo_result() if result is None else result
                )) as collect, \
                mock.patch.object(self.pipeline.hoyo, "SignedPoolClient") as client, \
                mock.patch.object(self.pipeline.subprocess, "run", side_effect=(
                    runner or (lambda *args, **kwargs: subprocess.CompletedProcess(
                        args[0], 0, SECRET, SECRET
                    ))
                )) as process:
            code = self.pipeline.main(args, stdout, stderr)
        return code, stdout.getvalue() + stderr.getvalue(), collect, client, process

    def bundles(self):
        return sorted(path.parent for path in self.reports.glob("*/aggregate_*.json"))

    def test_four_channels_export_import_private_history_and_pg_environment(self):
        with mock.patch.dict(os.environ, {"PARENT_FIXTURE": "kept"}, clear=True):
            code, logs, collect, client, process = self.invoke()

        self.assertEqual(code, 0)
        self.assertEqual([provider.calls for provider in self.providers], [1, 1, 1])
        self.assertEqual(collect.call_args.args[0].page_size, 17)
        self.assertEqual(client.call_args.args[1], self.pipeline.hoyo.SPEC)
        bundle = self.bundles()[0]
        stamp = bundle.name
        self.assertEqual({path.name for path in bundle.iterdir()}, {
            "aggregate_{}.json".format(stamp),
            "postgres_{}.data.sql".format(stamp),
            "postgres_{}.imported".format(stamp),
            *["{}_orders_{}.csv".format(name, stamp) for name in (
                "atgpay", "millerpay", "penguinpay", "hoyopay"
            )],
        })
        payload = json.loads((bundle / "aggregate_{}.json".format(bundle.name)).read_text())
        self.assertEqual(list(payload["channels"]), [
            "atgpay", "millerpay", "penguinpay", "hoyopay"
        ])
        self.assertEqual(payload["total"]["order_count"], 4)
        self.assertEqual(payload["total"]["order_amount"], "43.095")
        self.assertIn("pool_snapshots", payload["channels"]["hoyopay"]["metadata"])
        for key in ("started_at", "finished_at"):
            self.assertEqual(datetime.fromisoformat(payload["metadata"][key]).utcoffset().total_seconds(), 0)
        self.assertLessEqual(payload["metadata"]["started_at"], payload["metadata"]["finished_at"])
        self.assertFalse((bundle / "postgres_{}.pending".format(bundle.name)).exists())
        marker = json.loads((bundle / "postgres_{}.imported".format(bundle.name)).read_text())
        self.assertIn(marker["run_id"], (bundle / "postgres_{}.data.sql".format(bundle.name)).read_text())
        for path in bundle.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(SECRET, path.read_text())
        args, kwargs = process.call_args
        self.assertEqual(args[0], ["psql", "-X", "-w", "-v", "ON_ERROR_STOP=1",
                                  "-f", str((bundle / "postgres_{}.data.sql".format(bundle.name)).resolve())])
        self.assertEqual(kwargs["timeout"], 60)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["env"]["PARENT_FIXTURE"], "kept")
        self.assertEqual(kwargs["env"]["PGCONNECT_TIMEOUT"], "10")
        for key in self.values:
            if key.startswith("PG") and key != "PGOPTIONS":
                self.assertEqual(kwargs["env"][key], self.values[key])
        self.assertNotIn("PGOPTIONS", kwargs["env"])
        self.assertNotIn("UNRELATED_SETTING", kwargs["env"])
        self.assertNotIn(SECRET, logs + repr(args))

    def test_hoyo_empty_pool_is_complete_and_missing_credentials_only_fail_hoyo(self):
        code, _, _, _, _ = self.invoke(result=hoyo_result(empty=True))
        self.assertEqual(code, 0)
        payload = json.loads((self.bundles()[0] / "aggregate_{}.json".format(self.bundles()[0].name)).read_text())
        self.assertEqual(payload["channels"]["hoyopay"]["status"], "success")
        self.assertEqual(payload["total"]["order_count"], 3)
        self.env_file.write_text("ENABLED_PROVIDERS=atgpay,millerpay,penguinpay\n")

        code, logs, collect, _, process = self.invoke()

        self.assertEqual(code, 3)
        collect.assert_not_called()
        process.assert_called_once()
        payload = json.loads((self.bundles()[-1] / "aggregate_{}.json".format(self.bundles()[-1].name)).read_text())
        self.assertEqual(payload["channels"]["hoyopay"]["error"]["category"], "configuration")
        self.assertEqual(payload["total"]["order_count"], 3)
        self.assertIn("provider=hoyopay status=error observed_orders=0 observed_amount=0 error=configuration", logs)
        self.assertNotIn(SECRET, logs)

    def test_partial_channels_preserve_orders_but_are_excluded_from_trusted_total(self):
        self.providers[1] = StaticProvider("millerpay", partial=True)

        code, logs, _, _, process = self.invoke(result=hoyo_result(partial=True))

        self.assertEqual(code, 3)
        self.assertEqual(process.call_count, 1)
        payload = json.loads((self.bundles()[0] / "aggregate_{}.json".format(self.bundles()[0].name)).read_text())
        self.assertEqual(payload["metadata"]["status"], "partial")
        self.assertEqual(payload["total"]["order_count"], 2)
        self.assertEqual(len(payload["channels"]["hoyopay"]["orders"]), 1)
        self.assertIn("provider=hoyopay status=partial observed_orders=1 observed_amount=12.345 error=incomplete", logs)
        self.assertNotIn("Incomplete fixture", logs)
        self.assertNotIn(SECRET, logs)

    def test_failed_database_is_queued_and_next_cycle_collects_before_retry(self):
        code, logs, _, _, process = self.invoke(runner=lambda *a, **k:
            subprocess.CompletedProcess(a[0], 1, SECRET, SECRET))
        self.assertEqual(code, 3)
        process.assert_called_once()
        first = self.bundles()[0]
        self.assertTrue((first / "postgres_{}.pending".format(first.name)).is_file())

        def retry(args, **kwargs):
            self.assertEqual(len(self.bundles()), 2)
            self.assertEqual([provider.calls for provider in self.providers], [2, 2, 2])
            return subprocess.CompletedProcess(args, 0, SECRET, SECRET)

        code, second_logs, _, _, process = self.invoke(runner=retry)

        self.assertEqual(code, 0)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(process.call_args_list[0].args[0][-1], str((first / "postgres_{}.data.sql".format(first.name)).resolve()))
        self.assertEqual(len(self.bundles()), 2)
        for directory in self.bundles():
            self.assertTrue((directory / "aggregate_{}.json".format(directory.name)).is_file())
            self.assertTrue((directory / "postgres_{}.data.sql".format(directory.name)).is_file())
            self.assertTrue((directory / "postgres_{}.imported".format(directory.name)).is_file())
        self.assertNotIn(SECRET, logs + second_logs)

    def test_an_offline_database_stops_drain_after_first_failure(self):
        failure = lambda *a, **k: subprocess.CompletedProcess(a[0], 1, SECRET, SECRET)
        self.invoke(runner=failure)
        code, logs, _, _, process = self.invoke(runner=failure)
        self.assertEqual(code, 3)
        self.assertEqual(process.call_count, 1)
        self.assertEqual(len(list(self.reports.glob("*/postgres_*.pending"))), 2)
        self.assertNotIn(SECRET, logs)

    def test_upgrade_retries_legacy_pending_batch_with_new_timestamped_capture(self):
        code, _, _, _, _ = self.invoke(runner=lambda *a, **k:
            subprocess.CompletedProcess(a[0], 1))
        self.assertEqual(code, 3)
        old = self.bundles()[0]
        (old / "postgres_{}.data.sql".format(old.name)).rename(old / "postgres.data.sql")
        (old / "postgres_{}.pending".format(old.name)).rename(old / "postgres.pending")
        (old / "aggregate_{}.json".format(old.name)).rename(old / "aggregate.json")
        old_json = (old / "aggregate.json").read_bytes()
        old_sql = (old / "postgres.data.sql").read_bytes()

        code, logs, _, _, process = self.invoke()

        self.assertEqual(code, 0)
        self.assertEqual(process.call_count, 2)
        self.assertEqual(process.call_args_list[0].args[0][-1], str((old / "postgres.data.sql").resolve()))
        self.assertFalse((old / "postgres.pending").exists())
        self.assertTrue((old / "postgres.imported").is_file())
        self.assertEqual((old / "aggregate.json").read_bytes(), old_json)
        self.assertEqual((old / "postgres.data.sql").read_bytes(), old_sql)
        fresh = self.bundles()[0]
        self.assertNotEqual(old, fresh)
        self.assertTrue((fresh / "postgres_{}.imported".format(fresh.name)).is_file())
        self.assertIn("imported=2 pending=0", logs)

    def test_timestamped_marker_symlink_is_not_an_import_candidate(self):
        bundle = self.reports / "20261021_160000"
        bundle.mkdir(parents=True)
        outside = self.root / "outside.pending"
        outside.write_text("{}")
        marker = bundle / "postgres_20261021_160000.pending"
        marker.symlink_to(outside)

        with mock.patch.object(self.pipeline.subprocess, "run") as process:
            self.assertEqual(self.pipeline.drain_pending(self.reports, {}), (0, 0, False))

        process.assert_not_called()
        self.assertTrue(marker.is_symlink())
        self.assertEqual(outside.read_text(), "{}")

    def test_timestamped_pending_marker_imports_its_matching_sql(self):
        bundle = self.reports / "20261021_160000_2"
        bundle.mkdir(parents=True)
        sql = bundle / "postgres_20261021_160000_2.data.sql"
        sql.write_text("-- fixture SQL\n")
        marker = bundle / "postgres_20261021_160000_2.pending"
        marker.write_text(json.dumps({
            "run_id": "00000000-0000-0000-0000-000000000001",
            "sha256": hashlib.sha256(sql.read_bytes()).hexdigest(),
        }))

        with mock.patch.object(self.pipeline.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0)) as process:
            self.assertEqual(self.pipeline.drain_pending(self.reports, {}), (1, 0, False))

        self.assertEqual(process.call_args.args[0][-1], str(sql.resolve()))
        self.assertFalse(marker.exists())
        self.assertTrue((bundle / "postgres_20261021_160000_2.imported").is_file())
        self.assertEqual(sql.read_text(), "-- fixture SQL\n")

    def test_failed_export_never_queues_or_imports_an_incomplete_sql_file(self):
        atomic = self.pipeline._atomic_text

        def fail_sql(path, writer):
            if path.name.endswith(".data.sql"):
                raise OSError(SECRET)
            return atomic(path, writer)

        with mock.patch.object(self.pipeline, "_atomic_text", side_effect=fail_sql):
            code, logs, _, _, process = self.invoke()

        self.assertEqual(code, 1)
        process.assert_not_called()
        self.assertEqual(len(self.bundles()), 1)
        self.assertEqual(list(self.reports.glob("*/postgres_*.pending")), [])
        self.assertEqual(list(self.reports.glob("*/postgres_*.data.sql")), [])
        self.assertNotIn(SECRET, logs)

    def test_default_keeps_only_configured_channels_and_never_overrides_pagination(self):
        self.values["ENABLED_PROVIDERS"] = "penguinpay"
        settings = AppSettings.from_values(self.values, output_dir=self.reports)
        factory = mock.Mock(return_value=self.providers[2])
        with mock.patch.object(self.pipeline, "PROVIDER_FACTORIES", {"penguinpay": factory}):
            providers = self.pipeline.build_cycle_providers(settings, False)
        self.assertEqual([p.name for p in providers], ["penguinpay"])
        self.assertEqual(factory.call_args.args, (settings.values, {}))
        self.values["ENABLED_PROVIDERS"] = "penguinpay,hoyopay"
        settings = AppSettings.from_values(self.values, output_dir=self.reports)
        with mock.patch.object(self.pipeline, "PROVIDER_FACTORIES", {"penguinpay": factory}):
            providers = self.pipeline.build_cycle_providers(settings, True)
        self.assertEqual([p.name for p in providers], ["penguinpay", "hoyopay"])

    def test_subprocess_exceptions_and_bad_configuration_are_safe(self):
        for error in (FileNotFoundError(SECRET), subprocess.TimeoutExpired(SECRET, 60)):
            with self.subTest(error=type(error).__name__):
                code, logs, _, _, _ = self.invoke(runner=mock.Mock(side_effect=error))
                self.assertEqual(code, 3)
                self.assertNotIn(SECRET, logs)
        code, logs, _, _, process = self.invoke(env_file=self.root / "missing.env")
        self.assertEqual(code, 2)
        process.assert_not_called()
        self.assertNotIn("Traceback", logs)

    def test_hoyo_failures_use_fixed_categories_without_exception_text(self):
        from walletquota.providers.signed_pool.client import APIError, AuthenticationError
        cases = [(AuthenticationError(SECRET), "authentication"),
                 (APIError(SECRET), "api"), (RuntimeError(SECRET), "internal")]
        for error, category in cases:
            with self.subTest(category=category), \
                    mock.patch.object(self.pipeline.hoyo, "collect", side_effect=error), \
                    mock.patch.object(self.pipeline.hoyo, "SignedPoolClient"):
                result = self.pipeline.HoyoPayProvider(self.values).run()
                self.assertEqual(result.error.category, category)
                self.assertNotIn(SECRET, result.error.message)

    def test_drain_ignores_nested_and_symlink_bundles_and_rejects_changed_sql(self):
        self.invoke(runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 1))
        first = self.bundles()[0]
        (first / "postgres_{}.data.sql".format(first.name)).write_text("changed fixture export")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "postgres.pending").write_text("{}")
        (self.reports / "linked").symlink_to(outside, target_is_directory=True)
        nested = self.reports / "nested" / "deeper"
        nested.mkdir(parents=True)
        (nested / "postgres.pending").write_text("{}")

        code, _, _, _, process = self.invoke()

        self.assertEqual(code, 3)
        process.assert_not_called()
        self.assertTrue((first / "postgres_{}.pending".format(first.name)).is_file())
        self.assertTrue((outside / "postgres.pending").is_file())
