# PenguinPay 渠道接入设计

日期：2026-08-20

## 目标

在现有多渠道订单池系统中接入 `penguinpay`。手动查询和每 20 分钟运行的独立定时脚本均调用所有已启用渠道，统一输出订单数、INR 订单金额、五个固定金额区间，以及当前账号可只读取得的 UPI、银行卡和已领取订单收款账户。

PenguinPay 仅增加渠道身份、受信任域名和配置。登录、签名、有界重试、稳定总数分页、账户采集和标准 `ProviderResult` 映射复用已完成的 `walletquota/providers/signed_pool/` 共享协议层。

## 已确认的产品行为

- `penguinpay` 可与 `atgpay`、`millerpay` 一起加入 `ENABLED_PROVIDERS`。
- 手动查询和定时脚本使用同一注册表和 `.env`，因此自动包含 PenguinPay。
- PenguinPay 抓取稳定 `total` 对应的完整订单池，而不是只统计第一页。
- 每个渠道和综合结果均输出订单数、金额总和以及五个固定金额区间。
- 尽力读取绑定 UPI、绑定银行卡和已领取订单详情中的收款方账户。
- 单渠道失败不会阻止其他渠道和综合报告生成。
- 绝不领取订单，不付款、不提交 UTR、不取消任务、不绑定或修改账户。
- IST 日界线后继续只清理本工具拥有的过期报告快照，不扩大删除范围。

## 已验证的 PenguinPay 契约

2026-08-20 使用用户授权账号进行了有限真实只读检查。凭据、Token、Session Key、签名、完整账户标识和原始认证响应没有写入项目或报告。

- Web Origin：`https://app-web.penguinpay-app.com`
- API Base：`https://api.penguinpay-app.com/app`
- 登录：`POST /auth/login`，手机号和密码登录，无 OTP 步骤。
- 登录数据字段：`loginToken`、`sessionKey`、`userId`。
- 签名：请求体加入 `ts`、`userId`，按键名排序后拼接 `key=value`，末尾拼接 `sessionKey`，计算 MD5 并放入 `Signature` 请求头。
- 订单池：`POST /pay/debit/task`，请求参数为 `page`、`size`、`sortType`、`orderType`、`ts`、`userId`。
- Deposit 页面默认 `sortType=1`、`orderType=1`、`size=10`。
- 订单池响应为 `data.total` 和 `data.list`；真实订单字段包含 `orderNo`、`orderAmount`、`income`、`balance`、`inrRechargeRate`。
- 检查时服务端报告 49 单，第一页返回 10 单；设计不依赖该动态数量。
- 绑定 UPI：`POST /user/upi/bind/list`，`accountType=1` 和 `accountType=2` 均返回数据字段 `availableCount`、`bindUpiList`、`unavailableCount`。
- 银行卡：`POST /bank/debit/card/list`，响应数据字段为 `list`、`total`。
- 已领取任务：`POST /pay/my/task`，响应数据字段为 `list`、`total`。
- 订单详情：`POST /pay/order/detail`。
- 当次真实检查时，UPI、银行卡和已领取任务列表均为空，因此非空账户行和订单详情映射必须使用本地模拟响应覆盖。未来如果真实字段与共享映射不兼容，仅记录安全警告，不损害已完整取得的订单池。
- 认证失效业务码与共享协议一致，客户端只对 `1001`、`1002` 执行一次重新登录。

前端还声明了 `/user/account/wallet`、`/pay/debit/upis` 以及多个写接口。它们不属于本次已确认的订单池与三类账户输出契约，因此不加入运行时白名单。

## 方案选择

采用“现有共享 `signed_pool` 协议核心 + PenguinPay 薄适配器”。

没有选择复制 MillerPay 实现，因为这会复制签名、重试、分页和账户映射，使安全修复需要维护多份。也不采用任意动态渠道配置，因为它会弱化“代码中明确绑定渠道名、受信任主机对和六个精确路径”的安全边界。

## 架构

### PenguinPay 适配器

新增 `walletquota/providers/penguinpay/`，包含：

- `spec.py`：定义 `provider=penguinpay`、固定 API Base、Web Origin、默认 `sortType=1`、`orderType=1` 和六个只读路径。
- `config.py`：解析和验证 `PENGUINPAY_*` 配置，隐藏密码并只允许非敏感运行时覆盖。
- `client.py`：向共享 `SignedPoolClient` 注入 PenguinPay 设置与规格，不重写签名或网络逻辑。
- `provider.py`：向共享 `SignedPoolProvider` 注入 PenguinPay 身份和固定安全错误语义。
- `__init__.py`：只导出 PenguinPay 对外 Provider。

### 共享与注册层

`walletquota/providers/signed_pool/spec.py` 的显式受信任主机表新增：

```text
penguinpay -> api.penguinpay-app.com + app-web.penguinpay-app.com
```

不修改共享登录、签名、分页、账户采集和报告算法。在显式 Provider 注册表中新增 `penguinpay`；聚合器、Runner、报告层、手动 CLI、定时器和保留模块继续只依赖标准模型，不增加 PenguinPay 分支。

每个渠道持有独立客户端、Cookie 和 Session 状态，不在渠道或线程之间共享认证材料。

## 数据流

1. 手动 CLI 或定时脚本读取 `.env` 和 `ENABLED_PROVIDERS`。
2. 注册表按配置顺序构建 `atgpay`、`millerpay`、`penguinpay` Provider。
3. Runner 并发运行渠道，每个渠道使用独立会话。
4. PenguinPay 登录后逐页读取 `/pay/debit/task`。
5. 仅当观察到的 `total` 稳定且去重后订单数完全一致时，订单池才是完整成功。
6. 渠道依次读取绑定 UPI、银行卡、已领取任务，并只对已领取任务读取订单详情。
7. 共享层映射为带 `provider=penguinpay` 的 `Order`、`Account` 和 `ProviderResult`。
8. 聚合器只将完整成功渠道纳入 `total`；报告层输出终端摘要、`aggregate.json` 和每渠道 CSV。
9. 定时脚本沿用现有 IST `:00`、`:20`、`:40` 运行时点和受控报告目录清理逻辑。

## 配置

`.env.example` 增加下列 8 个 PenguinPay 键：

```dotenv
PENGUINPAY_PHONE=
PENGUINPAY_PASSWORD=
PENGUINPAY_API_BASE=https://api.penguinpay-app.com/app
PENGUINPAY_SORT_TYPE=1
PENGUINPAY_ORDER_TYPE=1
PENGUINPAY_PAGE_SIZE=10
PENGUINPAY_REQUEST_DELAY_MS=150
PENGUINPAY_TIMEOUT_SECONDS=30
```

默认综合示例使用：

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay,penguinpay
```

API Base 可在 `.env` 中显式填写，但必须为 HTTPS、受信任精确主机、默认 443 端口和 `/app` 路径。不能把 Provider 指向任意域名或其他路径。

密码字段不参与 `repr`。实际手机号和密码只保存在被 Git 忽略的本机 `.env`，不写入示例、测试、规格、提交记录或报告。已启用 PenguinPay 但缺少凭据时，只返回 PenguinPay 渠道级配置错误，其他渠道仍正常运行。

## 订单池完整性

- 验证每页 `total`、`list`、订单号和有限非负金额。
- 在 PenguinPay 渠道内按 `orderNo` 去重；跨渠道唯一性由 `(provider, order_id)` 保证。
- 使用有限最大页数，防止异常 `total` 、重复页或空页造成无限请求。
- 仅当不同页观察到的 `total` 稳定，且实际收集数与之一致时，状态才为完整成功。
- 不完整快照保留安全错误和部分数据，但不计入可信 `total`。
- 分页等待、传输重试和 `Retry-After` 继续使用现有有界策略；每次重试重新生成严格递增的时间戳和签名。

## 账户采集

采集顺序与现有共享协议一致：

1. 查询绑定 UPI。
2. 查询绑定银行卡。
3. 分页查询当前账号已领取任务。
4. 对已领取任务逐个查询详情。
5. 映射为 `bound_upi`、`bank_account`、`order_payee_upi` 或 `order_payee_bank`。
6. 用包含 Provider 名的确定性摘要生成缺失的账户 ID 并去重。

普通账户数据错误或某个附加接口暂时失败时，保留已完整取得的订单池并记录不包含原始响应的固定警告。客户端重新登录一次后仍发生的认证失败必须传播为渠道认证错误，不能降级成普通账户警告。

## 故障隔离与安全边界

- 缺少凭据返回固定 `configuration` 错误。
- 登录失败或一次重新登录后仍失效，返回固定 `authentication` 错误。
- 响应结构或字段无效，返回不包含完整响应的安全 `api` 或 `data` 错误。
- HTTP 超时、限流和暂时性服务端错误使用现有有界重试。
- Runner 对渠道异常做最后隔离，不泄漏原始异常或凭据。

客户端只允许下列六个精确路径：

- `/auth/login`
- `/pay/debit/task`
- `/user/upi/bind/list`
- `/bank/debit/card/list`
- `/pay/my/task`
- `/pay/order/detail`

任何未列出路径在发出网络请求前被拒绝。明确不允许 `/pay/receive/task`、`/pay/submit/utr`、`/pay/cancel/task`、绑卡、解绑、删除、更新或其他写接口。

日志和报告继续递归过滤密码、Token、Session、Cookie、签名、Authorization、API Key、Credential 和原始认证响应。终端账户标识脱敏；完整 UPI、卡号和 IFSC 只保存在本机 `aggregate.json`，不写入渠道 CSV。

## 输出、调度与保留

终端和 JSON 沿用现有结构：

```text
total:
  订单数、总金额、五档金额分布

atgpay: success
  订单与账户摘要

millerpay: success
  订单与账户摘要

penguinpay: success
  订单与账户摘要
```

每个非错误渠道输出订单数、金额总和、五个固定金额区间和账户摘要。`total` 只汇总完整成功渠道。渠道 CSV 新增 `penguinpay_orders.csv`。

手动查询保持 `python3 wallet_quota.py`。独立定时器保持 `python3 wallet_quota_scheduler.py`，启动后立即运行一次，之后在 IST 每小时 `:00`、`:20`、`:40` 运行；退出后仍由人工重启，不新增 Codex、cron、launchd 或开机自启集成。

现有保留模块只依据受控报告根目录、快照所有权标记、规范路径和 IST 日期删除本工具拥有的过期导出。PenguinPay 不引入新删除路径，也不触碰源码、`.env`、日志或其他用户文件。

## 测试策略

开发遵循测试先行，自动化测试使用本地 fake transport，不访问真实支付接口，不使用真实凭据。

- 规格测试：受信任 API 主机、Web Origin、HTTPS、默认 443、`/app` 路径和六接口白名单。
- 安全测试：错误主机、非 443 端口、userinfo、额外路径、query、fragment 和写路径在传输前拒绝。
- 配置测试：8 个 `PENGUINPAY_*` 键、必填凭据、默认 `1 / 1 / 10`、有限数值范围和密码脱敏。
- 客户端测试：PenguinPay Origin、未签名登录、签名请求、重试重签、一次重登录和六个只读方法。
- 订单测试：空池、单页、多页、重复订单、不稳定总数、缺失可选字段和异常金额。
- 账户测试：非空 UPI、银行卡、已领取任务详情、Provider 隔离、去重、普通失败降级和持续认证失败传播。
- Provider 和注册表测试：正确 `penguinpay` 身份、固定安全错误、三渠道配置顺序和单渠道缺配置隔离。
- 报告端到端测试：`total`、`atgpay`、`millerpay`、`penguinpay`顺序，INR 五档对账，终端账户掩码，JSON 保留完整账户，CSV 排除账户，认证字段递归删除。
- 手动与定时测试：三渠道使用同一 `.env`，定时器不改写它，不打印凭据。
- 回归测试：ATGPay、MillerPay、调度器、报告和 IST 保留行为不退化。

全部自动化测试通过后，才使用授权账号执行一次有限真实只读 smoke test。该检查只调用六个白名单端点，不保存或打印认证材料。

## 完成标准

- `penguinpay` 可通过本机 `.env` 启用，并同时出现在手动和定时综合结果中。
- PenguinPay 稳定完整订单数、金额合计和五个金额区间严格对账。
- 绑定 UPI、银行卡和已领取任务收款账户在非空模拟契约中正确映射、去重和脱敏；真实空列表不被解释为渠道没有账户。
- ATGPay 和 MillerPay 现有公开入口、默认参数、安全边界和输出不退化。
- 单渠道失败不会阻止其他渠道或综合报告生成。
- 客户端只可访问六个精确只读路径，不存在接单、UTR、取消任务或资金相关写调用。
- 报告和日志不包含认证秘密，完整账户敏感信息只存在本机综合 JSON。
- 手动查询、20 分钟定时查询、IST 受控清理和全部自动化测试通过。
