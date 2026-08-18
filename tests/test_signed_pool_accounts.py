from unittest import TestCase

from walletquota.providers.atgpay.spec import ATGPAY_SPEC
from walletquota.providers.signed_pool.client import AuthenticationError
from walletquota.providers.signed_pool.spec import ChannelSpec, READ_ONLY_PATHS


MILLER_SPEC = ChannelSpec(
    name="millerpay",
    display_name="MillerPay",
    api_label="MillerPay",
    api_base="https://api.millerpay-app.com/app",
    web_origin="https://app-web.millerpay-app.com",
    user_agent="WalletQuota/millerpay/1.0",
    default_sort_type=1,
    default_order_type=1,
    allowed_paths=READ_ONLY_PATHS,
)


class FakeClient:
    def __init__(self, bound=None, cards=None, task_pages=None, details=None):
        self.bound = {
            "bindUpiList": [
                {
                    "account": "Alice",
                    "upi": "alice@upi",
                    "sessionKey": "must-not-leak",
                }
            ]
        } if bound is None else bound
        self.cards = {"list": [{"cardNumber": "123456789012", "accName": "Alice"}]} if cards is None else cards
        self.task_pages = {
            1: {"total": 1, "list": [{"orderNo": "ORDER-1"}]}
        } if task_pages is None else task_pages
        self.details = {
            "ORDER-1": {"toUpi": "payee@upi", "cardNumber": "999900001111"}
        } if details is None else details

    def fetch_bound_upis(self, account_type=1):
        if isinstance(self.bound, Exception):
            raise self.bound
        return self.bound

    def fetch_bank_cards(self):
        if isinstance(self.cards, Exception):
            raise self.cards
        return self.cards

    def fetch_my_tasks_page(self, page, size):
        value = self.task_pages[page]
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_order_detail(self, order_no):
        value = self.details[order_no]
        if isinstance(value, Exception):
            raise value
        return value


class SignedPoolAccountCollectionTests(TestCase):
    def test_collects_accounts_with_the_spec_provider_identity(self):
        from walletquota.providers.signed_pool.accounts import collect_accounts

        snapshot = collect_accounts(FakeClient(), MILLER_SPEC, task_page_size=1)

        self.assertEqual({account.provider for account in snapshot.accounts}, {"millerpay"})
        self.assertEqual(
            [account.kind for account in snapshot.accounts],
            ["bound_upi", "bank_account", "order_payee_upi", "order_payee_bank"],
        )
        self.assertNotIn("must-not-leak", repr(snapshot))

    def test_derived_ids_are_isolated_by_provider(self):
        from walletquota.providers.signed_pool.accounts import collect_accounts

        miller = collect_accounts(FakeClient(), MILLER_SPEC)
        atg = collect_accounts(FakeClient(), ATGPAY_SPEC)

        self.assertNotEqual(miller.accounts[0].account_id, atg.accounts[0].account_id)

    def test_authentication_errors_propagate_from_every_account_source(self):
        from walletquota.providers.signed_pool.accounts import collect_accounts

        cases = (
            ("bound UPI", FakeClient(bound=AuthenticationError("expired"))),
            ("bank cards", FakeClient(cards=AuthenticationError("expired"))),
            (
                "claimed tasks",
                FakeClient(task_pages={1: AuthenticationError("expired")}),
            ),
            (
                "order details",
                FakeClient(details={"ORDER-1": AuthenticationError("expired")}),
            ),
        )

        for source, client in cases:
            with self.subTest(source=source):
                with self.assertRaises(AuthenticationError):
                    collect_accounts(client, MILLER_SPEC)

    def test_ordinary_enrichment_failures_return_only_fixed_safe_warnings(self):
        from walletquota.providers.signed_pool.accounts import collect_accounts

        snapshot = collect_accounts(
            FakeClient(
                bound=RuntimeError("bound raw-secret"),
                cards=RuntimeError("card raw-secret"),
                task_pages={1: RuntimeError("task raw-secret")},
            ),
            MILLER_SPEC,
        )

        self.assertEqual(
            snapshot.warnings,
            (
                "MillerPay bound UPI account collection failed",
                "MillerPay bank card account collection failed",
                "MillerPay claimed task collection failed",
            ),
        )
        self.assertNotIn("raw-secret", repr(snapshot))
