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
from scripts.export_sulu_pool import build_sulu_export
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
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SULU_PG")}
    environment.update({key: values[key] for key in PG_ENV_KEYS if key in values})
    environment["PGDATABASE"] = environment.get("PGDATABASE") or "walletquota"
    environment.setdefault("PGCONNECT_TIMEOUT", "10")
    return environment


def sulu_postgres_environment(values):
    service = values.get("SULU_PGSERVICE", "").strip()
    if not service:
        return None
    # libpq may read dozens of PG* variables. Start with only the OS settings
    # needed to find psql and the user's service file, plus Sulu's own settings.
    environment = {k: os.environ[k] for k in ("PATH", "HOME", "SYSTEMROOT") if k in os.environ}
    environment.update(
        PGSERVICE=service,
        PGPASSFILE=values.get("SULU_PGPASSFILE") or os.devnull,
        PGCONNECT_TIMEOUT="10",
    )
    if values.get("SULU_PGSERVICEFILE"):
        environment["PGSERVICEFILE"] = values["SULU_PGSERVICEFILE"]
    return environment


def queue_export(directory, target, run_id, sql):
    basename = "{}_{}".format(target, directory.name)
    sql_path = directory / (basename + ".data.sql")
    _atomic_text(sql_path, lambda handle: handle.write(sql))
    marker = {"run_id": str(run_id), "sha256": hashlib.sha256(sql_path.read_bytes()).hexdigest()}
    _atomic_text(directory / (basename + ".pending"),
                 lambda handle: json.dump(marker, handle, sort_keys=True))


def pending_imports(report_root, target="postgres"):
    """Only immediate, real bundle directories belong to this import queue."""
    root = Path(report_root).resolve()
    if target not in {"postgres", "sulu"}:
        raise ValueError("Unknown import queue")
    markers = []
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            markers.extend(child.glob(target + "_*.pending"))
            if target == "postgres":
                markers.append(child / "postgres.pending")  # Legacy batches.
    return sorted(path for path in markers if path.is_file() and not path.is_symlink())


def drain_pending(report_root, environment, target="postgres"):
    """Return (imported_count, remaining_count, failed); retain history on failure."""
    try:
        markers = pending_imports(report_root, target)
    except OSError:
        return 0, None, True  # Could not determine how many imports remain.
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
        report = merge_reports([bundle.aggregate_json])
        run_id, sql = build_export(report)
    except ConfigError:
        print("配置错误: 无法加载采集配置。", file=stderr)
        return 2
    except Exception:
        print("运行错误: 无法完成采集报告或导出。", file=stderr)
        return 1

    sulu_env = sulu_postgres_environment(settings.values)
    export_failed = False
    try:
        queue_export(bundle.directory, "postgres", run_id, sql)
    except Exception:
        print("本地数据库导出失败，已保留原始采集报告。", file=stderr)
        export_failed = True
    if sulu_env is not None:
        try:
            queue_export(bundle.directory, "sulu", run_id, build_sulu_export(report, run_id))
        except Exception:
            print("SuluPay 汇总导出失败，已保留原始采集报告。", file=stderr)
            export_failed = True

    imported, pending, failed = drain_pending(root, postgres_environment(settings.values))
    sulu_failed = False
    if sulu_env is not None:
        sulu_imported, sulu_pending, sulu_failed = drain_pending(root, sulu_env, "sulu")
        print("SuluPay 汇总: imported={} pending={}".format(sulu_imported, sulu_pending), file=stdout)
        if sulu_failed:
            print("SuluPay 汇总导入未完成，已保留待导入历史，下轮重试。", file=stderr)
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
    if export_failed:
        return 1
    return 3 if aggregate.status != "success" or failed or sulu_failed else 0


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
