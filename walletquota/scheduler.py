import errno
import fcntl
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, TextIO, Tuple
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


class SchedulerAlreadyRunning(Exception):
    pass


class SchedulerLock:
    def __init__(self, path: Path, handle: TextIO) -> None:
        self.path = path
        self._handle: Optional[TextIO] = handle

    @classmethod
    def acquire(cls, report_directory: Path) -> "SchedulerLock":
        root = Path(report_directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root.parent / (
            ".{}.wallet_quota_scheduler.lock".format(root.name)
        )
        handle = path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise SchedulerAlreadyRunning() from None
            raise
        return cls(path, handle)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SchedulerLock":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def next_run_after(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Scheduler time must be timezone-aware")
    local = now.astimezone(IST)
    rounded = local.replace(second=0, microsecond=0)
    return rounded + timedelta(minutes=20 - rounded.minute % 20)


def _wait_until(target: datetime) -> bool:
    while True:
        remaining = (target - datetime.now(IST)).total_seconds()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 1))


class StandaloneScheduler:
    def __init__(
        self,
        report_directory: Path,
        query: Callable[[], int],
        clock: Callable[[], datetime],
        wait: Callable[[datetime], bool] = _wait_until,
        stdout: Optional[TextIO] = None,
        stderr: Optional[TextIO] = None,
    ) -> None:
        self.report_directory = Path(report_directory)
        self.query = query
        self.clock = clock
        self.wait = wait
        self.stdout = stdout
        self.stderr = stderr
        self.query_exit_codes: Tuple[int, ...] = ()

    def _query_once(self) -> None:
        try:
            exit_code = self.query()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            if self.stderr is not None:
                self.stderr.write("Scheduled query failed\n")
            return
        self.query_exit_codes += (exit_code,)

    def _log_startup(self) -> None:
        if self.stdout is not None:
            self.stdout.write("Scheduler started\n")

    def _log_next_run(self, target: datetime) -> None:
        if self.stdout is not None:
            self.stdout.write(
                "Next scheduled run: {} IST\n".format(
                    target.astimezone(IST).strftime("%Y-%m-%d %H:%M")
                )
            )

    def run(self) -> None:
        self._log_startup()
        self._query_once()

        while True:
            target = next_run_after(self.clock())
            self._log_next_run(target)
            if not self.wait(target):
                return
            self._query_once()
