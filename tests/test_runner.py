import threading
from decimal import Decimal
from unittest import TestCase

from walletquota.models import Order, ProviderResult
from walletquota.runner import run_providers


class StubProvider:
    def __init__(self, name, operation):
        self._name = name
        self.operation = operation

    @property
    def name(self):
        return self._name

    def run(self):
        return self.operation()


class RunnerTests(TestCase):
    def test_runs_providers_concurrently_and_preserves_input_order(self):
        barrier = threading.Barrier(2, timeout=1)

        def operation(name):
            barrier.wait()
            return ProviderResult.success(
                name, (Order(name, "A1", Decimal("10")),), {}
            )

        providers = (
            StubProvider("slowpay", lambda: operation("slowpay")),
            StubProvider("fastpay", lambda: operation("fastpay")),
        )

        results = run_providers(providers)

        self.assertEqual([result.provider for result in results], ["slowpay", "fastpay"])
        self.assertTrue(all(result.status == "success" for result in results))

    def test_unexpected_exception_is_isolated_and_redacted(self):
        def explode():
            raise RuntimeError("secret raw body")

        providers = (
            StubProvider("badpay", explode),
            StubProvider(
                "goodpay", lambda: ProviderResult.success("goodpay", (), {})
            ),
        )

        results = run_providers(providers)

        self.assertEqual(results[0].status, "error")
        self.assertEqual(results[0].error.category, "internal")
        self.assertEqual(results[0].error.message, "Unexpected provider failure")
        self.assertNotIn("secret raw body", results[0].error.message)
        self.assertEqual(results[1].status, "success")

    def test_mismatched_result_name_is_internal_failure(self):
        provider = StubProvider(
            "atgpay", lambda: ProviderResult.success("wrongpay", (), {})
        )

        result = run_providers((provider,))[0]

        self.assertEqual(result.status, "error")
        self.assertEqual(result.provider, "atgpay")

    def test_rejects_invalid_worker_limits(self):
        for value in (0, 9):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    run_providers((), max_workers=value)
