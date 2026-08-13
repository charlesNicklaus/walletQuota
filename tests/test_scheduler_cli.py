import io
import json
import signal
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

import wallet_quota_scheduler
from walletquota.config import ConfigError
from walletquota.retention import CleanupResult
from walletquota.scheduler import IST, SchedulerAlreadyRunning, StandaloneScheduler


class SchedulerCliTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env_file = self.root / ".env"
        self.env_file.write_text("ENABLED_PROVIDERS=atgpay\n", encoding="utf-8")
        self.report_directory = self.root / "reports"
        self.settings = SimpleNamespace(report_directory=self.report_directory)

    def test_parser_accepts_only_scheduler_options(self):
        destinations = {
            action.dest for action in wallet_quota_scheduler.build_parser()._actions
        }

        self.assertEqual(destinations, {"help", "env_file", "output_dir", "verbose"})

    def test_wires_resolved_query_arguments_and_cleanup(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        cleanup = mock.Mock(return_value=CleanupResult((), (), ()))
        captured = {}

        class FakeLock:
            def __enter__(self):
                captured["locked"] = True
                return self

            def __exit__(self, *unused):
                captured["released"] = True

        class FakeScheduler:
            def __init__(self, report_directory, query, cleanup_callback, clock, **unused):
                captured["report_directory"] = report_directory
                captured["query"] = query
                captured["cleanup"] = cleanup_callback
                captured["clock"] = clock

            def run(self):
                captured["cleanup"](date(2026, 8, 12))
                captured["query"]()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=self.settings) as load, mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", return_value=FakeLock()
        ), mock.patch.object(wallet_quota_scheduler, "StandaloneScheduler", FakeScheduler), mock.patch.object(
            wallet_quota_scheduler, "cleanup_expired_reports", cleanup
        ), mock.patch.object(wallet_quota_scheduler.wallet_quota, "main", return_value=3) as query:
            result = wallet_quota_scheduler.main(
                ["--env-file", str(self.env_file), "--output-dir", str(self.root / "chosen"), "--verbose"],
                stdout,
                stderr,
            )

        self.assertEqual(result, 0)
        load.assert_called_once_with(self.env_file, output_dir=self.root / "chosen")
        self.assertEqual(captured["report_directory"], self.report_directory)
        query.assert_called_once_with(
            ["--env-file", str(self.env_file), "--output-dir", str(self.root / "chosen"), "--verbose"],
            stdout,
            stderr,
        )
        cleanup.assert_called_once_with(self.report_directory, date(2026, 8, 12))
        self.assertEqual(stdout.getvalue(), "Report cleanup: deleted 0, preserved 0\n")
        self.assertTrue(captured["locked"])
        self.assertTrue(captured["released"])

    def test_cleanup_logging_omits_paths_and_prints_fixed_warnings(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = CleanupResult((), (), ("Unrecognized report directory preserved",))

        wallet_quota_scheduler._log_cleanup(result, stdout, stderr)

        self.assertEqual(stdout.getvalue(), "Report cleanup: deleted 0, preserved 0\n")
        self.assertEqual(stderr.getvalue(), "Unrecognized report directory preserved\n")

    def test_configuration_failure_is_safe_and_happens_before_lock(self):
        stderr = io.StringIO()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", side_effect=ConfigError("secret")), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire"
        ) as acquire:
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), stderr)

        self.assertEqual(result, 2)
        acquire.assert_not_called()
        self.assertEqual(stderr.getvalue(), "Scheduler configuration error\n")

    def test_configuration_os_failure_is_safe_and_happens_before_lock(self):
        stderr = io.StringIO()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", side_effect=OSError("secret")), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire"
        ) as acquire:
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), stderr)

        self.assertEqual(result, 1)
        acquire.assert_not_called()
        self.assertEqual(stderr.getvalue(), "Scheduler setup failed\n")

    def test_non_main_thread_fails_safely_before_configuration_or_lock(self):
        stderr = io.StringIO()

        with mock.patch.object(
            wallet_quota_scheduler.threading, "current_thread", return_value=object()
        ), mock.patch.object(
            wallet_quota_scheduler.threading, "main_thread", return_value=object()
        ), mock.patch.object(wallet_quota_scheduler.AppSettings, "load") as load, mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire"
        ) as acquire:
            result = wallet_quota_scheduler.main(
                ["--env-file", str(self.env_file)], io.StringIO(), stderr
            )

        self.assertEqual(result, 1)
        load.assert_not_called()
        acquire.assert_not_called()
        self.assertEqual(stderr.getvalue(), "Scheduler setup failed\n")

    def test_lock_conflict_is_safe(self):
        stderr = io.StringIO()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=self.settings), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", side_effect=SchedulerAlreadyRunning()
        ):
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), stderr)

        self.assertEqual(result, 1)
        self.assertEqual(stderr.getvalue(), "Scheduler already running\n")

    def test_lock_setup_failure_is_safe(self):
        stderr = io.StringIO()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=self.settings), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", side_effect=OSError("secret")
        ):
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), stderr)

        self.assertEqual(result, 1)
        self.assertEqual(stderr.getvalue(), "Scheduler setup failed\n")

    def test_sigterm_handler_stops_cleanly_releases_lock_and_restores_handler(self):
        stderr = io.StringIO()
        captured = {}

        class FakeLock:
            def __enter__(self):
                return self

            def __exit__(self, *unused):
                captured["released"] = True

        class FakeScheduler:
            def __init__(self, *unused, **unused_kwargs):
                pass

            def run(self):
                captured["handler"](signal.SIGTERM, None)

        previous = object()

        def install(signum, handler):
            if handler is not previous:
                captured["handler"] = handler

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=self.settings), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", return_value=FakeLock()
        ), mock.patch.object(wallet_quota_scheduler, "StandaloneScheduler", FakeScheduler), mock.patch.object(
            wallet_quota_scheduler.signal, "getsignal", return_value=previous
        ), mock.patch.object(wallet_quota_scheduler.signal, "signal", side_effect=install) as signal_call:
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), stderr)

        self.assertEqual(result, 0)
        self.assertTrue(captured["released"])
        self.assertEqual(signal_call.call_args_list[-1].args, (signal.SIGTERM, previous))
        self.assertEqual(stderr.getvalue(), "")

    def test_local_bounded_integration_keeps_outside_sentinel(self):
        report_directory = self.root / "reports"
        report_directory.mkdir()
        sentinel = self.root / "outside-sentinel"
        sentinel.write_text("keep", encoding="utf-8")
        expired = report_directory / "20260811_080000"
        expired.mkdir()
        (expired / "aggregate.json").write_text(
            json.dumps(
                {
                    "metadata": {
                        "generator": "walletQuota",
                        "schema_version": 1,
                        "finished_at": "2026-08-11T08:00:00+05:30",
                    }
                }
            ),
            encoding="utf-8",
        )
        current = report_directory / "20260812_080000"
        current.mkdir()
        (current / "aggregate.json").write_text(
            json.dumps(
                {
                    "metadata": {
                        "generator": "walletQuota",
                        "schema_version": 1,
                        "finished_at": "2026-08-12T08:00:00+05:30",
                    }
                }
            ),
            encoding="utf-8",
        )
        unknown = report_directory / "unknown-child"
        unknown.mkdir()
        settings = SimpleNamespace(report_directory=report_directory)
        query_calls = []
        stdout = io.StringIO()
        stderr = io.StringIO()

        class BoundedScheduler:
            def __init__(self, report_root, query, cleanup_callback, clock, **unused):
                times = iter(
                    (
                        datetime(2026, 8, 12, 12, 0, tzinfo=IST),
                        datetime(2026, 8, 12, 12, 1, tzinfo=IST),
                        datetime(2026, 8, 12, 12, 20, tzinfo=IST),
                        datetime(2026, 8, 12, 12, 21, tzinfo=IST),
                    )
                )
                self.scheduler = StandaloneScheduler(
                    report_root,
                    query,
                    cleanup_callback,
                    lambda: next(times),
                    lambda target, results=iter((True, False)): next(results),
                    stdout=unused["stdout"],
                    stderr=unused["stderr"],
                )

            def run(self):
                self.scheduler.run()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=settings), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, "main", side_effect=lambda *args: query_calls.append(args) or 0
        ), mock.patch.object(
            wallet_quota_scheduler, "StandaloneScheduler", BoundedScheduler
        ):
            result = wallet_quota_scheduler.main(
                ["--env-file", str(self.env_file)], stdout, stderr
            )

        self.assertEqual(result, 0)
        self.assertFalse(expired.exists())
        self.assertTrue(sentinel.is_file())
        self.assertTrue(unknown.is_dir())
        self.assertTrue(current.is_dir())
        self.assertEqual(len(query_calls), 2)
        self.assertEqual(
            stdout.getvalue(),
            "Scheduler started\n"
            "Report cleanup: deleted 1, preserved 2\n"
            "Next scheduled run: 2026-08-12 12:20 IST\n"
            "Next scheduled run: 2026-08-12 12:40 IST\n",
        )
        self.assertEqual(stderr.getvalue(), "Unrecognized report directory preserved\n")
