#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from atg_pool.client import APIError, AtgClient
from atg_pool.config import ConfigError, Settings
from atg_pool.pool import PoolDataError, collect_pool
from atg_pool.report import build_summary, format_terminal_report, write_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="登录 ATG Pay 并生成只读订单池统计报告。"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="本地配置文件路径（默认: .env）",
    )
    parser.add_argument("--sort-type", type=int, help="覆盖订单排序类型")
    parser.add_argument("--order-type", type=int, help="覆盖订单池类型")
    parser.add_argument(
        "--page-size", type=int, help="每页请求数量，范围 1–1000"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="CSV/JSON 输出目录（默认取 .env 配置）"
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
    overrides = {
        "sort_type": args.sort_type,
        "order_type": args.order_type,
        "page_size": args.page_size,
        "report_directory": args.output_dir,
    }
    try:
        settings = Settings.load(args.env_file, overrides=overrides)
        if args.verbose:
            print("正在登录 ATG Pay…", file=stderr)
        client = AtgClient(settings)
        client.login()
        if args.verbose:
            print("正在读取订单池全部分页…", file=stderr)
        snapshot = collect_pool(client, settings)
        summary = build_summary(snapshot)
        csv_path, json_path = write_reports(
            snapshot, summary, settings.report_directory
        )
    except ConfigError as error:
        print("配置错误: {}".format(error), file=stderr)
        return 2
    except (APIError, PoolDataError, OSError) as error:
        print("错误: {}".format(error), file=stderr)
        return 1

    print(format_terminal_report(snapshot, summary), file=stdout)
    print("", file=stdout)
    print("CSV 明细: {}".format(csv_path.resolve()), file=stdout)
    print("JSON 快照: {}".format(json_path.resolve()), file=stdout)
    return 0 if snapshot.complete else 3


if __name__ == "__main__":
    raise SystemExit(main())
