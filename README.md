# walletQuota

并发登录所有已启用的支付渠道，读取各自订单池全部分页，最后返回一个综合结果：

```text
total:    所有完整成功渠道的可信总计
atgpay:   ATG Pay 的订单数、金额、金额分布或错误状态
millerpay: MillerPay 的订单数、金额、金额分布或错误状态
```

一个渠道失败不会影响其他渠道。失败和不完整渠道会保留在报告中，但不会混入 `total`。

当前已接入 `atgpay` 和 `millerpay`；多渠道注册、并发、故障隔离和综合报告框架已经完成。工具只读取订单池，不会接单、付款、提交 UTR、绑卡或修改账户设置。

## 环境要求

- Python 3.9 或更高版本
- 无第三方依赖

## 配置

```bash
cp .env.example .env
```

在 `.env` 中填写本地账号：

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay
ATGPAY_PHONE=你的印度手机号
ATGPAY_PASSWORD=你的密码
MILLERPAY_PHONE=你的印度手机号
MILLERPAY_PASSWORD=你的密码
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

定时器不会补跑错过的时间点，也不会让查询重叠；退出后需要由人手动重启。它委托给同一条手动查询命令及同一份 `--env-file`，没有 Codex、cron、launchd 或开机自启动集成。只有本工具拥有的已导出报告快照会在 IST 日期边界过期时清理，包含兼容的旧手动报告；无法识别的目录一律保留。

旧 ATG 专用入口仍然可用。它只运行 `atgpay`，并保留原有筛选参数：

```bash
python3 atg_pool_report.py --sort-type 1 --order-type 4 --page-size 10
```

## 综合输出

终端先显示 `total`，再按配置顺序显示各渠道。例如：

```text
订单池综合报告
==========================================================
状态: partial
total:
  渠道: 成功 1 / 部分 0 / 失败 1
  订单数: 299
  总金额: ₹160500

atgpay: success
  订单数: 299
  总金额: ₹160500
  金额分布:
    ₹522 × 200（66.89%，小计 ₹104400）

millerpay: success
  订单数: 120
  总金额: ₹64000
  金额分布:
    ₹500 × 120（100%，小计 ₹64000）
```

每次运行生成独立快照目录：

```text
reports/20260812_153000/
├── aggregate.json
├── atgpay_orders.csv
└── millerpay_orders.csv
```

- `aggregate.json` 包含顶层 `total`、全部渠道状态、摘要、金额分布、元数据和订单。
- 金额分布固定为五个互不重叠的范围：`<100`（不含 100）、`100-10000`（含 100、不含 10000）、`10000-20000`（含 10000、不含 20000）、`20000-50000`（两端都含）和 `>50000`（不含 50000）。终端中 `total` 与每个非错误渠道都会完整显示这五行，即使某行数量为 0。
- 成功渠道生成自己的订单 CSV。
- 已取得订单的不完整渠道也生成 CSV，但不进入 `total`。
- 没有订单的错误渠道不生成空 CSV。
- 同一秒多次执行会使用 `_2`、`_3` 目录，不覆盖历史结果。

### 收款账户与敏感输出

ATG Pay 和 MillerPay 都读取完整订单池，并尽力从同样三类只读来源收集账户信息：已绑定 UPI、已绑定银行卡，以及“我的已领取任务”中的订单详情收款方。账户收集失败只会记录安全的警告，不会让已经完成的订单池快照变为失败。

终端只显示掩码账户标识；订单 CSV 不包含账户信息。完整 UPI、银行卡号和 IFSC 仅会写入本机快照目录的 `aggregate.json`（例如 `channels.atgpay.accounts` 和 `channels.millerpay.accounts`），因此该文件是敏感数据：不要上传、共享或提交到仓库。`账户: 0` 只表示当前登录未返回已绑定账户或已领取订单账户，**不**表示该支付渠道或商户不存在收款账户。

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
- 每个 Provider 只允许访问其显式列出的 HTTPS 主机和只读接口；ATG 基址固定为 `https://api.atg-game.com/app`，MillerPay 基址固定为 `https://api.millerpay-app.com/app`，Web Origin 为 `https://app-web.millerpay-app.com`。
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

两个渠道只读取订单与账户信息，绝不会调用 `/pay/receive/task`，也不会认领、接取或锁定订单、付款、提交 UTR、绑卡、删除、更新或修改账户设置。程序不会绕过 Cloudflare、验证码、登录或访问控制。服务端修改接口或签名规则时会安全失败，不会猜测或调用其他端点。
