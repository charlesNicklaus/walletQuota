"""Configure the optional Sulu Pay summary database on the collector host."""

import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wallet_quota_pipeline import sulu_postgres_environment
from walletquota.config import read_env
from walletquota.reporting import _atomic_text


PROBE = """
SELECT json_build_array(
    current_database(),
    to_regclass('public.t_payout_order') IS NOT NULL OR (
        current_database() = 'sulu_pool' AND (
            to_regclass('public.t_payout_pool_snapshot') IS NOT NULL OR NOT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
            )
        )
    ),
    to_regclass('public.t_payout_pool_snapshot') IS NOT NULL AND EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('public.v_payout_pool_current')
          AND attname = 'requires_review' AND NOT attisdropped
    ),
    has_schema_privilege(current_user, 'public', 'USAGE') AND
    has_table_privilege(current_user, to_regclass('public.t_payout_pool_snapshot'), 'INSERT')
)
"""


def field(label, default=""):
    value = input(label).strip() or default
    if not value or any(char in value for char in "\r\n\x00"):
        raise ValueError("数据库连接信息不完整。")
    return value


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        handle.write(value)


def probe(psql, environment, dbname):
    result = subprocess.run(psql + ["-At", "-c", PROBE], env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            text=True, timeout=20, check=True)
    actual, valid_target, ready, can_insert = json.loads(result.stdout)
    if actual != dbname or not valid_target:
        raise ValueError("请连接 Sulu Pay 业务库，或专用汇总库 sulu_pool（空库或已安装汇总表）。")
    return ready, can_insert


def configure(root):
    env_path = root / ".env"
    read_env(env_path)
    executable = shutil.which("psql")
    if not executable:
        raise RuntimeError("找不到 psql，请先安装 PostgreSQL 客户端。")
    host = field("Sulu Pay 数据库主机（使用隧道时填本地地址）：")
    port = field("端口 [5432]：", "5432")
    dbname = field("Sulu Pay 数据库名：")
    user = field("数据库用户名：")
    sslmode = field("SSL 模式 [verify-full]（以 DBA 提供的连接要求为准）：", "verify-full")
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("端口必须为 1–65535。")
    if ("," in host or host.startswith("#") or "://" in host or "=" in dbname
            or any("*" in value for value in (host, dbname, user))):
        raise ValueError("请分别输入单一主机、数据库名和用户名。")
    if sslmode not in {"verify-full", "verify-ca", "require", "disable"}:
        raise ValueError("支持的 SSL 模式为 verify-full、verify-ca、require 和 disable。")
    password = getpass.getpass("数据库密码（输入时不显示）：")
    if not password or any(char in password for char in "\r\n\x00"):
        raise ValueError("需要有效的单行密码。")

    private = root / ".local-secrets"
    private.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="sulu-db-", dir=private)).resolve()
    try:
        service_file, pass_file = directory / "pg_service.conf", directory / "pgpass"
        write_private(service_file, f"[sulu_pool]\nhost={host}\nport={port}\ndbname={dbname}\n"
                                    f"user={user}\nsslmode={sslmode}\n")
        fields = (host, port, dbname, user, password)
        write_private(pass_file, ":".join(v.replace("\\", "\\\\").replace(":", "\\:") for v in fields) + "\n")
        updates = {
            "SULU_PGSERVICE": "sulu_pool",
            "SULU_PGSERVICEFILE": str(service_file),
            "SULU_PGPASSFILE": str(pass_file),
        }
        environment = sulu_postgres_environment(updates)
        psql = [executable, "-X", "-w", "-v", "ON_ERROR_STOP=1"]
        ready, can_insert = probe(psql, environment, dbname)
        if not ready:
            print("已识别 Sulu Pay 库，正在安装汇总表和运营视图。")
            subprocess.run(psql + ["-f", str(root / "sql/sulu_pay/payout_pool.sql")],
                           env=environment, stdin=subprocess.DEVNULL, timeout=60, check=True)
            ready, can_insert = probe(psql, environment, dbname)
        if not ready or not can_insert:
            raise RuntimeError("请让 DBA 安装 sql/sulu_pay/payout_pool.sql 并授予汇总表 INSERT 权限。")

        lines = [line for line in env_path.read_text(encoding="utf-8").splitlines()
                 if line.partition("=")[0].strip() not in updates]
        lines.extend(f"{key}={value}" for key, value in updates.items())
        _atomic_text(env_path, lambda handle: handle.write("\n".join(lines) + "\n"))
    except BaseException:
        shutil.rmtree(directory)
        raise


if __name__ == "__main__":
    try:
        configure(ROOT)
    except (OSError, ValueError, RuntimeError, EOFError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        if isinstance(error, subprocess.CalledProcessError):
            message = "psql 执行失败。请核对上方连接或权限错误，必要时让 DBA 执行建表 SQL。"
        elif isinstance(error, subprocess.TimeoutExpired):
            message = "连接或建表超时，请核对网络、隧道和数据库状态。"
        else:
            message = str(error) or "操作已取消。"
        print("配置未启用：" + message, file=sys.stderr)
        sys.exit(1)
    print("Sulu Pay 汇总同步已配置，数据库和写入权限校验通过。")
