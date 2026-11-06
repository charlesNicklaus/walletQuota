import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import TestCase

from walletquota.config import read_env


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = r"private:fixture\password"

# Run the actual interactive helper in a fresh process. Every database call is
# intercepted, even if psql is installed on the machine running the tests.
HARNESS = """
import json, os, subprocess, sys
from pathlib import Path
from unittest import mock

root = Path(sys.argv[1])
responses = iter(json.loads(sys.argv[2]))
password = sys.stdin.read()
def fake_psql(command, **options):
    assert Path(command[0]).name == 'psql', command
    env = options['env']
    with (root / 'calls.jsonl').open('a') as handle:
        handle.write(json.dumps({'command': command, 'env': env,
                                 'dotenv': (root / '.env').read_text()}, default=str) + '\\n')
    reply = next(responses)
    stdout = json.dumps(reply) if isinstance(reply, list) else ''
    code = int(reply == 'fail')
    if code and options.get('check'):
        raise subprocess.CalledProcessError(code, command)
    return subprocess.CompletedProcess(command, code, stdout, '')

answers = ['db.example.invalid', '', 'sulu_db', 'pool_writer', '']
with mock.patch('builtins.input', side_effect=answers), \\
        mock.patch('getpass.getpass', return_value=password), \\
        mock.patch('subprocess.run', side_effect=fake_psql):
    from scripts.configure_sulu_db import configure
    try:
        configure(root)
    except (ValueError, RuntimeError, subprocess.CalledProcessError, OSError):
        sys.exit(3)
"""


class ConfigureSuluDatabaseTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.psql = self.root / "bin/psql"
        self.psql.parent.mkdir()
        self.psql.write_text("#!/bin/sh\nexit 99\n")
        self.psql.chmod(0o700)
        ddl = self.root / "sql/sulu_pay/payout_pool.sql"
        ddl.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "sql/sulu_pay/payout_pool.sql", ddl)
        self.dotenv = self.root / ".env"
        self.saved_settings = "# keep original comment\nPGDATABASE=local_wallet\nENABLED_PROVIDERS=showpay\n"
        self.old_service = self.root / "old.conf"
        self.old_passfile = self.root / "old.pgpass"
        self.old_service.write_text("[old]\ndbname=previous\n")
        self.old_passfile.write_text("oldhost:5432:previous:user:old-password\n")
        self.original = self.saved_settings + (
            f"SULU_PGSERVICE=old\nSULU_PGSERVICEFILE={self.old_service}\nSULU_PGPASSFILE={self.old_passfile}\n"
        )
        self.dotenv.write_text(self.original)
        self.dotenv.chmod(0o600)

    def invoke(self, responses):
        # Keep both the project configuration and HOME under test control. Any
        # terminal credential or secret in the real process environment is excluded.
        env = {
            "PATH": str(self.psql.parent) + os.pathsep + os.defpath, "HOME": str(self.root),
            "PGDATABASE": "local_wallet", "PGPASSWORD": "local-test-secret",
            "PGOPTIONS": "local-options", "PGHOST": "wrong-host.invalid",
            "PGSERVICEFILE": str(self.old_service), "PGPASSFILE": str(self.old_passfile),
        }
        process = subprocess.run(
            [sys.executable, "-c", HARNESS, str(self.root), json.dumps(responses)],
            env=env, cwd=ROOT, input=PASSWORD, capture_output=True, text=True, timeout=10,
        )
        recorded = self.root / "calls.jsonl"
        self.assertTrue(recorded.exists(), process.stderr)
        calls = [json.loads(line) for line in recorded.read_text().splitlines()]
        for call in calls:
            self.assertEqual(call["dotenv"], self.original)
            command = call["command"]
            self.assertNotIn(PASSWORD, repr(command))
            for option in ("-X", "-w", "ON_ERROR_STOP=1"):
                self.assertIn(option, command)
            if "-c" in command:
                query = command[command.index("-c") + 1].lower()
                for required in ("current_database()", "public.t_payout_order", "requires_review",
                                 "has_table_privilege", "public.t_payout_pool_snapshot", "'insert'"):
                    self.assertIn(required, query)
            self.assertNotIn("local-test-secret", repr(call["env"]))
            for key in ("PGPASSWORD", "PGDATABASE", "PGOPTIONS", "PGHOST"):
                self.assertNotIn(key, call["env"])
        self.assertNotIn(PASSWORD, process.stdout + process.stderr)
        self.assertEqual(self.old_service.read_text(), "[old]\ndbname=previous\n")
        self.assertEqual(self.old_passfile.read_text(), "oldhost:5432:previous:user:old-password\n")
        return process, calls

    def test_existing_schema_writer_skips_ddl_and_publishes_private_verified_settings(self):
        process, calls = self.invoke([["sulu_db", True, True, True]])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("-f", calls[0]["command"])
        self.assertIn(self.saved_settings, self.dotenv.read_text())
        self.assertNotIn(PASSWORD, self.dotenv.read_text())
        values = read_env(self.dotenv)
        service = Path(values["SULU_PGSERVICEFILE"])
        pgpass = Path(values["SULU_PGPASSFILE"])
        self.assertEqual(values["SULU_PGSERVICE"], calls[0]["env"]["PGSERVICE"])
        self.assertEqual(str(service), calls[0]["env"]["PGSERVICEFILE"])
        self.assertEqual(str(pgpass), calls[0]["env"]["PGPASSFILE"])
        for path in (self.dotenv, service, pgpass):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(service)
        connection = ini[values["SULU_PGSERVICE"]]
        for key, value in dict(host="db.example.invalid", port="5432", dbname="sulu_db",
                               user="pool_writer", sslmode="verify-full").items():
            self.assertEqual(connection[key], value)
        self.assertNotIn(PASSWORD, service.read_text())
        self.assertEqual(pgpass.read_text(), r"db.example.invalid:5432:sulu_db:pool_writer:private\:fixture\\password" + "\n")

    def test_missing_schema_applies_bundled_ddl_then_verifies_writer(self):
        process, calls = self.invoke([["sulu_db", True, False, False], "ok", ["sulu_db", True, True, True]])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(calls), 3)
        command = calls[1]["command"]
        self.assertEqual(Path(command[command.index("-f") + 1]), self.root / "sql/sulu_pay/payout_pool.sql")
        self.assertNotIn("-f", calls[0]["command"])
        self.assertNotIn("-f", calls[2]["command"])
        self.assertNotEqual(self.dotenv.read_text(), self.original)

    def test_wrong_db_or_missing_business_schema_never_applies_ddl_or_enables(self):
        for database, is_sulu in (("local_wallet", True), ("sulu_db", False)):
            with self.subTest(database=database, is_sulu=is_sulu):
                (self.root / "calls.jsonl").unlink(missing_ok=True)
                process, calls = self.invoke([[database, is_sulu, False, False]])
                self.assertEqual(process.returncode, 3, process.stderr)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("-f", calls[0]["command"])
                self.assertEqual(self.dotenv.read_text(), self.original)
                self.assertFalse(Path(calls[0]["env"]["PGPASSFILE"]).exists())

    def test_ddl_failure_or_missing_insert_permission_keeps_previous_config(self):
        for finish, count in ((["fail"], 2), (["ok", ["sulu_db", True, True, False]], 3)):
            with self.subTest(finish=finish):
                (self.root / "calls.jsonl").unlink(missing_ok=True)
                process, calls = self.invoke([["sulu_db", True, False, False], *finish])
                self.assertEqual(process.returncode, 3, process.stderr)
                self.assertEqual(len(calls), count)
                self.assertEqual(self.dotenv.read_text(), self.original)
                self.assertFalse(Path(calls[0]["env"]["PGPASSFILE"]).exists())
