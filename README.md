# walletQuota

并发登录所有已启用的支付渠道，读取各自订单池全部分页，最后返回一个综合结果：

```text
total:    所有完整成功渠道的可信总计
atgpay:   ATG Pay 的订单数、金额、金额分布或错误状态
millerpay: MillerPay 的订单数、金额、金额分布或错误状态
penguinpay: PenguinPay 的订单数、金额、金额分布或错误状态
```

一个渠道失败不会影响其他渠道。失败和不完整渠道会保留在报告中，但不会混入 `total`。

当前已接入 `atgpay`、`millerpay` 和 `penguinpay`；多渠道注册、并发、故障隔离和综合报告框架已经完成。工具只读取订单池，不会接单、付款、提交 UTR、绑卡或修改账户设置。

## 环境要求

- Python 3.9 或更高版本
- 无第三方依赖

## 配置

```bash
cp .env.example .env
```

在 `.env` 中填写本地账号：

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay,penguinpay
ATGPAY_PHONE=你的印度手机号
ATGPAY_PASSWORD=你的密码
MILLERPAY_PHONE=你的印度手机号
MILLERPAY_PASSWORD=你的密码
PENGUINPAY_PHONE=你的印度手机号
PENGUINPAY_PASSWORD=你的密码
REPORT_DIRECTORY=reports
```

`ENABLED_PROVIDERS` 用英文逗号分隔。只查询 ATG Pay 时，例如：

```dotenv
ENABLED_PROVIDERS=atgpay
```

程序会并发运行这里列出的所有渠道，并按照配置顺序输出结果。未知渠道会显示 `Provider is not registered`；如果没有任何已注册渠道，程序返回配置错误。

`.env` 已被 Git 忽略。不要把密码、`loginToken`、`sessionKey`、Cookie、签名或验证码发送到聊天或提交到仓库。

### ATG Pay 配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `ATGPAY_API_BASE` | `https://api.atg-game.com/app` | API 基址，必须使用 HTTPS |
| `ATGPAY_SORT_TYPE` | `1` | 排序类型 |
| `ATGPAY_ORDER_TYPE` | `4` | 订单池类型 |
| `ATGPAY_PAGE_SIZE` | `10` | 每页数量，`1–1000` |
| `ATGPAY_REQUEST_DELAY_MS` | `150` | 分页请求之间的等待毫秒数 |
| `ATGPAY_TIMEOUT_SECONDS` | `30` | 单次 HTTP 请求超时秒数 |

### MillerPay 配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `MILLERPAY_PHONE` | 无 | 本地登录手机号，必填 |
| `MILLERPAY_PASSWORD` | 无 | 本地登录密码，必填 |
| `MILLERPAY_API_BASE` | `https://api.millerpay-app.com/app` | API 基址，必须使用 HTTPS 且为受信任的 MillerPay 主机 |
| `MILLERPAY_SORT_TYPE` | `1` | 排序类型 |
| `MILLERPAY_ORDER_TYPE` | `1` | 订单池类型；MillerPay 默认 `orderType=1` |
| `MILLERPAY_PAGE_SIZE` | `10` | 每页数量，`1–1000` |
| `MILLERPAY_REQUEST_DELAY_MS` | `150` | 分页请求之间的等待毫秒数 |
| `MILLERPAY_TIMEOUT_SECONDS` | `30` | 单次 HTTP 请求超时秒数 |

### PenguinPay 配置

PenguinPay API Base: https://api.penguinpay-app.com/app

PenguinPay Web Origin: https://app-web.penguinpay-app.com

PenguinPay 默认 sortType=1、orderType=1、pageSize=10。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `PENGUINPAY_PHONE` | 无 | 本地登录手机号，必填 |
| `PENGUINPAY_PASSWORD` | 无 | 本地登录密码，必填 |
| `PENGUINPAY_API_BASE` | `https://api.penguinpay-app.com/app` | API 基址，必须使用 HTTPS 且为受信任的 PenguinPay 主机 |
| `PENGUINPAY_SORT_TYPE` | `1` | 排序类型；PenguinPay 默认 `sortType=1` |
| `PENGUINPAY_ORDER_TYPE` | `1` | 订单池类型；PenguinPay 默认 `orderType=1` |
| `PENGUINPAY_PAGE_SIZE` | `10` | 每页数量，`1–1000`；PenguinPay 默认 `pageSize=10` |
| `PENGUINPAY_REQUEST_DELAY_MS` | `150` | 分页请求之间的等待毫秒数 |
| `PENGUINPAY_TIMEOUT_SECONDS` | `30` | 单次 HTTP 请求超时秒数 |

旧版 `ATG_PHONE`、`ATG_PASSWORD` 和其他 `ATG_*` 配置仍然兼容；同一个配置同时出现时，新的 `ATGPAY_*` 优先。报告根目录优先使用 `REPORT_DIRECTORY`，缺失时回退到旧 `ATG_REPORT_DIRECTORY`，也可以通过 `--output-dir` 临时覆盖。

## 使用

运行所有已启用渠道：

```bash
python3 wallet_quota.py
```

显示安全的渠道进度：

```bash
python3 wallet_quota.py --verbose
```

使用其他配置文件或报告目录：

```bash
python3 wallet_quota.py --env-file private.env --output-dir snapshots
```

### 独立定时运行

前台定时器会立刻执行一次，并随后在 IST 每小时的 `:00`、`:20`、`:40` 运行：

```bash
python3 wallet_quota_scheduler.py --verbose
# 使用 Ctrl+C 停止
```

也可以随时手动执行一次查询：

```bash
python3 wallet_quota.py --verbose
```

定时器不会补跑错过的时间点，也不会让查询重叠；退出后需要由人手动重启。它委托给同一条手动查询命令及同一份 `--env-file`，没有 Codex、cron、launchd 或开机自启动集成。所有历史报告持续保留；启动和跨日均不自动删除报告。

### Mac Studio 持续抓取并自动入库

PostgreSQL migration 执行完成后，在本地 `.env` 配置各渠道账号，以及 `PGDATABASE=walletquota`。`ENABLED_PROVIDERS` 控制 ATG、Miller、Penguin；启动脚本额外启用 Hoyo，读取 `HOYOPAY_PHONE` 和 `HOYOPAY_PASSWORD`。某渠道配置或采集失败时，其余渠道继续运行，失败及不完整渠道不计入可信总计。

```bash
./start.sh
tail -f reports/continuous.log
```

脚本从任意工作目录都可启动，会进入项目目录、脱离终端并显示实际采集进程 PID；日志权限为 `0600`。进程启动信息不代表首轮抓取或数据库导入已成功，应查看日志中的渠道状态、`imported` 和 `pending`。首次立即抓取，之后沿用 IST 每小时 `:00`、`:20`、`:40` 的调度；同一报告目录的进程锁避免重复采集。Mac 上存在 `caffeinate` 时会在进程存活期间阻止空闲睡眠。

每轮报告文件名都带快照目录的北京时间戳（Asia/Shanghai，UTC+8），例如 `aggregate_20260812_160000.json`、`penguinpay_orders_20260812_160000.csv` 和 `postgres_20260812_160000.data.sql`，然后通过本机 `psql` 自动导入。数据库不可用时保留 `postgres_20260812_160000.pending`；后续周期先完成新抓取，再按时间顺序重试待导入批次。导入成功后仅将标记后缀改为 `.imported`，历史报告和 SQL 持续保留。旧版 `postgres.pending` 和 `postgres.data.sql` 批次仍可自动补导。同一报告重复导入不会重复增加数据库记录。`reports/continuous.log` 是整个后台进程的持续日志，保留固定文件名。

数据库连接可以使用当前终端的标准 PostgreSQL 环境变量，也可在 `.env` 设置 `PGDATABASE`、`PGHOST`、`PGPORT`、`PGUSER`、`PGPASSFILE`、`PGSERVICE`、`PGSERVICEFILE`、`PGSSLMODE` 或本地 `PGPASSWORD`。密码不放入命令行；后台导入不交互询问密码。使用与手工 `psql` 相同的本机连接设置即可。

前台运行并用 Ctrl+C 停止：

```bash
./start.sh --foreground
```

后台模式可对启动时显示的采集进程 PID 执行 `kill -TERM 实际PID`。关闭终端不会停止后台采集；重启机器后需重新启动。仅执行一次抓取和自动入库时可运行 `python3 wallet_quota_pipeline.py --include-hoyo --verbose`。

旧 ATG 专用入口仍然可用。它只运行 `atgpay`，并保留原有筛选参数：

```bash
python3 atg_pool_report.py --sort-type 1 --order-type 4 --page-size 10
```

### HOYO PAY 独立抓取

已注册的 HOYO PAY 账号可使用独立脚本。先在本地 `.env` 填写 `HOYOPAY_PHONE` 和 `HOYOPAY_PASSWORD`，然后运行：

```bash
python3 scripts/hoyopay_readonly.py --env-file .env --output reports/hoyopay-result.json
```

脚本读取 Low Amount（`orderType=1`）和 High Amount（`orderType=2`）的全部分页，跨池按订单号去重；同一订单的数据冲突或任一池不完整时，结果标为 `partial`。账户信息同样只从已绑定 UPI、已绑定银行卡及已领取订单详情收集。结果 JSON 以本机文件权限 `0600` 写入，包含完整账户信息时应作为敏感文件保管。

HOYO PAY 使用固定的 `https://api.hoyopay-app.com/app` API 和只读端点；脚本只登录、读取，不注册账号、发送短信、接单或付款。它尚未注册到多渠道 Runner，不要把 `hoyopay` 加入 `ENABLED_PROVIDERS`。成功退出码为 `0`，不完整为 `3`，配置或运行错误为 `1`。

### 离线合并已保存快照

标准综合报告 JSON 和独立 HOYO PAY 结果可以合并为新的标准报告目录。把下面的输入路径换成实际报告路径：

```bash
python3 scripts/merge_wallet_reports.py \
  reports/20260812_160000/aggregate_20260812_160000.json path/to/hoyopay-result.json \
  --output-dir reports
```

合并工具按 JSON 内容识别输入，旧版 `aggregate.json` 仍可使用。工具不联网，不修改输入文件；金额从逐笔订单重新计算，只有完整成功渠道计入可信总计。同一渠道重复出现时会拒绝合并，避免把两个时间点的订单池重复累加。各渠道原始时间和元数据会保留，报告的时间范围覆盖最早开始至最晚结束；不同时间采集的快照不代表同时余额。

订单池实时变化可能使跨页快照不完整。MillerPay 和 PenguinPay 的本地 `*_PAGE_SIZE` 可在配置允许的范围内调大，以减少翻页次数；是否完整仍以实际返回数据和完整性校验为准。

### PostgreSQL 建表与数据导入

Mac Studio 可直接执行 [PostgreSQL migration](sql/migrations/0001_create_wallet_tables.sql)，创建抓取批次、渠道快照、订单和账户四张业务表，并记录已应用版本。每次报告独立留存，同一报告重复导入不会重复增加记录；可信汇总视图只统计完整成功渠道。

```bash
psql -v ON_ERROR_STOP=1 -d walletquota -f sql/migrations/0001_create_wallet_tables.sql
python3 scripts/export_postgres.py reports/20260812_160000/aggregate_20260812_160000.json --output reports/postgres_20260812_160000.data.sql
psql -v ON_ERROR_STOP=1 -d walletquota -f reports/postgres_20260812_160000.data.sql
```

先创建 `walletquota` 数据库，输入路径替换为实际的综合报告 JSON。导出工具按内容解析，旧版 `aggregate.json` 仍可使用；手动指定 `--output` 时由你决定 SQL 文件名。Migration 在事务中执行，重复执行会跳过已应用版本。旧的 `sql/schema.postgresql.sql` 命令仍可使用。导出工具离线生成权限为 `0600` 的本地 SQL；其中可能包含完整账户信息，应保留在被忽略的 `reports/` 目录。建表、查询和时区说明见 [数据库使用说明](sql/README.md)。

## 综合输出

终端先显示 `total`，再按配置顺序显示各渠道。为便于阅读，下面的示例省略了 `total` 和每个渠道的部分金额分布行；实际输出会完整显示五个固定范围。

```text
订单池综合报告
==========================================================
状态: success
total:
  渠道: 成功 2 / 部分 0 / 失败 0
  订单数: 419
  总金额: ₹224500

atgpay: success
  订单数: 299
  总金额: ₹160500
  金额分布:
    范围 100-10000 × 200（66.89%，小计 ₹104400）
    …（其余固定范围省略）

millerpay: success
  订单数: 120
  总金额: ₹64000
  金额分布:
    范围 100-10000 × 120（100%，小计 ₹64000）
    …（其余固定范围省略）

penguinpay: success
  订单数: 80
  总金额: ₹40000
  金额分布:
    范围 100-10000 × 80（100%，小计 ₹40000）
    …（其余固定范围省略）
```

每次运行生成独立快照目录，目录和文件名统一使用北京时间（Asia/Shanghai，UTC+8），不受运行机器的系统时区影响：

```text
reports/20260812_160000/
├── aggregate_20260812_160000.json
├── atgpay_orders_20260812_160000.csv
├── millerpay_orders_20260812_160000.csv
└── penguinpay_orders_20260812_160000.csv
```

- `aggregate_<timestamp>.json` 包含顶层 `total`、全部渠道状态、摘要、金额分布、元数据和订单。
- 金额分布固定为五个互不重叠的范围：`<100`（不含 100）、`100-10000`（含 100、不含 10000）、`10000-20000`（含 10000、不含 20000）、`20000-50000`（两端都含）和 `>50000`（不含 50000）。终端中 `total` 与每个非错误渠道都会完整显示这五行，即使某行数量为 0。
- 成功渠道生成自己的订单 CSV。
- 已取得订单的不完整渠道也生成 CSV，但不进入 `total`。
- 没有订单的错误渠道不生成空 CSV。
- 同一秒多次执行会使用 `_2`、`_3` 目录，文件名也带相同后缀，例如 `20260812_160000_2/aggregate_20260812_160000_2.json`，不覆盖历史结果。

### 收款账户与敏感输出

ATG Pay、MillerPay 和 PenguinPay 都读取完整订单池，并尽力从同样三类只读来源收集账户信息：已绑定 UPI、已绑定银行卡，以及“我的已领取任务”中的订单详情收款方。PenguinPay 账户来源：绑定 UPI、绑定银行卡、已领取订单详情。账户收集失败只会记录安全的警告，不会让已经完成的订单池快照变为失败。

终端只显示掩码账户标识；订单 CSV 不包含账户信息；`penguinpay_orders_<timestamp>.csv` 不包含账户字段。完整 UPI、银行卡号和 IFSC 仅会写入本机快照目录的 `aggregate_<timestamp>.json`（例如 `channels.atgpay.accounts`、`channels.millerpay.accounts` 和 `channels.penguinpay.accounts`），因此该文件是敏感数据：不要上传、共享或提交到仓库；`channels.penguinpay.accounts` 可包含完整本机账户标识。`账户: 0` 只表示当前登录未返回账户，不表示渠道或商户没有账户。

所有金额按 INR 汇总。跨渠道订单唯一键是“渠道名 + 订单号”，不同渠道相同订单号不会互相覆盖。

## 状态和退出码

- `success`：所有启用渠道均成功且分页完整。
- `partial`：至少一个渠道失败或分页不完整，综合报告仍已生成。

命令退出码：

- `0`：全部渠道成功且完整。
- `1`：程序级错误，例如无法写报告。
- `2`：基础配置错误，例如配置文件不存在或没有可运行的已注册渠道。
- `3`：至少一个渠道失败或不完整，但其他结果与综合报告可用。

## 新增渠道

每个渠道都是独立 Provider。接入新渠道需要：

1. 在 `walletquota/providers/<渠道名>/` 实现本渠道配置、认证、分页和标准订单映射。
2. 返回统一 `ProviderResult`，状态只能是 `success`、`partial` 或 `error`。
3. 使用本地假响应增加登录、分页、错误脱敏和 Provider 契约测试。
4. 在 `walletquota/registry.py` 的 `PROVIDER_FACTORIES` 中显式注册。
5. 在 `.env` 中加入渠道配置并把名称添加到 `ENABLED_PROVIDERS`。

聚合器、Runner 和报告层不需要因新增渠道而修改。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用本地假响应，不需要真实账号，也不会访问支付接口。

## 安全边界

- 凭据只从本地 `.env` 读取，不提供密码命令行参数。
- 报告和日志不会写入密码、Token、Session Key、Cookie、签名或原始认证响应。
- 每个 Provider 只允许访问其显式列出的 HTTPS 主机和只读接口；ATG 基址固定为 `https://api.atg-game.com/app`，MillerPay 基址固定为 `https://api.millerpay-app.com/app`，Web Origin 为 `https://app-web.millerpay-app.com`；PenguinPay API Base 为 `https://api.penguinpay-app.com/app`，Web Origin 为 `https://app-web.penguinpay-app.com`。
- ATG Pay 只允许调用：

```text
POST /auth/login
POST /pay/debit/task
POST /user/upi/bind/list
POST /bank/debit/card/list
POST /pay/my/task
POST /pay/order/detail
```

MillerPay 只允许调用：

```text
POST /auth/login
POST /pay/debit/task
POST /user/upi/bind/list
POST /bank/debit/card/list
POST /pay/my/task
POST /pay/order/detail
```

PenguinPay 只允许调用：

```text
POST /auth/login
POST /pay/debit/task
POST /user/upi/bind/list
POST /bank/debit/card/list
POST /pay/my/task
POST /pay/order/detail
```

三个渠道只读取订单与账户信息，绝不会调用 `/pay/receive/task`，也不会认领、接取或锁定订单、付款、提交 UTR、绑卡、删除、更新或修改账户设置。程序不会绕过 Cloudflare、验证码、登录或访问控制。服务端修改接口或签名规则时会安全失败，不会猜测或调用其他端点。

PenguinPay 永不调用 `/pay/receive/task`、`/pay/submit/utr`、`/pay/cancel/task`、`/user/account/wallet`、`/pay/debit/upis`，也永不调用付款、绑定、解绑、删除、更新或其他变更端点。
