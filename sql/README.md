# PostgreSQL 数据库

在 Mac Studio 上启动 PostgreSQL 后，从项目根目录执行以下命令。`createdb` 和 `psql` 使用你现有的本机 PostgreSQL 连接设置。

```sh
createdb walletquota
psql -v ON_ERROR_STOP=1 -d walletquota -f sql/schema.postgresql.sql
python3 scripts/export_postgres.py path/to/aggregate.json --output reports/postgres-data.sql
psql -v ON_ERROR_STOP=1 -d walletquota -f reports/postgres-data.sql
```

输入必须是标准报告目录中的 `aggregate.json`；`.firecrawl/latest-four-refresh.json` 是指向报告的索引，不能直接作为输入。导出程序离线生成 SQL，不连接数据库，也不需要安装 PostgreSQL Python 驱动。数据库连接和数据导入均由本机 `psql` 执行。

建表脚本在事务内创建四张表、索引和一个汇总视图。可以对相同表结构重复执行；`IF NOT EXISTS` 不会升级已经存在的旧表结构。生成的数据脚本使用确定性的批次 ID 和 `ON CONFLICT DO NOTHING`，同一份报告重复导入不会重复增加数据；报告内容变化会作为新批次保留。

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

账户数据可能包含未脱敏的银行卡号和 UPI。生成的 SQL 与原始报告应保留在本机被 Git 忽略的 `reports/` 目录，勿提交到仓库。这里没有自动连接、定时导入、过期清理或级联删除配置。
