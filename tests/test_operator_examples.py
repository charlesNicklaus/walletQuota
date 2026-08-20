from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class OperatorExampleTests(TestCase):
    def test_penguinpay_configuration_and_read_only_contract_are_documented(self):
        env_lines = set(
            (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        expected_env_lines = {
            "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay",
            "PENGUINPAY_PHONE=",
            "PENGUINPAY_PASSWORD=",
            "PENGUINPAY_API_BASE=https://api.penguinpay-app.com/app",
            "PENGUINPAY_SORT_TYPE=1",
            "PENGUINPAY_ORDER_TYPE=1",
            "PENGUINPAY_PAGE_SIZE=10",
            "PENGUINPAY_REQUEST_DELAY_MS=150",
            "PENGUINPAY_TIMEOUT_SECONDS=30",
        }
        self.assertTrue(expected_env_lines.issubset(env_lines))

        for fact in (
            "penguinpay:",
            "https://api.penguinpay-app.com/app",
            "https://app-web.penguinpay-app.com",
            "sortType=1",
            "orderType=1",
            "pageSize=10",
            "penguinpay_orders.csv",
            "channels.penguinpay.accounts",
            "账户: 0",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, readme)

        for path in (
            "/auth/login",
            "/pay/debit/task",
            "/user/upi/bind/list",
            "/bank/debit/card/list",
            "/pay/my/task",
            "/pay/order/detail",
        ):
            with self.subTest(path=path):
                self.assertIn("POST {}".format(path), readme)

        self.assertIn("永不调用", readme)
        for forbidden in (
            "/pay/receive/task",
            "/pay/submit/utr",
            "/pay/cancel/task",
            "/user/account/wallet",
            "/pay/debit/upis",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, readme)
