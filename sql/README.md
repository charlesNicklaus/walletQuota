# PostgreSQL 数据库

## 在 Sulu Pay 中查看上游代付订单池

Mac Studio 的 `./start.sh` 每轮可以向第二个 PostgreSQL 库单独写入渠道汇总。Sulu 的历史表 `public.t_payout_pool_snapshot` 每批每渠道一行，保留采集范围、状态、观测时间、可见订单笔数和金额（卢比）。订单号、UPI、银行账户和登录资料只留在各自原有位置。钱包侧完整快照继续使用原有 `PG*` 配置。

### 首次部署

在 Mac Studio 项目目录执行：

```bash
git pull --ff-only origin main && python3 scripts/configure_sulu_db.py
```

按照 DBA 提供的信息输入主机、端口、库名、用户名、SSL 模式和密码。通过 SSH 隧道访问时先建立隧道；使用 `verify-full` 时应使用证书匹配的主机名，并按 DBA 的说明配置根证书。工具先识别目标库中的 `public.t_payout_order`，按需安装汇总表和视图，然后检查 INSERT 权限。写入账号权限不足时，请让 DBA 在 Sulu 库运行 `sql/sulu_pay/payout_pool.sql`、授予写入权限后再执行上述命令。

服务文件和密码文件保存在本机 `.local-secrets/` 的私有子目录中。验证成功后工具将其绝对路径及服务名写入现有 `.env` 的三个 `SULU_PG*` 配置项。已运行带有 Sulu 同步功能的采集进程会在下一轮读取配置；如果正在运行升级前的旧程序，请重启采集。日志中 `SuluPay 汇总: imported=1 pending=0` 表示已导入一批且队列已清空。

### 手动配置已有的 PostgreSQL 服务文件

1. 在 Mac Studio 的项目目录执行 `git pull --ff-only origin main`。
2. 在运行采集的 macOS 用户的 `~/.pg_service.conf` 中添加独立服务。把下例中的主机、库名和用户替换成 Sulu Pay PostgreSQL 的实际值，并采用该库要求的 TLS 配置。

   ```ini
   [sulu_pool]
   host=你的Sulu数据库主机
   port=5432
   dbname=你的Sulu数据库名
   user=你的Sulu数据库用户
   ```

3. 将该用户的连接密码放在权限为 `600` 的专用密码文件中，例如 `~/.sulu_pool.pgpass`。按 libpq 格式填写一行 `主机:端口:库名:用户名:密码`。用有建表权限的 Sulu 数据库连接执行一次：

   ```bash
   chmod 600 ~/.pg_service.conf ~/.sulu_pool.pgpass
   PGPASSFILE="$HOME/.sulu_pool.pgpass" psql -X -w -v ON_ERROR_STOP=1 \
     'service=sulu_pool' -f sql/sulu_pay/payout_pool.sql
   ```

   如果 DBA 单独建表，让采集服务的用户具有该表的 INSERT 权限，并给查看报表的用户授予视图的 SELECT 权限。

4. 在项目现有 `.env` 中设置 `SULU_PGSERVICE=sulu_pool` 和 `SULU_PGPASSFILE=/Users/pal/.sulu_pool.pgpass`（按本机用户名填写绝对路径）。使用自定义服务文件时，再设置 `SULU_PGSERVICEFILE` 为其绝对路径。保留本地数据库的 `PGDATABASE` 原值。
5. 停止旧版采集进程，再运行 `./start.sh --verbose`。在 `reports/continuous.log` 中查看 Sulu 汇总导入的 `imported` 和 `pending`。连接中断时，各自队列保留待导入批次，下一轮按原批次号补传。同一份快照重复导入只保留一份。

### 运营查询

在 Sulu 数据库执行以下查询，得到最新收到批次中、最近 30 分钟内完整采集且为零的**已查订单池**：

```sql
SELECT channel, order_types, observed_at, order_count, order_amount_inr
FROM public.v_payout_pool_current
WHERE pool_state = 'scope_empty'
ORDER BY observed_at DESC;
```

查看全部七个渠道时，去掉 `WHERE` 并在查询列中加上 `provider`、`pool_state` 和 `requires_review`。缺失、失败、缺少范围或观测时间、数据过期、以及零笔但采集不完整，均返回 `unknown`。新鲜快照只要观察到订单就标记 `nonempty`，此时若 `complete=false`，笔数和金额只覆盖已观察部分。查询按采集批次排序，补传旧记录不会覆盖新一轮判断。

`DONEPAY` 持续使用 MillerPay 原有 API（`api.millerpay-app.com`），`requires_review` 固定为 `true`。完整、新鲜的零笔快照显示 `review_required`（待复核），自动从上面的空池候选查询中排除。历史表仍保存实际观测的笔数和金额。已部署旧版视图的数据库，重新执行一次 `sql/sulu_pay/payout_pool.sql` 即可更新规则，无需重抓数据。

映射关系为 ATG Pay → `AGTPAY`、MillerPay → `DONEPAY`、PenguinPay → `PENGUINPAY`、SharkPay → `SHARKPAY`、SmartWallet → `SMARTWALLET`、ShowPay → `SHOWPAY`、Hoyo Pay → `HOYOPAY`。范围字段 `order_types` 记录当前账号实际查询的页签。按默认配置，Hoyo 查 `1,2` 并按订单号去重，ATG 查 `4`，其余渠道查 `1`。MillerPay 的高额/SVIP 池需分别核对 `2` 和 `3`。空池可作为人工加量候选；实际派单仍取决于 Sulu 的渠道启用状态、金额限额和近期代付表现。

如需补录启用同步前的某次采集，可从它的本机报告单独生成汇总 SQL，再导入 Sulu：

```bash
python3 scripts/export_sulu_pool.py reports/<批次>/aggregate_<批次>.json --output reports/sulu-backfill.sql
PGPASSFILE="$HOME/.sulu_pool.pgpass" psql -X -w -v ON_ERROR_STOP=1 'service=sulu_pool' -f reports/sulu-backfill.sql
```

## Mac Studio 本地完整历史库

在 Mac Studio 上启动 PostgreSQL 后，从项目根目录执行以下命令。`createdb` 和 `psql` 使用你现有的本机 PostgreSQL 连接设置。

```sh
createdb walletquota
psql -v ON_ERROR_STOP=1 -d walletquota -f sql/migrations/0001_create_wallet_tables.sql
python3 scripts/export_postgres.py reports/20260812_160000/aggregate_20260812_160000.json --output reports/postgres_20260812_160000.data.sql
psql -v ON_ERROR_STOP=1 -d walletquota -f reports/postgres_20260812_160000.data.sql
```

输入必须是标准综合报告 JSON，请将示例替换为实际报告路径。每次抓取的报告名包含北京时间戳（Asia/Shanghai，UTC+8），例如 `aggregate_20260812_160000.json`；导出程序按 JSON 内容识别输入，旧版 `aggregate.json` 仍可使用。`.firecrawl/latest-four-refresh.json` 是指向报告的索引，不能直接作为输入。导出程序离线生成 SQL，不连接数据库，也不需要安装 PostgreSQL Python 驱动；手动指定 `--output` 时由你决定 SQL 文件名。数据库连接和数据导入均由本机 `psql` 执行。

`migrations/0001_create_wallet_tables.sql` 是可直接执行的 PostgreSQL migration，在一个事务内创建四张业务表、索引、汇总视图以及版本记录表 `public.wallet_schema_migrations`。成功后记录版本 `0001`；重复执行会跳过已经应用的版本，不覆盖表或已有数据。失败时整个事务回滚，版本也不会被标记为成功。

已执行旧版建表脚本的同结构数据库也可以执行此 migration，以保留数据并补上版本记录。`IF NOT EXISTS` 不会修复其他不兼容表结构。后续结构修改应新增编号 migration，保留已发布版本不变。版本记录可用以下 SQL 查询：

```sql
SELECT version, description, applied_at
FROM public.wallet_schema_migrations
ORDER BY version;
```

旧命令 `psql -v ON_ERROR_STOP=1 -d walletquota -f sql/schema.postgresql.sql` 仍兼容，通过 [psql 的相对引用命令 `\ir`](https://www.postgresql.org/docs/current/app-psql.html)加载同一 migration。数据库图形客户端可直接执行编号 migration 文件，它包含完整 SQL。生成的数据脚本使用确定性的批次 ID 和 `ON CONFLICT DO NOTHING`，同一份报告重复导入不会重复增加数据；报告内容变化会作为新批次保留。

| 表或视图 | 用途 |
| --- | --- |
| `public.wallet_crawl_runs` | 一次报告的批次、内容摘要、开始及结束时间、整体状态、元数据 |
| `public.wallet_channel_snapshots` | 各渠道在该批次的状态、完整性、订单摘要、错误及采集元数据 |
| `public.wallet_orders` | 各批次的 INR 订单金额、收入、余额及渠道属性 |
| `public.wallet_accounts` | 各批次的绑定 UPI、银行账户或订单收款方及其属性 |
| `public.wallet_run_totals` | 只统计成功且完整渠道的可信订单数和金额，并分别统计渠道状态 |

订单主键是 `(run_id, provider, order_id)`；同一订单在不同批次出现时会保留历史快照。账户主键是 `(run_id, provider, kind, account_id, order_id)`，没有关联订单的账户使用空字符串 `order_id`。账户只引用渠道快照；已领取订单的账户可能不在公开订单池中，因此不对账户的 `order_id` 建立订单外键。

金额使用不指定精度和小数位数的 `NUMERIC`，导出时保留十进制文本，缺失的收入和余额使用 SQL `NULL`。PostgreSQL 的 [`NUMERIC` 文档](https://www.postgresql.org/docs/current/datatype-numeric.html)说明了精确数值存储。时间使用 `TIMESTAMPTZ` 保存时间点；显示时受数据库会话时区影响，可以执行 `SET TIME ZONE 'Asia/Singapore';`。原报告的带偏移时间文本保留在元数据中。参见 PostgreSQL [日期和时间类型文档](https://www.postgresql.org/docs/current/datatype-datetime.html)。

渠道的完整 `summary` 和 `metadata` 保存在 JSONB 中，包括五档金额分布、收入及余额覆盖数、翻页情况、重复计数、Hoyo 各订单池快照以及来源报告元数据。五档金额边界与采集程序一致：`<100`、`[100,10000)`、`[10000,20000)`、`[20000,50000]`、`>50000`。

`partial` 或 `error` 渠道及其已取得的数据可以留存，但不计入 `wallet_run_totals` 的订单数和金额。没有采集的渠道不会生成记录，例如当前报告没有 Smart/Miles 时仍然是未知，不能据此解释为零订单。可信汇总中的渠道数仅代表该报告实际收录的渠道。

查看最新批次的可信汇总：

```sql
SELECT *
FROM public.wallet_run_totals
ORDER BY finished_at DESC
LIMIT 10;
```

账户数据可能包含未脱敏的银行卡号和 UPI。生成的 SQL 与原始报告应保留在本机被 Git 忽略的 `reports/` 目录，勿提交到仓库。上述导出命令只生成文件；需要持续抓取并自动导入时，配置 `.env` 中的渠道账号和 `PGDATABASE=walletquota`，然后执行项目根目录的 `./start.sh`。具体启动、日志和停止命令见 [项目说明](../README.md#mac-studio-持续抓取并自动入库)。

持续模式每轮保存带时间戳的报告和 SQL，例如 `aggregate_20260812_160000.json` 和 `postgres_20260812_160000.data.sql`。时间戳与快照目录一致，固定采用北京时间（Asia/Shanghai，UTC+8），不受系统时区影响；同一秒多次执行会连同文件名保留 `_2`、`_3` 后缀。在写完 SQL 后创建带校验值的 `postgres_20260812_160000.pending` 标记，再用非交互式 `psql -X -w -v ON_ERROR_STOP=1` 导入。连接参数通过环境变量传递，不放在命令行中。失败时保留待导入标记并在下轮重试，成功后仅将后缀改为 `.imported`。队列仍兼容旧版 `postgres.pending` 和 `postgres.data.sql`。报告、导入 SQL 和历史数据库记录持续保留；没有过期清理或级联删除配置。`reports/continuous.log` 是后台进程的持续日志，文件名不随抓取批次变化。
