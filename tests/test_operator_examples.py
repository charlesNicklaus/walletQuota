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
            "penguinpay_orders_<timestamp>.csv",
            "channels.penguinpay.accounts",
            "账户: 0",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact, readme)

        self.assertNotIn(
            "它也不表示当前登录未返回已绑定账户或已领取订单账户", readme
        )

        penguin_allowlist = readme.partition(
            "PenguinPay 只允许调用：\n\n```text\n"
        )[2].partition("\n```")[0]
        self.assertEqual(
            [
                line
                for line in penguin_allowlist.splitlines()
                if line.startswith("POST ")
            ],
            [
                "POST /auth/login",
                "POST /pay/debit/task",
                "POST /user/upi/bind/list",
                "POST /bank/debit/card/list",
                "POST /pay/my/task",
                "POST /pay/order/detail",
            ],
        )

        penguin_safety = readme.partition("PenguinPay 永不调用 ")[2].partition(
            "\n\n"
        )[0]
        for forbidden in (
            "/pay/receive/task",
            "/pay/submit/utr",
            "/pay/cancel/task",
            "/user/account/wallet",
            "/pay/debit/upis",
            "付款、绑定、解绑、删除、更新或其他变更端点",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, penguin_safety)
