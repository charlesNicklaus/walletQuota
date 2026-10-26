import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import TestCase, mock
from urllib.parse import urlsplit

import wallet_quota_pipeline as pipeline
from walletquota.config import AppSettings
from walletquota.providers.signed_pool.client import UrllibTransport, make_signature
from walletquota.providers.signed_pool.spec import READ_ONLY_PATHS
from walletquota.registry import UnavailableProvider


CHANNEL_HOSTS = {
    "atgpay": ("api.atg-game.com", "app-web.atg-game.com"),
    "millerpay": ("api.millerpay-app.com", "app-web.millerpay-app.com"),
    "penguinpay": ("api.penguinpay-app.com", "app-web.penguinpay-app.com"),
    "sharkpay": ("api.sharkpay-app.com", "app-web.sharkpay-app.com"),
    "smartwallet": ("api.smartwallet-app.com", "app-web.smartwallet-app.com"),
    "hoyopay": ("api.hoyopay-app.com", "app-web.hoyopay-app.com"),
}
AMOUNTS = {
    name: Decimal(index) * Decimal("10.01")
    for index, name in enumerate(CHANNEL_HOSTS, 1)
}
PASSWORD = "NEW_CHANNELS_PASSWORD_FIXTURE"
PHONE = "9000000001"
TOKEN = "NEW_CHANNELS_TOKEN_FIXTURE"
SESSION = "NEW_CHANNELS_SESSION_FIXTURE"


class NewChannelsPipelineTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reports = self.root / "reports"
        self.env_file = self.root / "channels.env"
        lines = [
            "ENABLED_PROVIDERS=atgpay,millerpay,penguinpay,sharkpay,smartwallet",
            "PGDATABASE=fixture_walletquota",
        ]
        for name in CHANNEL_HOSTS:
            prefix = name.upper()
            lines.extend((
                prefix + "_PHONE=" + PHONE,
                prefix + "_PASSWORD=" + PASSWORD,
                prefix + "_REQUEST_DELAY_MS=0",
            ))
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_capture(self, failed_channel=None):
        settings = AppSettings.load(self.env_file, output_dir=self.reports)
        providers = pipeline.build_cycle_providers(settings, include_hoyo=True)
        self.assertEqual([provider.name for provider in providers], list(CHANNEL_HOSTS))
        self.assertFalse(any(isinstance(provider, UnavailableProvider) for provider in providers))
        calls = []
        by_host = {hosts[0]: name for name, hosts in CHANNEL_HOSTS.items()}

        def fake_post(transport, url, payload, headers, timeout):
            parsed = urlsplit(url)
            self.assertEqual(parsed.scheme, "https")
            self.assertIn(parsed.netloc, by_host)
            name = by_host[parsed.netloc]
            self.assertTrue(parsed.path.startswith("/app/"))
            path = parsed.path[len("/app"):]
            self.assertIn(path, READ_ONLY_PATHS)
            self.assertEqual(headers["Origin"], "https://" + CHANNEL_HOSTS[name][1])
            calls.append((name, path, dict(payload)))
            if path == "/auth/login":
                self.assertEqual(payload["password"], PASSWORD)
                if name == failed_channel:
                    return {"code": 1001, "message": PASSWORD + TOKEN + SESSION}
                return {"code": 200, "data": {
                    "userId": list(CHANNEL_HOSTS).index(name) + 1,
                    "loginToken": TOKEN,
                    "sessionKey": SESSION + name,
                }}
            self.assertEqual(
                headers["Signature"], make_signature(SESSION + name, payload)
            )
            if path == "/pay/debit/task":
                data = {"total": 1, "list": [{
                    "orderNo": "shared-order-id",
                    "orderAmount": str(AMOUNTS[name]),
                    "income": "0.01",
                    "balance": str(AMOUNTS[name] + Decimal("0.01")),
                }]}
            elif path == "/user/upi/bind/list":
                data = {"bindUpiList": []}
            elif path == "/bank/debit/card/list":
                data = {"list": []}
            elif path == "/pay/my/task":
                data = {"total": 0, "list": []}
            else:
                self.fail("Unexpected order detail request for an empty claimed pool")
            return {"code": 200, "data": data}

        stdout, stderr = io.StringIO(), io.StringIO()
        moment = datetime(2026, 10, 26, 18, 30, 45, tzinfo=timezone.utc)
        with mock.patch.object(UrllibTransport, "post", new=fake_post), \
                mock.patch.object(pipeline, "datetime", SimpleNamespace(now=lambda zone: moment)), \
                mock.patch.object(pipeline.subprocess, "run", return_value=
                                  subprocess.CompletedProcess([], 0, TOKEN, SESSION)) as psql, \
                mock.patch("socket.create_connection", side_effect=AssertionError("Unexpected network")):
            code = pipeline.main([
                "--env-file", str(self.env_file), "--output-dir", str(self.reports),
                "--include-hoyo", "--verbose",
            ], stdout, stderr)

        bundle = self.reports / "20261027_023045"
        aggregate = bundle / "aggregate_20261027_023045.json"
        sql = bundle / "postgres_20261027_023045.data.sql"
        self.assertTrue(aggregate.is_file())
        self.assertTrue(sql.is_file())
        self.assertTrue((bundle / "postgres_20261027_023045.imported").is_file())
        self.assertFalse((bundle / "postgres_20261027_023045.pending").exists())
        psql.assert_called_once()
        self.assertEqual(psql.call_args.args[0][-1], str(sql.resolve()))
        logs = stdout.getvalue() + stderr.getvalue()
        saved = "".join(path.read_text() for path in bundle.iterdir())
        for secret in (PHONE, PASSWORD, TOKEN, SESSION):
            self.assertNotIn(secret, logs + saved)
        for path in bundle.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        return code, json.loads(aggregate.read_text()), sql.read_text(), bundle, calls

    def test_six_real_providers_keep_hosts_orders_and_sql_separate(self):
        code, report, sql, bundle, calls = self.run_capture()

        self.assertEqual(code, 0)
        self.assertEqual(list(report["channels"]), list(CHANNEL_HOSTS))
        self.assertEqual(report["total"]["order_count"], 6)
        self.assertEqual(report["total"]["order_amount"], "210.21")
        for name in CHANNEL_HOSTS:
            channel = report["channels"][name]
            self.assertEqual(channel["status"], "success")
            self.assertEqual(channel["orders"][0]["provider"], name)
            self.assertEqual(channel["orders"][0]["order_id"], "shared-order-id")
            self.assertEqual(Decimal(channel["orders"][0]["amount"]), AMOUNTS[name])
            self.assertTrue((bundle / (name + "_orders_20261027_023045.csv")).is_file())
            self.assertIn("E'{}', E'shared-order-id'".format(name), sql)
            self.assertEqual(sum(1 for provider, path, _ in calls
                                 if provider == name and path == "/auth/login"), 1)
            self.assertTrue(any(provider == name and path == "/pay/debit/task"
                                for provider, path, _ in calls))

    def test_shark_login_failure_does_not_remove_smartwallet_or_other_channels(self):
        code, report, sql, bundle, calls = self.run_capture(failed_channel="sharkpay")

        self.assertEqual(code, 3)
        self.assertEqual(report["total"]["order_count"], 5)
        self.assertEqual(report["total"]["order_amount"], "170.17")
        self.assertEqual(report["channels"]["sharkpay"]["status"], "error")
        self.assertEqual(report["channels"]["sharkpay"]["error"]["category"], "authentication")
        self.assertEqual(report["channels"]["smartwallet"]["status"], "success")
        self.assertIn("E'smartwallet', E'shared-order-id'", sql)
        self.assertNotIn("E'sharkpay', E'shared-order-id'", sql)
        self.assertFalse((bundle / "sharkpay_orders_20261027_023045.csv").exists())
        self.assertFalse(any(provider == "sharkpay" and path != "/auth/login"
                             for provider, path, _ in calls))
