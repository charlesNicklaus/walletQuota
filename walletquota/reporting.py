import csv
import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from .models import Account, AggregateResult, Order, ProviderResult
from .summary import summarize_orders


CSV_FIELDS = [
    "provider",
    "order_id",
    "amount",
    "currency",
    "income",
    "balance",
    "attributes",
]

_CREDENTIAL_ATTRIBUTE_NAMES = frozenset(
    {
        "password",
        "token",
        "cookie",
        "session",
        "signature",
        "authorization",
        "authheader",
        "proxyauthorization",
        "xauthorization",
        "apikey",
        "credential",
        "secret",
        "sign",
    }
)
_CREDENTIAL_ATTRIBUTE_AFFIXES = (
    "password",
    "token",
    "cookie",
    "session",
    "signature",
    "secret",
)
_RAW_AUTH_RESPONSE_NAMES = frozenset(
    {"rawauthresponse", "authresponse", "loginresponse"}
)


def _decimal_text(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _distribution_lines(summary: Mapping[str, Any]):
    rows = summary["amount_distribution"]
    return [
        "    范围 {} × {}（{}%，小计 ₹{}）".format(
            row["label"],
            row["count"],
            _decimal_text(row["percentage"]),
            _decimal_text(row["subtotal"]),
        )
        for row in rows
    ]


def _mask_number(value: str) -> str:
    return "****{}".format(value[-4:]) if len(value) > 4 else "*" * len(value)


def _mask_upi(value: str) -> str:
    local, separator, handle = value.partition("@")
    if not separator:
        return _mask_number(value)

    def mask_part(part: str) -> str:
        if len(part) < 3:
            return "*" * len(part)
        return "{}***{}".format(part[0], part[-1])

    return "{}@{}".format(mask_part(local), mask_part(handle))


def _account_line(account: Account) -> str:
    if account.kind == "bound_upi":
        return "    收款 UPI: {}".format(_mask_upi(account.upi))
    if account.kind == "bank_account":
        line = "    银行账户: {}".format(_mask_number(account.account))
        if account.bank_name:
            line += " / {}".format(account.bank_name)
        if account.ifsc:
            line += " / IFSC {}".format(_mask_number(account.ifsc))
        return line

    value = _mask_upi(account.upi) if account.upi else _mask_number(account.account)
    return "    订单收款方: {} / {}".format(account.order_id or "—", value)


def _account_lines(accounts):
    if not accounts:
        return ["  账户: 0（当前账号未返回已绑定或已领取订单账户）"]
    return ["  账户: {}".format(len(accounts))] + [
        _account_line(account) for account in accounts
    ]


def format_terminal_report(result: AggregateResult) -> str:
    total = result.total
    lines = [
        "订单池综合报告",
        "=" * 58,
        "状态: {}".format(result.status),
        "total:",
        "  渠道: 成功 {} / 部分 {} / 失败 {}".format(
            total["channel_success"],
            total["channel_partial"],
            total["channel_error"],
        ),
        "  订单数: {}".format(total["order_count"]),
        "  总金额: ₹{}".format(_decimal_text(total["order_amount"])),
        "  金额分布:",
    ]
    lines.extend(_distribution_lines(total))

    for channel in result.channels:
        lines.extend(["", "{}: {}".format(channel.provider, channel.status)])
        if channel.status == "error":
            lines.append(
                "  错误: {} / {}".format(
                    channel.error.category, channel.error.message
                )
            )
            continue
        summary = summarize_orders(channel.orders)
        count_suffix = "" if channel.status == "success" else "（不计入 total）"
        lines.extend(
            [
                "  订单数: {}{}".format(summary["order_count"], count_suffix),
                "  总金额: ₹{}".format(_decimal_text(summary["order_amount"])),
                "  金额分布:",
            ]
        )
        lines.extend(_distribution_lines(summary))
        lines.extend(_account_lines(channel.accounts))
        if channel.status == "partial":
            lines.append("  警告: {}".format(channel.error.message))
    return "\n".join(lines)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def _safe_report_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_report_mapping(item)
            for key, item in value.items()
            if not _is_credential_attribute(key)
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_report_mapping(item) for item in value]
    return value


def _is_credential_attribute(key: Any) -> bool:
    normalized = str(key).replace("_", "").replace("-", "").lower()
    return (
        normalized in _CREDENTIAL_ATTRIBUTE_NAMES
        or normalized in _RAW_AUTH_RESPONSE_NAMES
        or normalized.startswith(("rawauth", "rawlogin"))
        or normalized.endswith(_CREDENTIAL_ATTRIBUTE_AFFIXES)
        or normalized.startswith(_CREDENTIAL_ATTRIBUTE_AFFIXES)
    )


def _account_dict(account: Account) -> Dict[str, Any]:
    return {
        "provider": account.provider,
        "kind": account.kind,
        "account_id": account.account_id,
        "account": account.account,
        "upi": account.upi,
        "bank_name": account.bank_name,
        "ifsc": account.ifsc,
        "order_id": account.order_id,
        "attributes": _json_value(_safe_report_mapping(account.attributes)),
    }


def _account_summary(accounts) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    for account in accounts:
        by_kind[account.kind] = by_kind.get(account.kind, 0) + 1
    return {"count": len(accounts), "by_kind": by_kind}


def _order_dict(order: Order) -> Dict[str, Any]:
    return {
        "provider": order.provider,
        "order_id": order.order_id,
        "amount": _decimal_text(order.amount),
        "currency": order.currency,
        "income": "" if order.income is None else _decimal_text(order.income),
        "balance": "" if order.balance is None else _decimal_text(order.balance),
        "attributes": _json_value(_safe_report_mapping(order.attributes)),
    }


def _channel_dict(result: ProviderResult) -> Dict[str, Any]:
    value = {
        "status": result.status,
        "complete": result.complete,
        "summary": _json_value(summarize_orders(result.orders)),
        "metadata": _json_value(_safe_report_mapping(result.metadata)),
        "orders": [_order_dict(order) for order in result.orders],
        "account_summary": _account_summary(result.accounts),
        "accounts": [_account_dict(account) for account in result.accounts],
    }
    if result.error is not None:
        value["error"] = {
            "category": result.error.category,
            "message": result.error.message,
        }
    return value


def _atomic_text(path: Path, writer: Any) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer(handle)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _reserve_directory(parent: Path, base_name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    suffix = 1
    while True:
        name = base_name if suffix == 1 else "{}_{}".format(base_name, suffix)
        candidate = parent / name
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            suffix += 1


@dataclass(frozen=True)
class ReportBundle:
    directory: Path
    aggregate_json: Path
    channel_csv: Mapping[str, Path]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "channel_csv", MappingProxyType(dict(self.channel_csv))
        )


def write_report_bundle(result: AggregateResult, directory: Path) -> ReportBundle:
    beijing_stamp = result.finished_at.astimezone(
        ZoneInfo("Asia/Shanghai")
    ).strftime("%Y%m%d_%H%M%S")
    bundle_directory = _reserve_directory(Path(directory), beijing_stamp)
    file_stamp = bundle_directory.name
    aggregate_json = bundle_directory / "aggregate_{}.json".format(file_stamp)
    channel_csv: Dict[str, Path] = {}

    payload = {
        "metadata": {
            "generator": "walletQuota",
            "schema_version": 1,
            "status": result.status,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
            "provider_count": len(result.channels),
        },
        "total": _json_value(result.total),
        "channels": {
            channel.provider: _channel_dict(channel) for channel in result.channels
        },
    }
    _atomic_text(
        aggregate_json,
        lambda handle: json.dump(
            payload, handle, ensure_ascii=False, indent=2, sort_keys=False
        ),
    )

    for channel in result.channels:
        if not channel.orders:
            continue
        path = bundle_directory / "{}_orders_{}.csv".format(
            channel.provider, file_stamp
        )

        def write_csv(handle: Any, orders=channel.orders) -> None:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for order in orders:
                row = _order_dict(order)
                row["attributes"] = json.dumps(
                    row["attributes"], ensure_ascii=False, sort_keys=True
                )
                writer.writerow(row)

        _atomic_text(path, write_csv)
        channel_csv[channel.provider] = path

    return ReportBundle(bundle_directory, aggregate_json, channel_csv)
