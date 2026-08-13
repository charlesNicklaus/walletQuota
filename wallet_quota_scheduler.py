#!/usr/bin/env python3
import argparse
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, TextIO

import wallet_quota
from walletquota.config import AppSettings, ConfigError
from walletquota.retention import CleanupResult, cleanup_expired_reports
from walletquota.scheduler import (
    IST,
    SchedulerAlreadyRunning,
    SchedulerLock,
    StandaloneScheduler,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run wallet quota reports on the IST 20-minute schedule."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verbose", action="store_true")
    return parser


def _log_cleanup(result: CleanupResult, stdout: TextIO, stderr: TextIO) -> None:
    print(
        "Report cleanup: deleted {}, preserved {}".format(
            len(result.deleted), len(result.preserved)
        ),
        file=stdout,
    )
    for warning in result.warnings:
        print(warning, file=stderr)


def main(
    argv: Optional[Sequence[str]] = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    if threading.current_thread() is not threading.main_thread():
        print("Scheduler setup failed", file=stderr)
        return 1
    args = build_parser().parse_args(argv)
    try:
        settings = AppSettings.load(args.env_file, output_dir=args.output_dir)
    except ConfigError:
        print("Scheduler configuration error", file=stderr)
        return 2
    except OSError:
        print("Scheduler setup failed", file=stderr)
        return 1

    query_args = ["--env-file", str(args.env_file)]
    if args.output_dir is not None:
        query_args.extend(["--output-dir", str(args.output_dir)])
    if args.verbose:
        query_args.append("--verbose")

    def cleanup(today):
        result = cleanup_expired_reports(settings.report_directory, today)
        _log_cleanup(result, stdout, stderr)
        return result

    previous_handler = None
    installed_handler = False

    def stop(signum, frame):
        raise KeyboardInterrupt

    try:
        with SchedulerLock.acquire(settings.report_directory):
            if threading.current_thread() is threading.main_thread():
                previous_handler = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, stop)
                installed_handler = True
            scheduler = StandaloneScheduler(
                settings.report_directory,
                lambda: wallet_quota.main(query_args, stdout, stderr),
                cleanup,
                lambda: datetime.now(IST),
                stdout=stdout,
                stderr=stderr,
            )
            scheduler.run()
    except SchedulerAlreadyRunning:
        print("Scheduler already running", file=stderr)
        return 1
    except (OSError, ValueError):
        print("Scheduler setup failed", file=stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if installed_handler:
            signal.signal(signal.SIGTERM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
