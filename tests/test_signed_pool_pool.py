from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from walletquota.providers.signed_pool.pool import PoolDataError, collect_pool


class PageClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_pool_page(self, page, size, sort_type, order_type):
        self.calls.append((page, size, sort_type, order_type))
        return self.pages.pop(0)


def settings():
    return SimpleNamespace(
        page_size=1,
        sort_type=1,
        order_type=1,
        request_delay_ms=0,
    )


class SignedPoolCollectionTests(TestCase):
    def test_collects_miller_pages_with_optional_fee_and_vip_absent(self):
        client = PageClient(
            [
                {
                    "total": 2,
                    "list": [
                        {
                            "orderNo": "M-1",
                            "orderAmount": 522,
                            "income": 18.66,
                            "balance": 540.66,
                            "inrRechargeRate": 3,
                        }
                    ],
                },
                {
                    "total": 2,
                    "list": [{"orderNo": "M-2", "orderAmount": "10000"}],
                },
            ]
        )
        snapshot = collect_pool(client, settings(), sleep=lambda unused: None)
        self.assertEqual(client.calls, [(1, 1, 1, 1), (2, 1, 1, 1)])
        self.assertEqual([order.order_no for order in snapshot.orders], ["M-1", "M-2"])
        self.assertEqual(snapshot.orders[1].order_amount, Decimal("10000"))
        self.assertIsNone(snapshot.orders[0].inr_recharge_fee)
        self.assertIsNone(snapshot.orders[0].vip)
        self.assertTrue(snapshot.complete)

    def test_rejects_negative_and_non_finite_amounts(self):
        for amount in ("-1", "NaN", "Infinity"):
            with self.subTest(amount=amount):
                client = PageClient(
                    [{"total": 1, "list": [{"orderNo": "M-1", "orderAmount": amount}]}]
                )
                with self.assertRaisesRegex(PoolDataError, "orderAmount"):
                    collect_pool(client, settings(), sleep=lambda unused: None)
