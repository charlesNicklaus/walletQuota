#!/usr/bin/env python3
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence, TextIO

from walletquota.aggregate import aggregate_results
from walletquota.config import AppSettings, ConfigError
from walletquota.registry import build_providers
from walletquota.reporting import format_terminal_report, write_report_bundle
from walletquota.runner import run_providers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="兼容入口：只运行 ATG Pay 并生成综合订单池报告。"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="本地配置文件路径（默认: .env）",
    )
    parser.add_argument("--sort-type", type=int, help="覆盖 ATG 订单排序类型")
    parser.add_argument("--order-type", type=int, help="覆盖 ATG 订单池类型")
    parser.add_argument(
        "--page-size", type=int, help="ATG 每页请求数量，范围 1–1000"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="综合报告输出目录（默认取 .env 配置）"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="显示不含敏感数据的进度信息"
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    atg_overrides = {
        key: value
        for key, value in {
            "sort_type": args.sort_type,
            "order_type": args.order_type,
            "page_size": args.page_size,
        }.items()
        if value is not None
    }
    try:
        settings = AppSettings.load(
            args.env_file,
            enabled=("atgpay",),
            output_dir=args.output_dir,
        )
        providers = build_providers(
            settings, overrides={"atgpay": atg_overrides}
        )
        if args.verbose:
            print("正在读取渠道: atgpay", file=stderr)
        started_at = datetime.now(timezone.utc)
        results = run_providers(providers)
        aggregate = aggregate_results(
            results, started_at, datetime.now(timezone.utc)
        )
        bundle = write_report_bundle(aggregate, settings.report_directory)
    except ConfigError as error:
        print("配置错误: {}".format(error), file=stderr)
        return 2
    except (OSError, ValueError) as error:
        print("错误: {}".format(error), file=stderr)
        return 1

    print(format_terminal_report(aggregate), file=stdout)
    print("", file=stdout)
    print("综合 JSON: {}".format(bundle.aggregate_json.resolve()), file=stdout)
    for provider, path in bundle.channel_csv.items():
        print("{} CSV: {}".format(provider, path.resolve()), file=stdout)
    return 0 if aggregate.status == "success" else 3


if __name__ == "__main__":
    raise SystemExit(main())
