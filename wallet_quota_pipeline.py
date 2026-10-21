#!/usr/bin/env python3
"""Collect one wallet snapshot, export it, and retry retained PostgreSQL imports."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from scripts import hoyopay_readonly as hoyo
from scripts.export_postgres import build_export
from scripts.merge_wallet_reports import SafeArgumentParser, load_channel, merge_reports
from walletquota.aggregate import aggregate_results
from walletquota.config import AppSettings, ConfigError
from walletquota.models import ProviderResult
from walletquota.providers.signed_pool.client import APIError, AuthenticationError
from walletquota.registry import PROVIDER_FACTORIES, build_providers
from walletquota.reporting import _atomic_text, write_report_bundle
from walletquota.runner import run_providers
from walletquota.summary import summarize_orders


PG_ENV_KEYS = (
    "PGDATABASE", "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGPASSFILE",
    "PGSERVICE", "PGSERVICEFILE", "PGSSLMODE",
)


class HoyoPayProvider:
    name = "hoyopay"

    def __init__(self, values, overrides=None):
        self.values = values
        self.overrides = overrides or {}

    def _settings(self):
        defaults = hoyo.Settings(
            phone=self.values.get("HOYOPAY_PHONE", ""),
            password=self.values.get("HOYOPAY_PASSWORD", ""),
        )
        selected = {}
        for name, converter in (
            ("page_size", int), ("sort_type", int),
            ("request_delay_ms", int), ("timeout_seconds", float),
        ):
            key = "HOYOPAY_" + name.upper()
            value = self.overrides.get(name, self.values.get(key, getattr(defaults, name)))
            selected[name] = converter(value)
        settings = replace(defaults, **selected)
        if (not 1 <= settings.page_size <= 1000 or settings.request_delay_ms < 0
                or not math.isfinite(settings.timeout_seconds)
                or settings.timeout_seconds <= 0):
            raise ConfigError("Invalid HOYO PAY collection settings")
        return settings

    def run(self):
        try:
            settings = self._settings()
        except (ConfigError, TypeError, ValueError, OverflowError):
            return ProviderResult.failure(self.name, "configuration", "HOYO PAY configuration is invalid")
        try:
            raw = hoyo.collect(settings, hoyo.SignedPoolClient(settings, hoyo.SPEC))
            return load_channel(self.name, raw, raw["metadata"])
        except AuthenticationError:
            return ProviderResult.failure(self.name, "authentication", "HOYO PAY authentication failed")
        except APIError:
            return ProviderResult.failure(self.name, "api", "HOYO PAY API collection failed")
        except Exception:
            return ProviderResult.failure(self.name, "internal", "Unexpected HOYO PAY collection failure")


def build_cycle_providers(settings, include_hoyo=False):
    factories = dict(PROVIDER_FACTORIES)
    if include_hoyo:
        factories["hoyopay"] = HoyoPayProvider
        names = tuple(dict.fromkeys((*settings.enabled_providers, "hoyopay")))
        settings = AppSettings.from_values(
            settings.values, enabled=names, output_dir=settings.report_directory
        )
    return build_providers(settings, factories=factories)


def postgres_environment(values):
    environment = os.environ.copy()
    environment.update({key: values[key] for key in PG_ENV_KEYS if key in values})
    environment["PGDATABASE"] = environment.get("PGDATABASE") or "walletquota"
    environment.setdefault("PGCONNECT_TIMEOUT", "10")
    return environment


def pending_imports(report_root):
    """Only immediate, real bundle directories belong to this import queue."""
    root = Path(report_root).resolve()
    return sorted(
        marker for child in root.iterdir()
        if child.is_dir() and not child.is_symlink()
        for marker in (child / "postgres.pending", *child.glob("postgres_*.pending"))
        if marker.is_file() and not marker.is_symlink()
    )


def drain_pending(report_root, environment):
    """Return (imported_count, remaining_count, failed); retain history on failure."""
    markers = pending_imports(report_root)
    imported = 0
    for marker in markers:
        try:
            sql_path = marker.with_suffix(".data.sql")
            if sql_path.is_symlink() or not sql_path.is_file():
                raise ValueError("Missing private SQL export")
            data = json.loads(marker.read_text(encoding="utf-8"))
            if set(data) != {"run_id", "sha256"}:
                raise ValueError("Invalid import marker")
            UUID(data["run_id"])
            if hashlib.sha256(sql_path.read_bytes()).hexdigest() != data["sha256"]:
                raise ValueError("SQL export does not match its marker")
            process = subprocess.run(
                ["psql", "-X", "-w", "-v", "ON_ERROR_STOP=1", "-f", str(sql_path.resolve())],
                env=environment, capture_output=True, timeout=60, check=False,
            )
            if process.returncode != 0:
                return imported, len(markers) - imported, True
            marker.replace(marker.with_suffix(".imported"))
            imported += 1
        except Exception:
            # libpq, the shell, paths, and exception messages may contain secrets.
            return imported, len(markers) - imported, True
    return imported, 0, False


def run_cycle(settings, include_hoyo=False, verbose=False,
              stdout=sys.stdout, stderr=sys.stderr):
    """Write a new snapshot before retrying imports; return CLI status 0/1/2/3."""
    try:
        started_at = datetime.now(timezone.utc)
        providers = build_cycle_providers(settings, include_hoyo)
        if verbose:
            print("采集开始: channels={}".format(len(providers)), file=stderr)
        results = run_providers(providers)
        aggregate = aggregate_results(results, started_at, datetime.now(timezone.utc))
        root = settings.report_directory.resolve()
        bundle = write_report_bundle(aggregate, root)
        run_id, sql = build_export(merge_reports([bundle.aggregate_json]))
        basename = "postgres_{}".format(bundle.directory.name)
        sql_path = bundle.directory / (basename + ".data.sql")
        _atomic_text(sql_path, lambda handle: handle.write(sql))
        marker = {
            "run_id": str(run_id),
            "sha256": hashlib.sha256(sql_path.read_bytes()).hexdigest(),
        }
        _atomic_text(bundle.directory / (basename + ".pending"),
                     lambda handle: json.dump(marker, handle, sort_keys=True))
        imported, pending, failed = drain_pending(root, postgres_environment(settings.values))
    except ConfigError:
        print("配置错误: 无法加载采集配置。", file=stderr)
        return 2
    except Exception:
        print("运行错误: 无法完成采集报告或导出。", file=stderr)
        return 1

    print("采集完成: status={} channels={} orders={} imported={} pending={}".format(
        aggregate.status, len(aggregate.channels), aggregate.total["order_count"],
        imported, pending,
    ), file=stdout)
    for channel in aggregate.channels:
        summary = summarize_orders(channel.orders)
        print("渠道: provider={} status={} observed_orders={} observed_amount={} error={}".format(
            channel.provider, channel.status, summary["order_count"],
            summary["order_amount"], channel.error.category if channel.error else "none",
        ), file=stdout)
    if failed:
        print("数据库导入未完成: 已保留待导入历史，下轮重试。", file=stderr)
    return 3 if aggregate.status != "success" or failed else 0


def build_parser():
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--include-hoyo", action="store_true")
    return parser


def main(argv=None, stdout=sys.stdout, stderr=sys.stderr):
    args = build_parser().parse_args(argv)
    try:
        settings = AppSettings.load(args.env_file, output_dir=args.output_dir)
    except ConfigError:
        print("配置错误: 无法加载采集配置。", file=stderr)
        return 2
    except Exception:
        print("运行错误: 无法读取配置。", file=stderr)
        return 1
    return run_cycle(settings, args.include_hoyo, args.verbose, stdout, stderr)


if __name__ == "__main__":
    raise SystemExit(main())
