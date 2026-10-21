#!/bin/bash
set -u
umask 077

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) ||
    fail '启动失败：无法定位项目目录。'
cd -- "$script_dir" 2>/dev/null || fail '启动失败：无法进入项目目录。'
[ -f "$script_dir/.env" ] || fail '启动失败：缺少 .env 配置文件。'
python_bin=${PYTHON_BIN:-python3}
command -v "$python_bin" >/dev/null 2>&1 || fail '启动失败：找不到 Python 命令。'
command -v psql >/dev/null 2>&1 || fail '启动失败：找不到 psql 命令。'

foreground=false
if [ "${1:-}" = --foreground ]; then
    foreground=true
    shift
fi
child=("$python_bin" -u "$script_dir/wallet_quota_scheduler.py"
       --env-file "$script_dir/.env" --postgres --include-hoyo "$@")

if "$foreground"; then
    if command -v caffeinate >/dev/null 2>&1; then
        exec caffeinate -i "${child[@]}"
    fi
    exec "${child[@]}"
fi

log_file="$script_dir/reports/continuous.log"
mkdir -p -- "$script_dir/reports" 2>/dev/null || fail '启动失败：无法创建日志目录。'
if ! { : >> "$log_file"; } 2>/dev/null || ! chmod 600 "$log_file" 2>/dev/null; then
    fail '启动失败：无法准备日志文件。'
fi
nohup "${child[@]}" </dev/null >> "$log_file" 2>&1 &
worker_pid=$!
if command -v caffeinate >/dev/null 2>&1; then
    nohup caffeinate -i -w "$worker_pid" </dev/null >> "$log_file" 2>&1 &
fi
printf '进程已启动，首轮结果见日志。\nPID: %s\n日志: %s\n' "$worker_pid" "$log_file"
