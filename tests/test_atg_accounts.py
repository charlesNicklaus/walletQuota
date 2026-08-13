from unittest import TestCase

from walletquota.providers.atgpay.accounts import collect_accounts


class FakeClient:
    def __init__(self, bound=None, cards=None, task_pages=None, details=None):
        self.bound = {} if bound is None else bound
        self.cards = {} if cards is None else cards
        self.task_pages = {} if task_pages is None else task_pages
        self.details = {} if details is None else details
        self.bound_account_types = []
        self.task_calls = []
        self.detail_calls = []

    def fetch_bound_upis(self, account_type=1):
        self.bound_account_types.append(account_type)
        if isinstance(self.bound, Exception):
            raise self.bound
        return self.bound

    def fetch_bank_cards(self):
        if isinstance(self.cards, Exception):
            raise self.cards
        return self.cards

    def fetch_my_tasks_page(self, page, size):
        self.task_calls.append((page, size))
        value = self.task_pages[page]
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_order_detail(self, order_no):
        self.detail_calls.append(order_no)
        value = self.details[order_no]
        if isinstance(value, Exception):
            raise value
        return value


class AccountCollectionTests(TestCase):
    def test_maps_allowlisted_account_fields_and_claimed_order_details(self):
        client = FakeClient(
            bound={
                "bindUpiList": [
                    {
                        "id": 11,
                        "account": "Alice",
                        "upi": "alice@paytm",
                        "type": 1,
                        "sale": True,
                        "status": 2,
                        "sessionKey": "must-not-be-kept",
                    }
                ]
            },
            cards={
                "list": [
                    {
                        "id": 12,
                        "cardNumber": "123456789012",
                        "accName": "Alice A",
                        "ifsc": "HDFC0000123",
                        "bankName": "HDFC",
                        "loginToken": "must-not-be-kept",
                    }
                ]
            },
            task_pages={
                1: {"total": 2, "list": [{"orderNo": "ORD-1"}]},
                2: {"total": 2, "list": [{"orderNo": "ORD-2"}]},
            },
            details={
                "ORD-1": {
                    "id": "detail-id-must-be-ignored",
                    "toUpi": "payee@upi",
                    "ignored": "secret",
                },
                "ORD-2": {
                    "cardNumber": "999900001111",
                    "bankName": "ICICI",
                    "ifsc": "ICIC0000123",
                    "accountName": "Payee B",
                    "ignored": "secret",
                },
            },
        )

        snapshot = collect_accounts(client, task_page_size=1)

        self.assertEqual(client.bound_account_types, [1])
        self.assertEqual(client.task_calls, [(1, 1), (2, 1)])
        self.assertEqual(client.detail_calls, ["ORD-1", "ORD-2"])
        self.assertEqual(snapshot.warnings, ())
        self.assertEqual(snapshot.bound_upi_total, 1)
        self.assertEqual(snapshot.bank_card_total, 1)
        self.assertEqual(snapshot.claimed_task_total, 2)
        self.assertEqual(
            [(account.kind, account.account_id) for account in snapshot.accounts],
            [
                ("bound_upi", "11"),
                ("bank_account", "12"),
                ("order_payee_upi", "c4248e44383234a8"),
                ("order_payee_bank", "d164bc3e8cf17411"),
            ],
        )
        bound, bank, payee_upi, payee_bank = snapshot.accounts
        self.assertEqual(bound.account, "Alice")
        self.assertEqual(bound.upi, "alice@paytm")
        self.assertEqual(bound.attributes, {"type": 1, "sale": True, "status": 2})
        self.assertEqual(bank.account, "123456789012")
        self.assertEqual(bank.bank_name, "HDFC")
        self.assertEqual(bank.ifsc, "HDFC0000123")
        self.assertEqual(bank.attributes, {"account_name": "Alice A"})
        self.assertEqual(payee_upi.upi, "payee@upi")
        self.assertEqual(payee_upi.order_id, "ORD-1")
        self.assertEqual(payee_bank.account, "999900001111")
        self.assertEqual(payee_bank.order_id, "ORD-2")
        self.assertEqual(payee_bank.attributes, {"account_name": "Payee B"})

    def test_rejects_nested_values_in_allowlisted_scalar_fields(self):
        client = FakeClient(
            bound={
                "bindUpiList": [
                    {"upi": {"sessionKey": "must-not-leak"}},
                    {"upi": "safe@upi", "status": {"token": "must-not-leak"}},
                ]
            },
            cards={"list": [{"cardNumber": {"password": "must-not-leak"}}]},
            task_pages={1: {"total": 1, "list": [{"orderNo": "ORD-1"}]}},
            details={"ORD-1": {"toUpi": {"cookie": "must-not-leak"}}},
        )

        snapshot = collect_accounts(client)

        self.assertEqual(len(snapshot.accounts), 1)
        self.assertEqual(snapshot.accounts[0].upi, "safe@upi")
        self.assertEqual(snapshot.accounts[0].attributes, {})
        self.assertNotIn("must-not-leak", repr(snapshot))

    def test_deduplicates_rows_and_uses_stable_derived_ids_when_server_ids_are_absent(self):
        client = FakeClient(
            bound={
                "bindUpiList": [
                    {"upi": "alice@paytm", "status": 1},
                    {"upi": "alice@paytm", "status": 2},
                ]
            },
            cards={
                "list": [
                    {"cardNumber": "123456789012", "bankName": "HDFC"},
                    {"cardNumber": "123456789012", "bankName": "HDFC"},
                ]
            },
            task_pages={1: {"total": 1, "list": [{"orderNo": "ORD-1"}]}},
            details={"ORD-1": {"toUpi": "payee@upi"}},
        )

        snapshot = collect_accounts(client)

        self.assertEqual(len(snapshot.accounts), 3)
        self.assertEqual(
            [account.account_id for account in snapshot.accounts],
            ["e09f25f2a9e79842", "f381e29bf32eb4e3", "c4248e44383234a8"],
        )
        self.assertEqual(snapshot.accounts[0].attributes, {"status": 1})

    def test_empty_responses_produce_an_empty_snapshot(self):
        client = FakeClient(
            bound={"bindUpiList": []},
            cards={"list": []},
            task_pages={1: {"total": 0, "list": []}},
        )

        snapshot = collect_accounts(client)

        self.assertEqual(snapshot.accounts, ())
        self.assertEqual(snapshot.warnings, ())
        self.assertEqual(
            (snapshot.bound_upi_total, snapshot.bank_card_total, snapshot.claimed_task_total),
            (0, 0, 0),
        )
        self.assertEqual(client.detail_calls, [])

    def test_contradictory_claimed_pagination_stops_with_a_safe_warning(self):
        client = FakeClient(
            bound={"bindUpiList": []},
            cards={"list": []},
            task_pages={
                1: {"total": 2, "list": [{"orderNo": "ORD-1"}]},
                2: {"total": 3, "list": [{"orderNo": "ORD-2"}]},
            },
            details={"ORD-1": {"toUpi": "payee@upi"}},
        )

        snapshot = collect_accounts(client, task_page_size=1)

        self.assertEqual(client.detail_calls, ["ORD-1"])
        self.assertEqual(snapshot.claimed_task_total, 2)
        self.assertEqual(snapshot.warnings, ("ATG claimed task pagination is inconsistent",))

    def test_duplicate_claimed_order_ids_are_reported_as_inconsistent(self):
        client = FakeClient(
            bound={"bindUpiList": []},
            cards={"list": []},
            task_pages={
                1: {
                    "total": 2,
                    "list": [{"orderNo": "ORD-1"}, {"orderNo": "ORD-1"}],
                }
            },
            details={"ORD-1": {"toUpi": "payee@upi"}},
        )

        snapshot = collect_accounts(client)

        self.assertEqual(client.detail_calls, ["ORD-1"])
        self.assertEqual(
            snapshot.warnings,
            ("ATG claimed task pagination is inconsistent",),
        )

    def test_repeated_detail_failures_add_one_warning(self):
        client = FakeClient(
            bound={"bindUpiList": []},
            cards={"list": []},
            task_pages={
                1: {
                    "total": 2,
                    "list": [{"orderNo": "ORD-1"}, {"orderNo": "ORD-2"}],
                }
            },
            details={
                "ORD-1": RuntimeError("first secret"),
                "ORD-2": RuntimeError("second secret"),
            },
        )

        snapshot = collect_accounts(client)

        self.assertEqual(
            snapshot.warnings,
            ("ATG claimed order detail collection failed",),
        )

    def test_enrichment_failures_return_only_fixed_safe_warnings(self):
        client = FakeClient(
            bound=RuntimeError("bound raw-secret-response"),
            cards=RuntimeError("card raw-secret-response"),
            task_pages={1: RuntimeError("task raw-secret-response")},
        )

        snapshot = collect_accounts(client)

        self.assertEqual(snapshot.accounts, ())
        self.assertEqual(
            snapshot.warnings,
            (
                "ATG bound UPI account collection failed",
                "ATG bank card account collection failed",
                "ATG claimed task collection failed",
            ),
        )
        self.assertNotIn("raw-secret-response", repr(snapshot))
