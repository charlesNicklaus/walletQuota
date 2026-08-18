from typing import Any

from walletquota.providers.signed_pool.accounts import AccountSnapshot
from walletquota.providers.signed_pool.accounts import collect_accounts as _collect_accounts

from .spec import ATGPAY_SPEC


def collect_accounts(client: Any, task_page_size: int = 100) -> AccountSnapshot:
    return _collect_accounts(client, ATGPAY_SPEC, task_page_size=task_page_size)


__all__ = ["AccountSnapshot", "collect_accounts"]
