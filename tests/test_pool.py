import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atg_pool.config import Settings
from atg_pool.pool import PoolDataError, collect_pool


class PageClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def fetch_pool_page(self, page, size, sort_type, order_type):
        self.calls.append((page, size, sort_type, order_type))
        return self.pages.pop(0)


def settings(page_size=2, delay_ms=0):
    return Settings(
        phone="9876543210",
        password="secret",
        page_size=page_size,
        request_delay_ms=delay_ms,
        report_directory=Path("reports"),
    )


def row(order_no, amount, income="1.25", balance=None):
    return {
        "orderNo": order_no,
        "orderAmount": amount,
        "income": income,
        "balance": balance if balance is not None else str(Decimal(str(amount)) + Decimal(income)),
        "inrRechargeRate": 3,
        "inrRechargeFee": 3,
        "vip": 0,
    }


class PoolCollectionTests(unittest.TestCase):
    def test_collects_multiple_pages_and_normalizes_decimal_fields(self):
        client = PageClient(
            [
                {"total": 3, "list": [row("one", 522), row("two", "530.50")]},
                {"total": 3, "list": [row("three", 534)]},
            ]
        )
        now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)

        snapshot = collect_pool(
            client,
            settings(),
            sleep=lambda _: None,
            now=lambda: now,
        )

        self.assertEqual([order.order_no for order in snapshot.orders], ["one", "two", "three"])
        self.assertEqual(snapshot.orders[1].order_amount, Decimal("530.50"))
        self.assertEqual(snapshot.latest_total, 3)
        self.assertEqual(snapshot.pages_fetched, 2)
        self.assertTrue(snapshot.complete)
        self.assertTrue(snapshot.stable)
        self.assertEqual(client.calls, [(1, 2, 1, 4), (2, 2, 1, 4)])

    def test_duplicate_order_numbers_are_deduplicated_and_mark_incomplete(self):
        client = PageClient(
            [
                {"total": 3, "list": [row("one", 522), row("two", 522)]},
                {"total": 3, "list": [row("two", 522)]},
            ]
        )

        snapshot = collect_pool(client, settings(), sleep=lambda _: None)

        self.assertEqual(len(snapshot.orders), 2)
        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.duplicate_count, 1)

    def test_changing_total_is_recorded_as_unstable(self):
        client = PageClient(
            [
                {"total": 4, "list": [row("one", 500), row("two", 501)]},
                {"total": 3, "list": [row("three", 502)]},
            ]
        )

        snapshot = collect_pool(client, settings(), sleep=lambda _: None)

        self.assertEqual(snapshot.observed_totals, (4, 3))
        self.assertFalse(snapshot.stable)
        self.assertFalse(snapshot.complete)

    def test_empty_page_before_total_is_incomplete(self):
        client = PageClient([{"total": 10, "list": []}])

        snapshot = collect_pool(client, settings(), sleep=lambda _: None)

        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.stop_reason, "empty_page")

    def test_sleeps_only_between_pages(self):
        client = PageClient(
            [
                {"total": 3, "list": [row("one", 500), row("two", 501)]},
                {"total": 3, "list": [row("three", 502)]},
            ]
        )
        delays = []

        collect_pool(client, settings(delay_ms=250), sleep=delays.append)

        self.assertEqual(delays, [0.25])

    def test_malformed_order_is_rejected(self):
        client = PageClient([{"total": 1, "list": [{"orderAmount": 522}]}])

        with self.assertRaisesRegex(PoolDataError, "orderNo"):
            collect_pool(client, settings(), sleep=lambda _: None)

    def test_non_finite_money_is_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                client = PageClient(
                    [{"total": 1, "list": [row("one", value)]}]
                )

                with self.assertRaisesRegex(PoolDataError, "orderAmount"):
                    collect_pool(client, settings(), sleep=lambda _: None)

    def test_defensive_page_cap_marks_snapshot_incomplete(self):
        client = PageClient(
            [
                {"total": 100, "list": [row("one", 500)]},
                {"total": 100, "list": [row("two", 501)]},
            ]
        )

        snapshot = collect_pool(
            client, settings(page_size=1), sleep=lambda _: None, max_pages=2
        )

        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.stop_reason, "page_limit")
        self.assertEqual(snapshot.pages_fetched, 2)


if __name__ == "__main__":
    unittest.main()
