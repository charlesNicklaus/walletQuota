import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase


START_SCRIPT = Path(__file__).resolve().parents[1] / "start.sh"


class StartScriptTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "project with spaces").resolve()
        self.root.mkdir()
        self.script = self.root / "start.sh"
        if START_SCRIPT.exists():
            shutil.copy2(START_SCRIPT, self.script)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.record = self.root / "child.json"
        self.release = self.root / "release"
        self.finished = self.root / "finished"
        self.leak = self.root / "sourced-env"
        (self.root / ".env").write_text(
            "AUTH_SECRET=private-env-value\n$(/usr/bin/touch '" + str(self.leak) + "')\n"
        )
        for name in ("dirname", "mkdir", "chmod", "nohup"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.write_command("psql", "#!/bin/sh\necho private-psql-value\nexit 0\n")
        python_command = "#!/bin/sh\nexec \"$TEST_PYTHON\" \"$FAKE_CHILD\" \"$@\"\n"
        self.python = self.write_command("fake python", python_command)
        self.write_command("python3", python_command)
        fake_child = self.root / "fake_child.py"
        fake_child.write_text(
            "import json, os, sys, time\nfrom pathlib import Path\n"
            "Path(os.environ['CHILD_RECORD']).write_text(json.dumps("
            "{'args': sys.argv[1:], 'cwd': os.getcwd(), 'pid': os.getpid(), 'stdin': sys.stdin.read()}))\n"
            "print('safe stdout', flush=True)\nprint('safe stderr', file=sys.stderr, flush=True)\n"
            "deadline = time.monotonic() + 3\n"
            "while os.environ.get('WAIT_RELEASE') and not Path(os.environ['RELEASE']).exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            "Path(os.environ['FINISHED']).write_text('done')\n"
            "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n"
        )
        self.env = dict(os.environ, PATH=str(self.bin), PYTHON_BIN=str(self.python),
                        TEST_PYTHON=sys.executable, FAKE_CHILD=str(fake_child),
                        CHILD_RECORD=str(self.record), RELEASE=str(self.release),
                        FINISHED=str(self.finished))

    def write_command(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def launch(self, *args, **kwargs):
        return subprocess.run(["/bin/bash", str(self.script), *args], cwd=self.root.parent,
                              env=self.env, input="terminal input", capture_output=True,
                              text=True, timeout=2, **kwargs)

    def wait_for(self, path):
        deadline = time.monotonic() + 4
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), str(path))

    def assert_child_arguments(self, extra=()):
        data = json.loads(self.record.read_text())
        self.assertEqual(data["cwd"], str(self.root))
        self.assertEqual(data["args"], ["-u", str(self.root / "wallet_quota_scheduler.py"),
                                      "--env-file", str(self.root / ".env"),
                                      "--postgres", "--include-hoyo", *extra])
        self.assertFalse(self.leak.exists())

    def test_foreground_preserves_arguments_and_exit_status_from_any_directory(self):
        self.env["FAKE_EXIT"] = "7"
        extra = ["--output-dir", "reports with spaces", "--verbose"]
        result = self.launch("--foreground", *extra)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "safe stdout\n")
        self.assertEqual(result.stderr, "safe stderr\n")
        self.assert_child_arguments(extra)

    def test_background_returns_with_all_child_streams_detached_and_private_log(self):
        log = self.root / "reports" / "continuous.log"
        log.parent.mkdir()
        log.write_text("earlier run\n")
        log.chmod(0o644)
        self.env["WAIT_RELEASE"] = "1"
        try:
            result = self.launch()
            self.assertEqual(result.returncode, 0)
            self.assertIn("进程已启动，首轮结果见日志", result.stdout)
            self.assertIn(str(log), result.stdout)
            self.assertRegex(result.stdout, r"PID: [0-9]+")
            self.assertEqual(result.stderr, "")
            self.wait_for(self.record)
            self.assertFalse(self.finished.exists())
            self.assert_child_arguments()
            self.assertIn("PID: " + str(json.loads(self.record.read_text())["pid"]), result.stdout)
            self.assertEqual(json.loads(self.record.read_text())["stdin"], "")
        finally:
            self.release.touch()
            if self.record.exists():
                self.wait_for(self.finished)
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertIn("earlier run\n", log.read_text())
        self.assertIn("safe stdout\n", log.read_text())
        self.assertIn("safe stderr\n", log.read_text())
        self.assertNotIn("private-env-value", log.read_text())

    def test_default_python_and_optional_caffeinate_keep_foreground_status(self):
        self.env.pop("PYTHON_BIN")
        self.env["FAKE_EXIT"] = "9"
        record = self.root / "caffeinate.txt"
        self.env["CAFFEINATE_RECORD"] = str(record)
        self.write_command("caffeinate", "#!/bin/sh\nprintf '%s\\n' \"$1\" > \"$CAFFEINATE_RECORD\"\nshift\nexec \"$@\"\n")
        result = self.launch("--foreground")
        self.assertEqual(result.returncode, 9)
        self.assertEqual(record.read_text(), "-i\n")
        self.assert_child_arguments()

    def test_background_creates_private_log_and_caffeinate_watches_actual_child(self):
        record = self.root / "caffeinate.txt"
        self.env["CAFFEINATE_RECORD"] = str(record)
        self.write_command("caffeinate", "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAFFEINATE_RECORD\"\n")
        result = self.launch()
        self.wait_for(self.finished)
        self.wait_for(record)
        pid = str(json.loads(self.record.read_text())["pid"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("PID: " + pid, result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(record.read_text().splitlines(), ["-i", "-w", pid])
        log = self.root / "reports" / "continuous.log"
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(log.parent.stat().st_mode), 0o700)

    def test_missing_config_or_dependencies_fail_safely_before_launch(self):
        cases = [("config", "启动失败：缺少 .env 配置文件。\n"),
                 ("python", "启动失败：找不到 Python 命令。\n"),
                 ("psql", "启动失败：找不到 psql 命令。\n")]
        for missing, expected in cases:
            with self.subTest(missing=missing):
                env_file = self.root / ".env"
                if missing == "config":
                    env_file.rename(self.root / "saved.env")
                elif missing == "python":
                    self.env["PYTHON_BIN"] = "missing-private-python-value"
                else:
                    (self.bin / "psql").unlink()
                result = self.launch()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, expected)
                self.assertFalse(self.record.exists())
                if missing == "config":
                    (self.root / "saved.env").rename(env_file)
                self.env["PYTHON_BIN"] = str(self.python)
