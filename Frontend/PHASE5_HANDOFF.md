# 电商前端第五阶段交付（2026-10-18）

**店铺负责人和员工订单工作台已完成，本阶段停止。** 以下为本阶段实测结果；后续历史章节仅代表当时状态。DeepSeek 负责实现、测试与浏览器操作，主代理核验并修正代码、执行受控验收；未使用 Luna。

### 1. 交付范围

本阶段实现店主（SHOP_OWNER）与店铺员工（SHOP_EMPLOYEE）两类订单页面：

| 角色 | 路由 |
|---|---|
| SHOP_OWNER | `/shop/orders`、`/shop/orders/create`、`/shop/orders/:id` |
| SHOP_EMPLOYEE | `/employee`（概览）、`/employee/catalog`、`/employee/inventory`、`/employee/orders`、`/employee/orders/create`、`/employee/orders/:id` |

下列 API 统一带 `/api/v1` 前缀。订单页面接入 5 个真实 API：`POST /orders`、`GET /orders`、`GET /orders/{id}`、`POST /orders/{id}/complete`、`POST /orders/{id}/cancel`。创建订单请求体仅含 `items[{sku_id, quantity}]`，并携带 `Idempotency-Key`，不含价格与租户参数。列表响应为 `{items, limit, offset, total}`，查询使用后端支持的状态与分页参数；订单状态为 `PENDING/COMPLETED/CANCELLED`；商品名称、编码、规格、单价、小计与总额使用服务端历史快照，金额保持十进制字符串。

### 2. 关键约束落实情况

- **只读边界**：员工仅可浏览目录与库存，不可管理；商家订单页保持只读。
- **数值与时间**：BIGINT 以精确 JSON 数字传递；数量在单 SKU 上为 `1..9223372036854775807`，单笔订单内 `1..100` 且为不同 SKU；无浮点金额；naive 时间按原文展示。
- **UUID 与幂等**：UUID 仅用于新操作；`sessionStorage` 按 user/merchant/shop 隔离，并保留 unknown 原键、按参数白名单存储，不存 JWT；只有用户明确点击重试时才用原键和原参数 POST，不自动重试。
- **状态回显**：取消订单返回后显示 `CANCELLED`；complete/cancel 结果未知时只做 GET 确认；仅 `PENDING` 状态显示操作按钮。
- **防旧响应**：以 scope epoch/token/user/merchant/shop/role + 序号防止旧响应覆盖；弹窗同样校验。
- **记录丢失处置**：记录丢失时由人工查订单流水，不承诺存储不丢。
- **新增文件**：`orders` 目录 6 个 source（api、pendingOrder、OrderCreatePage、OrderListPage、OrderDetailPage、EmployeeHomePage）与 6 个 spec（orders-api、pendingOrder、OrderCreatePage、OrderPages、roleGuard、EmployeeRead）。
- **修改文件**：router、AppShell、DashboardPage、ShopReadPage、merchant/test-support/elementPlus.ts、README。
- **QA 资产**：脚本 `qa/phase5_env.py`、`qa/phase5_checks.py`；产物 `PHASE5_ACCEPTANCE`、`VERIFICATION`、`HTTP_RESULTS`、`TRIGGER_RECHECK`、`TEST_FIXTURE`、`OPENAPI JSON`；文档三张截图。
- **依赖与后端**：无新增依赖，无 backend 变更，116 处 hash 一致；OpenAPI 与 head `20261017_phase12`（20 tables）一致。

### 3. 构建与测试结果

- lint、typecheck、test、build 全部通过（exit code 均为 0）。
- 测试：26 文件、145 通过、0 失败、0 跳过（原 99 新增 46）。
- Vitest + MemoryRouter 存在不完整路由 warn，实际浏览器 Vue warn/error 为 0。
- 产物大小（括号内为 gzip）：`main` 151.63 kB（51.43 kB）；含 preload 初始 JS 427.73 kB（上阶段 424.80 kB）；CSS 390.02 kB（55.15 kB，不变）；`store` 153.22 kB、`table` 94.53 kB；`create` 21.05 kB（7.19 kB）、`detail` 11.35 kB（4.13 kB）懒加载。未调高阈值。

### 4. 真实 MySQL 与 HTTP 验证

- 真实 MySQL 目标 `orders, orders_migrations, phase12`：首次 15 pass / 2 fail，原因为 MySQL 1419 触发器 SUPER 权限；受控本机管理连接仅在临时库重跑两例（2 pass），不改全局设置；最终 17 unique 通过。此为订单相关验证，非 backend 全量。
- 13 组 HTTP 通过：共享库存/隔离、抢 1 库存 201+409、取消回补 1、multiSKU 回滚、改价下架历史保持、旧 JWT 移除失权、再入职、交接等。
- 浏览器验证：店主创建与完成；员工未知重试显示 cancelled；complete/cancel 实际成功但丢响应后经 GET 确认；未授权库存 403 且保留身份；移除旧 JWT 后导航 403；晋升新 shop 不显示旧订单。
- 代理验证：同 UUID 两次 POST，两次请求后端均返回 201；代理向浏览器先返回 503、再返回 201，两次请求参数 SHA 相同；仅 order 6 一单 1 扣减 1 回补；complete/cancel 各仅 1 次 POST。
- 环境清理：MySQL 9.7.2 隔离库（8 users、2 merchants、4 shops）库账号已删；端口 8018/8019/6179 已关闭；浏览器已关闭；开发库未改。另有 Starlette 弃用 warning。

截图：`docs/phase5-order-completed.png`、`docs/phase5-order-replay.png`、`docs/phase5-order-cancelled.png`。

### 5. 未覆盖项

未跑 backend 全量，未做生产/跨浏览器全窄屏矩阵。

---

## 停止记录

阶段 5 完成并停止。验收依据：lint/typecheck/test/build 全通过（145 通过，0 失败）；订单相关真实 MySQL 17 unique 通过；13 组 HTTP 通过；浏览器与代理核验通过；无新增依赖，无 backend 变更，OpenAPI 一致。已清理隔离库与端口。未跑 backend 全量与全窄屏矩阵，记录丢失仅可人工查订单流水。
### 6. 文件定位与验收证据

新增源码（均位于 `src/modules/orders/`）：`api.ts`、`pendingOrder.ts`、`OrderCreatePage.vue`、`OrderListPage.vue`、`OrderDetailPage.vue`、`EmployeeHomePage.vue`。测试为 `src/modules/orders/__tests__/orders-api.spec.ts`，以及 `src/modules/orders/` 下的 `pendingOrder.spec.ts`、`OrderCreatePage.spec.ts`、`OrderPages.spec.ts`、`roleGuard.spec.ts`、`EmployeeRead.spec.ts`。

修改文件：`src/app/router.ts`、`src/shared/layout/AppShell.vue`、`src/modules/dashboard/pages/DashboardPage.vue`、`src/modules/workspace/pages/ShopReadPage.vue`、`src/modules/merchant/test-support/elementPlus.ts`、`README.md`。另新增本阶段 QA 脚本、JSON 证据、截图及 [停止记录](PHASE5_HANDOFF.md)。QA 脚本复用本机 MCP 的受控临时库工具，不是应用运行依赖；响应丢失代理只用于测试，不提供新业务 API。

- [真实 OpenAPI 快照](PHASE5_OPENAPI.json)、[前端与清理核验](PHASE5_VERIFICATION.json)。
- [13 组 HTTP 验收](PHASE5_ACCEPTANCE.json)、[浏览器请求及响应丢失代理记录](PHASE5_HTTP_RESULTS.json)。
- [两项触发器故障注入复测](PHASE5_TRIGGER_RECHECK.json)、[已清理测试环境信息](PHASE5_TEST_FIXTURE.json)。
- 完整工具日志在本机 MCP runs：前端检查 `5997d3906ed64af9a75f6a7030802278`，首次目标后端测试 `4573c81577b54a07873625f300197d01`。

### 7. 权限、异常与后续边界

店主和员工可操作本店订单，包括本店其他员工创建的订单；商家管理员保持现有只读权限。前端守卫每次受保护导航重新获取真实身份，服务端继续裁决所有授权。员工不提供分类、SKU、库存调整、人员或审批管理入口。商品与库存复用现有只读 API，不新增公开店铺查询；订单创建只选择 ON_SALE 商品，实际库存和价格由下单事务决定。

列表与详情包含加载、空数据、重试和分页；401 处理登录失效，403/404 显示真实访问错误，409 保留业务冲突反馈，422 显示输入错误，503/网络中断按写请求结果未知处理。创建结果未知时冻结原参数；完整确认后才允许开始下一笔。完成或取消需要确认，成功后重查订单与库存；结果未知只读取状态，避免重复写操作。员工仓库资源返回 403 时显示申请授权说明，不擅自把有效员工身份清空。

自定义岗位不影响订单权限；不信任 JWT 中永久保存的身份，也不信任客户端租户字段。历史订单展示快照，不从当前商品信息重建；商品改价或下架不改变已生成订单。本阶段未开发平台管理、支付、物流、订单删除或员工管理新功能。

剩余风险：浏览器关闭、存储清理或存储损坏仍可能丢失待确认记录；缺少按请求键查询的独立 API 时必须人工核对历史订单/流水，不得直接认定失败后换键重下。共享 Element Plus CSS 仍较大；未验证生产部署、所有移动端尺寸及跨浏览器矩阵。若继续下一阶段，先审核上述交付与存储恢复体验，再明确范围；本次不自动扩展。

