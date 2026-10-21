# PostgreSQL 数据库

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
