import io
import json
import signal
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

import wallet_quota_scheduler
from walletquota.config import ConfigError
from walletquota.scheduler import (
    IST,
    SchedulerAlreadyRunning,
    SchedulerLock,
    StandaloneScheduler,
)


class SchedulerCliTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay\n", encoding="utf-8"
        )
        self.report_directory = self.root / "reports"
        self.settings = SimpleNamespace(report_directory=self.report_directory)

    def test_parser_accepts_only_scheduler_options(self):
        destinations = {
            action.dest for action in wallet_quota_scheduler.build_parser()._actions
        }

        self.assertEqual(destinations, {"help", "env_file", "output_dir", "verbose"})

    def test_wires_resolved_query_arguments(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        captured = {}
        original_env = self.env_file.read_text(encoding="utf-8")

        class FakeLock:
            def __enter__(self):
                captured["locked"] = True
                return self

            def __exit__(self, *unused):
                captured["released"] = True

        class FakeScheduler:
            def __init__(self, report_directory, query, clock, **unused):
                captured["report_directory"] = report_directory
                captured["query"] = query
                captured["clock"] = clock

            def run(self):
                captured["query"]()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=self.settings) as load, mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", return_value=FakeLock()
        ), mock.patch.object(wallet_quota_scheduler, "StandaloneScheduler", FakeScheduler), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, "main", return_value=3
        ) as query:
            result = wallet_quota_scheduler.main(
                ["--env-file", str(self.env_file), "--output-dir", str(self.root / "chosen"), "--verbose"],
                stdout,
                stderr,
            )

        self.assertEqual(result, 0)
        load.assert_called_once_with(self.env_file, output_dir=self.root / "chosen")
        frozen_root = self.report_directory.resolve()
        self.assertEqual(captured["report_directory"], frozen_root)
        query.assert_called_once_with(
            ["--env-file", str(self.env_file), "--output-dir", str(frozen_root), "--verbose"],
            stdout,
            stderr,
        )
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(captured["locked"])
        self.assertTrue(captured["released"])
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), original_env)

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

    def test_report_root_resolution_failure_is_safe_before_lock_or_query(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        class FailingRoot:
            def __init__(self, error):
                self.error = error

            def resolve(self):
                raise self.error

        for error in (OSError("secret path"), RuntimeError("secret path")):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                wallet_quota_scheduler.AppSettings,
                "load",
                return_value=SimpleNamespace(report_directory=FailingRoot(error)),
            ), mock.patch.object(wallet_quota_scheduler.SchedulerLock, "acquire") as acquire, mock.patch.object(
                wallet_quota_scheduler.wallet_quota, "main"
            ) as query:
                result = wallet_quota_scheduler.main(
                    ["--env-file", str(self.env_file)], stdout, stderr
                )

            self.assertEqual(result, 1)
            acquire.assert_not_called()
            query.assert_not_called()

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Scheduler setup failed\nScheduler setup failed\n")

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

    def test_keyboard_interrupt_stops_cleanly_and_releases_lock(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        previous_handler = signal.getsignal(signal.SIGTERM)

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=self.settings), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, "main", side_effect=KeyboardInterrupt
        ) as query:
            result = wallet_quota_scheduler.main(
                ["--env-file", str(self.env_file)], stdout, stderr
            )

        self.assertEqual(result, 0)
        query.assert_called_once()
        with SchedulerLock.acquire(self.report_directory):
            pass
        self.assertIs(signal.getsignal(signal.SIGTERM), previous_handler)
        self.assertEqual(stdout.getvalue(), "Scheduler started\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_freezes_report_root_when_env_directory_changes_after_startup(self):
        original_root = self.root / "original-reports"
        changed_root = self.root / "changed-reports"
        original_root.mkdir()
        changed_root.mkdir()
        self.env_file.write_text(
            "ENABLED_PROVIDERS=atgpay\nREPORT_DIRECTORY={}\n".format(original_root),
            encoding="utf-8",
        )
        settings = SimpleNamespace(report_directory=original_root)
        query_calls = []
        captured = {}
        env_file = self.env_file

        class FakeLock:
            def __enter__(self):
                return self

            def __exit__(self, *unused):
                return None

        class FakeScheduler:
            def __init__(self, report_root, query, clock, **unused):
                captured["report_root"] = report_root
                captured["query"] = query

            def run(self):
                env_file.write_text(
                    "ENABLED_PROVIDERS=atgpay\nREPORT_DIRECTORY={}\n".format(changed_root),
                    encoding="utf-8",
                )
                captured["query"]()
                captured["query"]()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=settings), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", return_value=FakeLock()
        ) as acquire, mock.patch.object(wallet_quota_scheduler, "StandaloneScheduler", FakeScheduler), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, "main", side_effect=lambda args, stdout, stderr: query_calls.append(args) or 0
        ):
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), io.StringIO())

        frozen_root = original_root.resolve()
        self.assertEqual(result, 0)
        self.assertEqual(acquire.call_args.args, (frozen_root,))
        self.assertEqual(captured["report_root"], frozen_root)
        self.assertEqual(
            query_calls,
            [
                ["--env-file", str(self.env_file), "--output-dir", str(frozen_root)],
                ["--env-file", str(self.env_file), "--output-dir", str(frozen_root)],
            ],
        )

    def test_freezes_resolved_symlink_root_after_retarget(self):
        original_root = self.root / "original-reports"
        replacement_root = self.root / "replacement-reports"
        original_root.mkdir()
        replacement_root.mkdir()
        alias = self.root / "reports-link"
        alias.symlink_to(original_root, target_is_directory=True)
        settings = SimpleNamespace(report_directory=alias)
        query_calls = []
        captured = {}

        class FakeLock:
            def __enter__(self):
                return self

            def __exit__(self, *unused):
                return None

        class FakeScheduler:
            def __init__(self, report_root, query, clock, **unused):
                captured["report_root"] = report_root
                captured["query"] = query

            def run(self):
                alias.unlink()
                alias.symlink_to(replacement_root, target_is_directory=True)
                captured["query"]()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=settings), mock.patch.object(
            wallet_quota_scheduler.SchedulerLock, "acquire", return_value=FakeLock()
        ) as acquire, mock.patch.object(wallet_quota_scheduler, "StandaloneScheduler", FakeScheduler), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, "main", side_effect=lambda args, stdout, stderr: query_calls.append(args) or 0
        ):
            result = wallet_quota_scheduler.main(["--env-file", str(self.env_file)], io.StringIO(), io.StringIO())

        frozen_root = original_root.resolve()
        self.assertEqual(result, 0)
        self.assertEqual(acquire.call_args.args, (frozen_root,))
        self.assertEqual(captured["report_root"], frozen_root)
        self.assertEqual(
            query_calls,
            [["--env-file", str(self.env_file), "--output-dir", str(frozen_root)]],
        )

    def test_local_bounded_integration_preserves_reports_at_startup_and_midnight(self):
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
        original_reports = {
            path: path.read_bytes()
            for directory in (expired, current)
            for path in directory.iterdir()
        }
        settings = SimpleNamespace(report_directory=report_directory)
        query_calls = []
        reports_at_query = []
        stdout = io.StringIO()
        stderr = io.StringIO()

        def query(*args):
            query_calls.append(args)
            reports_at_query.append(
                {path: path.read_bytes() for path in original_reports if path.exists()}
            )
            return 0

        class BoundedScheduler:
            def __init__(self, report_root, query, clock, **unused):
                times = iter(
                    (
                        datetime(2026, 8, 12, 23, 59, tzinfo=IST),
                        datetime(2026, 8, 13, 0, 1, tzinfo=IST),
                    )
                )
                self.scheduler = StandaloneScheduler(
                    report_root,
                    query,
                    lambda: next(times),
                    lambda target, results=iter((True, False)): next(results),
                    stdout=unused["stdout"],
                    stderr=unused["stderr"],
                )

            def run(self):
                self.scheduler.run()

        with mock.patch.object(wallet_quota_scheduler.AppSettings, "load", return_value=settings), mock.patch.object(
            wallet_quota_scheduler.wallet_quota, "main", side_effect=query
        ), mock.patch.object(
            wallet_quota_scheduler, "StandaloneScheduler", BoundedScheduler
        ):
            result = wallet_quota_scheduler.main(
                ["--env-file", str(self.env_file)], stdout, stderr
            )

        self.assertEqual(result, 0)
        self.assertEqual(reports_at_query, [original_reports, original_reports])
        self.assertEqual(
            {path: path.read_bytes() for path in original_reports}, original_reports
        )
        self.assertTrue(expired.is_dir())
        self.assertTrue(sentinel.is_file())
        self.assertTrue(unknown.is_dir())
        self.assertTrue(current.is_dir())
        self.assertEqual(len(query_calls), 2)
        self.assertEqual(
            stdout.getvalue(),
            "Scheduler started\n"
            "Next scheduled run: 2026-08-13 00:00 IST\n"
            "Next scheduled run: 2026-08-13 00:20 IST\n",
        )
        self.assertEqual(stderr.getvalue(), "")
