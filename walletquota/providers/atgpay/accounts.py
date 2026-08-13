import hashlib
from dataclasses import dataclass
from typing import Any, List, Mapping, Sequence, Tuple

from walletquota.models import Account


_DERIVED_ID_LENGTH = 16
_MAX_CLAIMED_TASK_PAGES = 1000
_DEFAULT_TASK_PAGE_SIZE = 100


@dataclass(frozen=True)
class AccountSnapshot:
    accounts: Tuple[Account, ...]
    warnings: Tuple[str, ...]
    bound_upi_total: int
    bank_card_total: int
    claimed_task_total: int

    @classmethod
    def empty(cls) -> "AccountSnapshot":
        return cls((), (), 0, 0, 0)


def _text(value: Any) -> str:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return ""
    return str(value).strip()


def _safe_attribute(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _warn_once(warnings: List[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _server_or_derived_id(
    value: Mapping[str, Any], kind: str, account: str = "", upi: str = "", order_id: str = ""
) -> str:
    server_id = _text(value.get("id"))
    if server_id:
        return server_id
    canonical = "\x1f".join(("atgpay", kind, account, upi, order_id))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_DERIVED_ID_LENGTH]


def _rows(data: Any, key: str) -> Sequence[Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        raise ValueError("ATG account response is invalid")
    rows = data.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("ATG account response is invalid")
    return rows


def _total(data: Any) -> int:
    if not isinstance(data, Mapping):
        raise ValueError("ATG claimed task response is invalid")
    try:
        total = int(data.get("total"))
    except (TypeError, ValueError) as error:
        raise ValueError("ATG claimed task response is invalid") from error
    if total < 0:
        raise ValueError("ATG claimed task response is invalid")
    return total


def _bound_upi_accounts(data: Any) -> Tuple[List[Account], int]:
    rows = _rows(data, "bindUpiList")
    accounts = []
    for row in rows:
        upi = _text(row.get("upi"))
        if not upi:
            continue
        account = _text(row.get("account"))
        attributes = {
            name: _safe_attribute(row[name])
            for name in ("type", "sale", "status")
            if name in row and _safe_attribute(row[name]) is not None
        }
        accounts.append(
            Account(
                "atgpay",
                "bound_upi",
                _server_or_derived_id(row, "bound_upi", account, upi),
                account=account or None,
                upi=upi,
                attributes=attributes,
            )
        )
    return accounts, len(rows)


def _bank_card_accounts(data: Any) -> Tuple[List[Account], int]:
    rows = _rows(data, "list")
    accounts = []
    for row in rows:
        number = _text(row.get("cardNumber"))
        if not number:
            continue
        account_name = _text(row.get("accName"))
        attributes = {"account_name": account_name} if account_name else {}
        accounts.append(
            Account(
                "atgpay",
                "bank_account",
                _server_or_derived_id(row, "bank_account", number),
                account=number,
                bank_name=_text(row.get("bankName")) or None,
                ifsc=_text(row.get("ifsc")) or None,
                attributes=attributes,
            )
        )
    return accounts, len(rows)


def _order_detail_accounts(order_id: str, data: Any) -> List[Account]:
    if not isinstance(data, Mapping):
        raise ValueError("ATG order detail response is invalid")
    upi = _text(data.get("toUpi"))
    number = _text(data.get("cardNumber"))
    account_name = _text(data.get("accountName"))
    attributes = {"account_name": account_name} if account_name else {}
    accounts = []
    if upi:
        accounts.append(
            Account(
                "atgpay",
                "order_payee_upi",
                _server_or_derived_id(
                    {}, "order_payee_upi", upi=upi, order_id=order_id
                ),
                upi=upi,
                order_id=order_id,
                attributes=attributes,
            )
        )
    if number:
        accounts.append(
            Account(
                "atgpay",
                "order_payee_bank",
                _server_or_derived_id(
                    {}, "order_payee_bank", account=number, order_id=order_id
                ),
                account=number,
                bank_name=_text(data.get("bankName")) or None,
                ifsc=_text(data.get("ifsc")) or None,
                order_id=order_id,
                attributes=attributes,
            )
        )
    return accounts


def _deduplicate(accounts: Sequence[Account]) -> Tuple[Account, ...]:
    seen = set()
    result = []
    for account in accounts:
        key = (account.kind, account.account_id, account.order_id)
        if key not in seen:
            seen.add(key)
            result.append(account)
    return tuple(result)


def _claimed_order_ids(client: Any, page_size: int, warnings: List[str]) -> Tuple[List[str], int]:
    order_ids = []
    expected_total = None
    for page in range(1, _MAX_CLAIMED_TASK_PAGES + 1):
        try:
            data = client.fetch_my_tasks_page(page, page_size)
            total = _total(data)
            rows = _rows(data, "list")
        except Exception:
            _warn_once(warnings, "ATG claimed task collection failed")
            break
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            _warn_once(warnings, "ATG claimed task pagination is inconsistent")
            break
        page_order_ids = []
        for row in rows:
            order_id = _text(row.get("orderNo"))
            if order_id:
                page_order_ids.append(order_id)
        if (
            len(page_order_ids) != len(rows)
            or len(set(page_order_ids)) != len(page_order_ids)
            or any(order_id in order_ids for order_id in page_order_ids)
            or len(order_ids) + len(page_order_ids) > total
        ):
            for order_id in page_order_ids:
                if order_id not in order_ids:
                    order_ids.append(order_id)
            _warn_once(warnings, "ATG claimed task pagination is inconsistent")
            break
        order_ids.extend(page_order_ids)
        if len(order_ids) == total:
            break
        if not rows:
            _warn_once(warnings, "ATG claimed task pagination is inconsistent")
            break
    else:
        _warn_once(warnings, "ATG claimed task page limit reached")
    return order_ids, expected_total if expected_total is not None else 0


def collect_accounts(client: Any, task_page_size: int = _DEFAULT_TASK_PAGE_SIZE) -> AccountSnapshot:
    if isinstance(task_page_size, bool) or not isinstance(task_page_size, int) or not 1 <= task_page_size <= 1000:
        raise ValueError("task page size must be between 1 and 1000")
    warnings: List[str] = []
    collected: List[Account] = []
    bound_upi_total = 0
    bank_card_total = 0

    try:
        bound_accounts, bound_upi_total = _bound_upi_accounts(client.fetch_bound_upis(1))
        collected.extend(bound_accounts)
    except Exception:
        _warn_once(warnings, "ATG bound UPI account collection failed")
    try:
        bank_accounts, bank_card_total = _bank_card_accounts(client.fetch_bank_cards())
        collected.extend(bank_accounts)
    except Exception:
        _warn_once(warnings, "ATG bank card account collection failed")

    order_ids, claimed_task_total = _claimed_order_ids(client, task_page_size, warnings)
    seen_order_ids = set()
    for order_id in order_ids:
        if order_id in seen_order_ids:
            continue
        seen_order_ids.add(order_id)
        try:
            collected.extend(_order_detail_accounts(order_id, client.fetch_order_detail(order_id)))
        except Exception:
            _warn_once(warnings, "ATG claimed order detail collection failed")

    return AccountSnapshot(
        _deduplicate(collected),
        tuple(warnings),
        bound_upi_total,
        bank_card_total,
        claimed_task_total,
    )
