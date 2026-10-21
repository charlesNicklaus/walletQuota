import io
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase

from walletquota.scheduler import (
    IST,
    SchedulerAlreadyRunning,
    SchedulerLock,
    StandaloneScheduler,
    next_run_after,
)


def ist(day, hour, minute, second=0):
    return datetime(2026, 8, day, hour, minute, second, tzinfo=IST)


class ScheduleTests(TestCase):
    def test_next_run_is_the_strictly_future_ist_boundary(self):
        self.assertEqual(next_run_after(ist(12, 12, 0)), ist(12, 12, 20))
        self.assertEqual(next_run_after(ist(12, 12, 19, 59)), ist(12, 12, 20))
        self.assertEqual(next_run_after(ist(12, 12, 40)), ist(12, 13, 0))
        self.assertEqual(next_run_after(ist(12, 23, 40)), ist(13, 0, 0))

    def test_next_run_rejects_naive_datetimes(self):
        with self.assertRaises(ValueError):
            next_run_after(datetime(2026, 8, 12, 12, 0))


class SchedulerLockTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.report_directory = Path(self.temporary.name) / "reports"

    def test_second_lock_is_rejected_then_release_allows_reacquisition(self):
        first = SchedulerLock.acquire(self.report_directory)
        self.addCleanup(first.release)

        with self.assertRaises(SchedulerAlreadyRunning):
            SchedulerLock.acquire(self.report_directory)

        first.release()
        with SchedulerLock.acquire(self.report_directory):
            pass

    def test_lock_file_is_beside_report_directory_and_persists_after_release(self):
        with SchedulerLock.acquire(self.report_directory) as lock:
            expected = self.report_directory.resolve().parent / (
                ".reports.wallet_quota_scheduler.lock"
            )
            self.assertEqual(lock.path, expected)
            self.assertTrue(lock.path.is_file())
            self.assertFalse(str(lock.path).startswith(str(self.report_directory)))

        self.assertTrue(expected.is_file())

    def test_absolute_relative_and_symlink_aliases_contend_for_one_lock(self):
        root = Path(self.temporary.name)
        reports = root / "reports"
        reports.mkdir()
        alias = root / "reports-alias"
        alias.symlink_to(reports, target_is_directory=True)
        relative = Path(self.temporary.name) / "nested" / ".." / "reports"

        first = SchedulerLock.acquire(relative)
        self.addCleanup(first.release)

        with self.assertRaises(SchedulerAlreadyRunning):
            SchedulerLock.acquire(reports.resolve())
        with self.assertRaises(SchedulerAlreadyRunning):
            SchedulerLock.acquire(alias)

        self.assertEqual(
            first.path,
            reports.resolve().parent / ".reports.wallet_quota_scheduler.lock",
        )


class StandaloneSchedulerTests(TestCase):
    def scheduler(self, times, wait_results, query_results):
        events = []
        targets = []
        times = iter(times)
        wait_results = iter(wait_results)
        query_results = iter(query_results)

        def clock():
            return next(times)

        def query():
            result = next(query_results)
            events.append(("query", result))
            return result

        def wait(target):
            targets.append(target)
            return next(wait_results)

        scheduler = StandaloneScheduler(
            Path("unused-reports"),
            query,
            clock,
            wait,
            io.StringIO(),
            io.StringIO(),
        )
        return scheduler, events, targets

    def test_startup_queries_immediately_and_same_day_cycles_query(self):
        scheduler, events, targets = self.scheduler(
            (ist(12, 12, 1), ist(12, 12, 21)),
            (True, False),
            (0, 3),
        )

        scheduler.run()

        self.assertEqual(
            events,
            [("query", 0), ("query", 3)],
        )
        self.assertEqual(targets, [ist(12, 12, 20), ist(12, 12, 40)])
        self.assertEqual(scheduler.query_exit_codes, (0, 3))

    def test_midnight_cycle_queries_on_the_next_boundary(self):
        scheduler, events, targets = self.scheduler(
            (ist(12, 23, 41), ist(13, 0, 1)),
            (True, False),
            (1, 2),
        )

        scheduler.run()

        self.assertEqual(
            events,
            [
                ("query", 1),
                ("query", 2),
            ],
        )
        self.assertEqual(scheduler.query_exit_codes, (1, 2))
        self.assertEqual(targets, [ist(13, 0, 0), ist(13, 0, 20)])

    def test_slow_query_waits_only_for_the_next_future_boundary(self):
        scheduler, events, targets = self.scheduler(
            (ist(12, 12, 45),),
            (False,),
            (0,),
        )

        scheduler.run()

        self.assertEqual(events, [("query", 0)])
        self.assertEqual(targets, [ist(12, 13, 0)])

    def test_stop_request_exits_without_another_query(self):
        scheduler, events, targets = self.scheduler(
            (ist(12, 12, 1),),
            (False,),
            (0,),
        )

        scheduler.run()

        self.assertEqual(events, [("query", 0)])
        self.assertEqual(targets, [ist(12, 12, 20)])

    def test_query_exceptions_are_safe_and_do_not_stop_the_loop(self):
        events = []
        times = iter((ist(12, 12, 1), ist(12, 12, 21)))
        stderr = io.StringIO()
        query_calls = 0

        def query():
            nonlocal query_calls
            query_calls += 1
            events.append("query")
            if query_calls == 1:
                raise RuntimeError("secret response must not be printed")
            return 0

        scheduler = StandaloneScheduler(
            Path("unused-reports"),
            query,
            lambda: next(times),
            lambda target, results=iter((True, False)): next(results),
            io.StringIO(),
            stderr,
        )

        scheduler.run()

        self.assertEqual(events, ["query", "query"])
        self.assertEqual(scheduler.query_exit_codes, (0,))
        self.assertEqual(stderr.getvalue(), "Scheduled query failed\n")

    def test_operational_messages_are_fixed_and_use_separate_streams(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        scheduler = StandaloneScheduler(
            Path("unused-reports"),
            lambda: 0,
            lambda: ist(12, 12, 1),
            lambda target: False,
            stdout,
            stderr,
        )

        scheduler.run()

        self.assertEqual(
            stdout.getvalue(),
            "Scheduler started\nNext scheduled run: 2026-08-12 12:20 IST\n",
        )
        self.assertEqual(stderr.getvalue(), "")
