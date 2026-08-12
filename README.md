# walletQuota

按需登录 ATG Pay，读取当前订单池的全部分页，并生成订单数量、金额汇总和精确金额分布。工具只调用登录接口和只读订单池接口，不会接单、付款、提交 UTR、绑卡或修改账户设置。

## 环境要求

- Python 3.9 或更高版本
- 无第三方依赖

## 配置

复制配置模板：

```bash
cp .env.example .env
```

编辑 `.env`，填写本地账号：

```dotenv
ATG_PHONE=你的印度手机号
ATG_PASSWORD=你的密码
```

`.env` 已被 Git 忽略。不要把密码、`loginToken`、`sessionKey`、Cookie、签名或验证码发送给其他人，也不要提交到仓库。

默认统计已确认的订单池参数：

```dotenv
ATG_SORT_TYPE=1
ATG_ORDER_TYPE=4
ATG_PAGE_SIZE=10
```

完整的可选配置如下：

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `ATG_API_BASE` | `https://api.atg-game.com/app` | API 基址，必须使用 HTTPS |
| `ATG_SORT_TYPE` | `1` | 排序类型 |
| `ATG_ORDER_TYPE` | `4` | 订单池类型 |
| `ATG_PAGE_SIZE` | `10` | 每页数量，`1–1000`；默认沿用已确认的真实请求 |
| `ATG_REQUEST_DELAY_MS` | `150` | 分页请求之间的等待毫秒数 |
| `ATG_TIMEOUT_SECONDS` | `30` | 单次 HTTP 请求超时秒数 |
| `ATG_REPORT_DIRECTORY` | `reports` | 报表目录 |

## 使用

生成一次最新报告：

```bash
python3 atg_pool_report.py
```

临时覆盖非敏感筛选参数：

```bash
python3 atg_pool_report.py --sort-type 1 --order-type 4 --page-size 10
```

显示安全的进度信息：

```bash
python3 atg_pool_report.py --verbose
```

指定其他配置文件和输出目录：

```bash
python3 atg_pool_report.py --env-file private.env --output-dir snapshots
```

## 输出

终端会显示：

- API 返回的订单池总数和实际收集数量
- 订单金额合计、最低、最高、平均和中位数
- 收益合计及含收益金额合计
- 每个订单金额对应的订单数、占比和小计
- 分页抓取是否完整、抓取期间订单池是否发生变化

每次运行还会在 `reports/` 生成：

- `atg_pool_YYYYMMDD_HHMMSS.csv`：逐笔订单明细
- `atg_pool_YYYYMMDD_HHMMSS.json`：元数据、汇总、金额分布和订单明细

不完整快照的两个文件名都会带 `_partial`，防止离开终端上下文后被误当作完整数据。

CSV 字段包括订单号、订单金额、收益、含收益金额、费率、费用和 VIP 标志。JSON 还包含完整性、稳定性、已读取页数、观察到的 API 总数及金额分布。如果收益字段只在部分订单中出现，终端会明确显示覆盖数量，而不会把缺失值当成零。

报告中不会写入密码或可复用的会话信息。

## 退出码

- `0`：报告完整
- `1`：网络、API、数据格式或写文件错误
- `2`：本地配置错误
- `3`：已保存报告，但分页快照不完整

如果订单池在逐页抓取期间变化，报告会标记为“不稳定”。这是跨页读取实时订单池的正常可能性，不应将它当作同一时刻的原子快照。

## 常见问题

- `Configuration file not found`：先执行 `cp .env.example .env` 并填写账号。
- `ATG authentication failed`：检查手机号、密码和账户状态。认证响应会被脱敏，不会原样打印。
- HTTP 429/5xx 或临时网络错误：程序会有限重试；持续失败时稍后重跑。代理或 DNS 也可能影响连接。
- 退出码 `3` 或文件名含 `_partial`：API 的 `total` 与收集到的唯一订单数不一致。订单池可能在分页期间变化，可立即再运行一次。
- 签名或数据格式错误：网站可能更新了接口契约。程序不会猜测其他接口或绕过安全控制。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用本地假响应，不需要真实账号，也不会访问 ATG API。

## 安全边界

程序只允许访问以下两个固定路径：

```text
POST /auth/login
POST /pay/debit/task
```

它不会绕过 Cloudflare、验证码、登录或访问控制。若服务端修改接口或签名规则，程序会明确失败，不会改用其他端点猜测。
