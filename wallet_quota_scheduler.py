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
    parser.add_argument("--postgres", action="store_true", help="Import each saved snapshot into PostgreSQL")
    parser.add_argument("--include-hoyo", action="store_true", help="Include Hoyo in PostgreSQL mode")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    if threading.current_thread() is not threading.main_thread():
        print("Scheduler setup failed", file=stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.include_hoyo and not args.postgres:
        parser.error("--include-hoyo requires --postgres")
    try:
        settings = AppSettings.load(args.env_file, output_dir=args.output_dir)
    except ConfigError:
        print("Scheduler configuration error", file=stderr)
        return 2
    except OSError:
        print("Scheduler setup failed", file=stderr)
        return 1

    try:
        report_directory = settings.report_directory.resolve()
    except (OSError, RuntimeError):
        print("Scheduler setup failed", file=stderr)
        return 1
    query_args = [
        "--env-file",
        str(args.env_file),
        "--output-dir",
        str(report_directory),
    ]
    if args.verbose:
        query_args.append("--verbose")

    def query():
        if args.postgres:
            from wallet_quota_pipeline import main as pipeline_main
            pipeline_args = query_args + (["--include-hoyo"] if args.include_hoyo else [])
            return pipeline_main(pipeline_args, stdout, stderr)
        return wallet_quota.main(query_args, stdout, stderr)

    previous_handler = None
    installed_handler = False

    def stop(signum, frame):
        raise KeyboardInterrupt

    try:
        with SchedulerLock.acquire(report_directory):
            if threading.current_thread() is threading.main_thread():
                previous_handler = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, stop)
                installed_handler = True
            scheduler = StandaloneScheduler(
                report_directory,
                query,
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
