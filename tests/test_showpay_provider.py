from dataclasses import replace
from unittest import TestCase, mock

from walletquota.providers.showpay.config import ConfigError, ShowPaySettings
from walletquota.providers.showpay.provider import ShowPayProvider
from walletquota.providers.showpay.spec import SHOWPAY_SPEC
from walletquota.providers.signed_pool.client import UrllibTransport


VALUES = {"SHOWPAY_PHONE": "fixture-phone", "SHOWPAY_PASSWORD": "fixture-password"}


class ShowPayTests(TestCase):
    def test_only_sends_showpay_credentials_to_its_trusted_api(self):
        foreign = "https://api.sharkpay-app.com/app"
        with mock.patch.object(UrllibTransport, "post") as post:
            for values in (
                {"SHARKPAY_PHONE": "another-phone", "SHARKPAY_PASSWORD": "another-password"},
                {**VALUES, "SHOWPAY_API_BASE": foreign},
            ):
                with self.subTest(keys=list(values)):
                    self.assertEqual(ShowPayProvider(values).run().error.category, "configuration")
            post.assert_not_called()
        with self.assertRaises(ValueError):
            replace(SHOWPAY_SPEC, web_origin="https://app-web.sharkpay-app.com")
        with self.assertRaises(ValueError):
            SHOWPAY_SPEC.url_for(SHOWPAY_SPEC.api_base, "/pay/receive/task")

    def test_defaults_match_the_task_tab_and_pool_type_can_be_selected(self):
        settings = ShowPaySettings.from_values(VALUES)
        self.assertEqual((settings.sort_type, settings.order_type, settings.page_size), (1, 1, 10))
        self.assertEqual(ShowPaySettings.from_values({**VALUES, "SHOWPAY_ORDER_TYPE": "3"}).order_type, 3)
        self.assertNotIn(VALUES["SHOWPAY_PASSWORD"], repr(settings))
        with self.assertRaises(ConfigError):
            ShowPaySettings.from_values({**VALUES, "SHOWPAY_PAGE_SIZE": "0"})

    def test_duplicate_on_second_page_is_partial_and_retains_only_unique_orders(self):
        row = {"orderNo": "showpay-1", "orderAmount": "120.50"}
        responses = [
            {"userId": 9, "sessionKey": "fixture-key", "loginToken": "fixture-token"},
            {"total": 2, "list": [row]},
            {"total": 2, "list": [row]},
            {"total": 2, "list": []},
            {"bindUpiList": []}, {"list": []}, {"total": 0, "list": []},
        ]
        with mock.patch.object(
            UrllibTransport, "post", side_effect=[{"code": 200, "data": data} for data in responses]
        ) as post:
            result = ShowPayProvider({**VALUES, "SHOWPAY_PAGE_SIZE": "1", "SHOWPAY_REQUEST_DELAY_MS": "0"}).run()
        self.assertEqual(result.status, "partial")
        self.assertEqual([order.order_id for order in result.orders], ["showpay-1"])
        self.assertEqual(result.metadata["duplicate_count"], 1)
        self.assertEqual([c.args[1]["page"] for c in post.call_args_list[1:4]], [1, 2, 3])
