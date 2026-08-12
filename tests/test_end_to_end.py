import json
import unittest
from pathlib import Path

from atg_pool.config import Settings
from atg_pool.pool import collect_pool
from atg_pool.report import build_summary


class FixtureClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = 0

    def fetch_pool_page(self, page, size, sort_type, order_type):
        self.calls += 1
        if self.calls == 1:
            return self.fixture["data"]
        return {
            "total": self.fixture["data"]["total"],
            "list": [],
            "inrBuyConfig": "",
            "inrTodayBuyCount": 0,
        }


class ConfirmedResponseContractTests(unittest.TestCase):
    def test_confirmed_299_order_response_is_interpreted_correctly(self):
        fixture_path = Path(__file__).parent / "fixtures" / "pool_page_1.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        client = FixtureClient(fixture)
        settings = Settings(
            phone="9876543210",
            password="secret",
            page_size=10,
            request_delay_ms=0,
        )

        snapshot = collect_pool(client, settings, sleep=lambda _: None)
        summary = build_summary(snapshot)

        self.assertEqual(snapshot.latest_total, 299)
        self.assertEqual(summary["collected_count"], 10)
        self.assertFalse(snapshot.complete)
        self.assertEqual(
            [(str(item["amount"]), item["count"]) for item in summary["amount_distribution"]],
            [("522", 8), ("530", 1), ("534", 1)],
        )


if __name__ == "__main__":
    unittest.main()
