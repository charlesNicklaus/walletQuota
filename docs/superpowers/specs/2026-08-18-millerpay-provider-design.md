# MillerPay 渠道接入设计

日期：2026-08-18

## 目标

在现有多渠道订单池系统中接入 `millerpay`。手动查询和每 20 分钟运行的独立定时脚本都调用 ATGPay 与 MillerPay，统一输出订单数、订单金额、五个固定金额区间，以及当前账号可只读取得的 UPI、银行卡和已领取订单收款账户。

本次接入同时把 ATGPay 与 MillerPay 共有的登录、签名、重试、分页和账户采集能力下沉为内部共享协议层。两个渠道仍保留独立配置、域名、安全白名单、默认业务参数和 Provider 名称。

## 已确认的产品行为

- 每次手动查询调用全部已启用渠道。
- 现有 20 分钟定时脚本使用同一渠道注册表，因此自动包含 MillerPay。
- MillerPay 抓取完整订单池，而不是只统计第一页。
- 每个渠道和综合结果均输出订单数、金额总和及固定金额区间。
- MillerPay 读取绑定 UPI、绑定银行卡，以及当前账号已领取订单的收款方账户。
- 一个渠道失败不会阻止另一个渠道生成结果。
- 不自动领取订单，不付款、不提交 UTR、不绑定或修改账户。
- 当天 IST 结束后的既有保留策略继续只清理本脚本导出的报告数据。

## 已验证的 MillerPay 契约

2026-08-18 使用用户授权账号进行了有限的真实只读契约检查。凭据、Token、Session Key 和原始认证响应没有写入项目或报告。

- Web Origin：`https://app-web.millerpay-app.com`
- API Base：`https://api.millerpay-app.com/app`
- 登录：`POST /auth/login`，手机号与密码登录，无 OTP 流程。
- 登录数据包含 `loginToken`、`sessionKey` 和 `userId`。
- 签名：请求体加入 `ts`、`userId`，按键名排序后拼接 `key=value`，末尾拼接 `sessionKey`，计算 MD5 并放入 `Signature` 请求头。
- 订单池：`POST /pay/debit/task`，使用 `page`、`size`、`sortType`、`orderType`、`ts` 和 `userId`。
- MillerPay Deposit 页面默认 `sortType=1`、`orderType=1`。
- 订单池响应为 `data.total` 和 `data.list`；当前订单包含 `orderNo`、`orderAmount`、`income`、`balance` 和 `inrRechargeRate`。
- 绑定 UPI：`POST /user/upi/bind/list`。
- 银行卡：`POST /bank/debit/card/list`。
- 已领取任务：`POST /pay/my/task`。
- 订单详情：`POST /pay/order/detail`。
- 认证失效使用业务码 `1001` 或 `1002` 表示。

真实检查时账户列表和已领取任务为空。这证明接口可正常调用，但字段映射仍需使用本地模拟响应覆盖非空情况。

`/user/account/wallet` 虽然由前端调用，但不属于本次订单池与收款账户输出范围，因此不加入运行时白名单。

## 方案选择

采用“共享协议核心 + 渠道适配器”。

没有选择完整复制 ATGPay Provider，因为两个站点的认证、签名、分页和账户接口协议高度一致，复制会导致安全修复和兼容性修复需要维护两份。也没有把 MillerPay 当作 ATGPay 的第二个实例，因为这会让 ATG 的名称、默认 `orderType=4`、环境变量和错误语义泄漏到 MillerPay。

共享层只表达已经验证相同的协议。渠道域名、Origin、Provider 名、默认参数和允许路径由各渠道显式提供，不通过任意动态配置扩展到未知主机或未知接口。

## 架构

### 共享协议层

新增内部包 `walletquota/providers/signed_pool/`，包含：

- `spec.py`：不可变渠道协议规格，保存 Provider 名、API Base、Origin、允许路径和默认业务参数。
- `client.py`：Cookie 会话、登录、签名、有限传输重试、一次重新登录和安全 API 错误分类。
- `pool.py`：订单解析、稳定总数检查、全量分页、去重和完整性判断。
- `accounts.py`：绑定 UPI、银行卡、已领取任务与详情的只读采集、映射和去重。
- `provider.py`：把订单池与账户采集结果转换为现有 `ProviderResult`。

共享层不读取 `ATGPAY_*` 或 `MILLERPAY_*` 环境变量，也不自行选择域名。它只接收经过渠道配置验证的设置和不可变协议规格。

### 渠道适配器

ATGPay 保留现有对外入口和类名。`AtgPayProvider` 改为向共享协议层提供：

- `provider=atgpay`
- ATG API Base 和 Web Origin
- ATG 默认 `sortType=1`、`orderType=4`
- ATG 自己的配置和安全错误上下文

新增 `walletquota/providers/millerpay/`，向共享层提供：

- `provider=millerpay`
- `https://api.millerpay-app.com/app`
- `https://app-web.millerpay-app.com`
- 默认 `sortType=1`、`orderType=1`
- `MILLERPAY_*` 配置和 MillerPay 错误上下文

两个适配器分别拥有不可变的客户端实例和 Cookie/Session 状态，不在线程之间或渠道之间共享认证信息。

### 注册表与现有核心

在显式 Provider 注册表中加入 `millerpay`。聚合器、报告层、手动 CLI 和定时调度器继续只依赖标准 `ProviderResult`，不添加 MillerPay 分支。

现有 `Order`、`Account`、金额区间、报告格式和保留策略无需改变。MillerPay 当前没有返回的 `vip`、手续费等订单属性保持可选，不影响核心统计。

## 数据流

1. CLI 或定时脚本读取 `.env` 和 `ENABLED_PROVIDERS`。
2. 注册表按固定顺序构建 `atgpay` 与 `millerpay` Provider。
3. Runner 并发运行渠道，各渠道持有独立客户端与会话。
4. 渠道登录后全量分页读取 `/pay/debit/task`。
5. 渠道依次读取绑定 UPI、银行卡和已领取任务；只对已领取任务读取订单详情。
6. 共享层把响应映射为带正确 Provider 名的 `Order` 与 `Account`。
7. Provider 返回标准 `ProviderResult`；单渠道异常由 Runner 隔离。
8. 聚合器计算 `total`，报告层生成终端摘要、`aggregate.json` 和各渠道订单 CSV。
9. 定时脚本沿用现有 IST 日界线和受控报告目录清理策略。

## 配置

`.env.example` 增加：

```dotenv
ENABLED_PROVIDERS=atgpay,millerpay

MILLERPAY_PHONE=...
MILLERPAY_PASSWORD=...
MILLERPAY_SORT_TYPE=1
MILLERPAY_ORDER_TYPE=1
MILLERPAY_PAGE_SIZE=10
MILLERPAY_REQUEST_DELAY_MS=150
MILLERPAY_TIMEOUT_SECONDS=30
```

API Base 和 Origin 可以提供受验证的高级覆盖项，但覆盖值必须仍为 HTTPS，且主机必须精确匹配该渠道的受信任主机。不能通过 `.env` 把 Provider 指向任意域名。

密码字段不参与 `repr`。实际手机号和密码只放在被 Git 忽略的本机 `.env`，不写入示例、测试、规格、提交记录或报告。

已启用 MillerPay 但缺少凭据时，MillerPay 返回渠道级配置错误；ATGPay 仍正常运行并生成综合报告。

## 订单池完整性

- 请求第一页后读取 `data.total`，继续请求直到收集到总数或遇到明确完整性错误。
- 每页验证列表类型、订单字段、有限非负金额和订单号。
- 按 MillerPay 渠道内 `orderNo` 去重；跨渠道唯一性继续由 `(provider, order_id)` 保证。
- 验证不同页面观察到的 `total` 是否稳定。
- 使用有限最大页数，避免异常 `total` 或重复页面造成无限请求。
- 收集数量与稳定 `total` 一致时状态为完整成功；不一致时保留安全告警并按现有规则返回部分结果。
- 每个订单请求都重新生成时间戳和签名；重试与重新登录后的时间戳严格递增。

## 账户采集

账户采集复用现有统一 `Account` 模型，但所有映射都显式注入当前 Provider 名，避免生成 `atgpay` 前缀的 MillerPay 账户 ID 或警告。

采集顺序：

1. 查询绑定 UPI。
2. 查询绑定银行卡。
3. 分页查询当前账号已领取任务。
4. 对已领取任务逐个查询详情。
5. 映射为 `bound_upi`、`bank_account`、`order_payee_upi` 或 `order_payee_bank`。
6. 使用包含 Provider 的确定性摘要生成缺失的安全账户 ID，并去重。

普通账户数据错误或某个附加接口暂时失败时，保留已经完整取得的订单池并记录不含原始响应的安全警告。客户端重新登录一次后仍发生的认证失败必须向 Provider 传播为认证错误，不能降级成普通账户警告。

## 故障隔离与错误语义

- `atgpay` 与 `millerpay` 独立运行；一方失败不会取消另一方。
- HTTP 超时、限流和有限的暂时性服务端错误使用现有有界重试策略。
- 每次传输重试重新构造请求体、时间戳与签名。
- 业务码 `1001`、`1002` 触发一次重新登录并重放当前只读请求。
- 重新登录后仍认证失败，返回该渠道的 `authentication` 错误。
- 响应结构或数据字段无效，返回安全的 `api` 或 `data` 错误，不包含完整响应。
- 账户附加数据失败按上一节的降级规则处理。
- Runner 对未预期异常做最后隔离，整体状态为 `partial`，仍保存其他渠道结果。

## 安全边界

共享客户端只允许以下六个精确路径：

- `/auth/login`
- `/pay/debit/task`
- `/user/upi/bind/list`
- `/bank/debit/card/list`
- `/pay/my/task`
- `/pay/order/detail`

每个渠道的协议规格独立声明同一组只读路径；客户端在拼接 URL 前验证 HTTPS、精确主机和精确路径。任何未列出的路径均在发出网络请求前拒绝。

明确不允许 `/pay/receive/task`、支付、UTR、绑定、删除、更新或其他写接口。实现和测试均不能调用这些端点。

日志和报告递归过滤密码、Cookie、登录 Token、Session Key、签名、认证头、登录原始响应及可能嵌套在 metadata 或 attributes 中的敏感键。终端账户标识脱敏；完整账户只保存在当天本机 `aggregate.json`，不会写入渠道 CSV。

## 输出与保留

输出沿用现有格式：

```text
total:
  ...

atgpay: success
  ...

millerpay: success
  ...
```

每个渠道包含订单数、金额总和、五个固定金额区间和账户摘要。综合 `total` 只汇总完整成功渠道。

每次运行生成同一报告根目录下的独立快照，例如：

```text
reports/20260818_120000/
├── aggregate.json
├── atgpay_orders.csv
└── millerpay_orders.csv
```

手动和定时运行产生相同结构。现有保留模块根据报告根目录、所有权标记、路径解析和 IST 日期判断删除目标；MillerPay 不引入新的删除路径，也不触碰源码、`.env`、日志或其他用户文件。

## 测试策略

开发遵循测试先行，自动化测试使用本地 fake transport，不访问真实支付接口。

- 共享规格测试：渠道名称、HTTPS 主机、Origin、默认业务参数和路径白名单。
- 签名测试：排序、JS 标量序列化、严格递增时间戳、重试重签和重新登录后重签。
- 安全测试：未知路径、写路径、错误主机和非 HTTPS URL 在传输前被拒绝。
- MillerPay 配置测试：必填凭据、默认 `orderType=1`、数值边界和密码脱敏。
- MillerPay 订单测试：空池、单页、多页、重复订单、总数变化、缺失可选字段和异常金额。
- MillerPay 账户测试：非空 UPI、银行卡、已领取任务详情、去重、普通失败降级和持续认证失败传播。
- Provider 测试：正确的 `millerpay` Provider 名、标准模型、完整性元数据和安全错误分类。
- 注册表测试：同时启用两个渠道、单渠道缺配置、未知渠道和稳定输出顺序。
- 手动与定时端到端测试：综合结果同时包含 `atgpay`、`millerpay`，金额区间对账，账户终端脱敏、JSON 完整、CSV 排除。
- ATG 回归测试：现有类名、环境变量、默认 `orderType=4`、签名、分页、账户、报告和调度行为不变。
- 保留策略回归测试：IST 日界线后只删除脚本拥有的过期报告。

自动化测试全部通过后，才使用授权账号执行一次有限真实只读 smoke test。该检查只调用六个白名单端点，不保存或打印认证材料。

## 完成标准

- `millerpay` 可通过本地 `.env` 启用，并同时出现在手动和定时综合结果中。
- MillerPay 全量订单数、金额合计和五个金额区间相互严格对账。
- MillerPay 的绑定 UPI、银行卡和已领取任务收款账户正确映射、去重和脱敏。
- ATGPay 现有公开入口、默认参数和输出不退化。
- 单渠道失败不会阻止其他渠道或综合报告生成。
- 客户端只可访问六个精确只读路径，不存在接单或资金相关写调用。
- 报告和日志不包含任何认证秘密，账户敏感信息只存在于本机当天综合 JSON。
- 手动查询、定时查询、IST 清理和全部自动化测试通过。
