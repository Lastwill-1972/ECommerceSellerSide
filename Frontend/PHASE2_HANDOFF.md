# 前端第二阶段恢复与验收记录（2026-10-17）

状态：**中断后已恢复；第二阶段源码、静态检查及真实浏览器验收已完成。** 完成交付概况见 [README.md](README.md)。下方保留中断时的交接记录作为历史，原待办不再是当前任务。

已完成：MERCHANT_ADMIN 商家资料、店铺列表、店铺与仓库申请审批、只读订单列表/详情；双商家隔离、角色拒绝、身份失效、审批与并发、精度及只读行为浏览器验收；`npm run lint`、`npm run typecheck`、`npm test` 和 `npm run build`。

最终源码自动检查：lint 无警告，typecheck 通过，Vitest **13 个文件、38 项测试通过**；build 通过，主 JS bundle 1,271.04 kB（gzip 405.12 kB）、CSS 384.98 kB（gzip 54.15 kB），Vite 的 500 kB 提示保留。第二阶段最后一个退出提示修正只经过自动组件测试，测试数据库清理后未再做真实浏览器复测；此前真实 401 过期提示已在浏览器验收。

根代理已清理专用数据库 `test_frontend_phase2_92761e61`、fixture 用户及 API8012/Vite5174 测试进程；确认无残留端口监听。后端 101 文件 baseline 未变；订单、订单明细、库存和库存流水数据哈希与验收前一致。fixture 文件仅保留历史定位元数据，其 `status` 标记为 `cleaned`，不要把旧 PID 或库名当成可用资源。

## 原中断交接记录（历史）

以下记录说明额度中断时的状态和后续恢复线索，不代表当前未完成事项。

当时状态：用户因额度不足明确要求中断；后续已由上文所述恢复并完成验收。

## 中断时的续做清单（历史，已处理）

1. 先读本文件、Frontend/README.md、实际源码和后端 OpenAPI，保留已有工作，不重新搭建工程。
2. 用户长期要求后续实现使用 Luna 子代理；本阶段子代理 `/root/frontend_phase2_luna` 已被根代理 interrupt。恢复时可 followup 或新建 Luna 子代理，先检查其实际状态。
3. 先运行 `npm run lint`、`npm run typecheck`、`npm test`、`npm run build`，修复尚未核实的问题，再冻结代码做最终验收。**本次中断前没有收到本阶段完整检查成功结果。**
4. 完成下面的审批/资料写入、双商家浏览器和身份变更验收，更新 README 后停止，不进入商品/库存/其他角色页面。

## 范围与环境

- 根目录 `D:\ECommerceSellerSide`，前端 `Frontend`，后端 `backend`；Windows PowerShell。
- 只实现 MERCHANT_ADMIN：商家资料、店铺列表、店铺创建审批、仓库授权审批、订单列表/详情只读。
- 后端 head `20261017_phase12`、20 表、46 业务 API。禁止修改后端业务/配置/迁移和真实开发数据，不访问 seller。
- 复用 Vue3/Pinia/Axios/json-bigint；所有 ID 字符串，金额原十进制字符串，naive DATETIME 不转 UTC。
- Node 24.16.0/npm 12.0.1，后端 `.venv` Python 3.14.2/MySQL 9.7.2。不要输出 `.env`、JWT 或数据库密码。

## 已实现但仍需最终检查

- `/merchant` 概览、`/merchant/profile`、`/merchant/shops`、`/merchant/applications`（店铺/仓库两类切换）、`/merchant/orders`、`/merchant/orders/:id`；商家页面懒加载。
- `src/modules/merchant/api.ts`、`useMerchantScope.ts`、页面和 ReviewQueue；扩展 shared/types、auth scopeEpoch、路由守卫、AppShell 菜单/面包屑及 Dashboard 入口。
- 角色守卫重新 GET /auth/me，仅 MERCHANT_ADMIN 允许，MerchantFrame 实时身份 ready 后失去资格转403。
- 资料页不虚构 GET /merchants/me：初始未读取，PATCH 成功才显示服务端名称。
- 申请批准只发送 decision；驳回 trim 后按 Unicode 码点 1–2000。仓库批准弹窗解释授权不等于库存任意修改权限。提交 busy 防重复、弹窗 boolean modelValue。
- 请求结果有 scope/序号隔离；需进一步审查全部异步 catch/submit/详情/筛选晚响应是否安全。
- 新测试：router.spec.ts、merchant/api.spec.ts、ReviewQueue.spec.ts、MerchantProfilePage.spec.ts、MerchantOrdersPage.spec.ts 等。新增 Vue Test Utils/jsdom，vitest 改 jsdom 环境。
- README 已开始写 phase2，但下方 **5文件12测试/旧 bundle/5173 浏览器结果均是第一阶段历史，绝不是本阶段全量结果**。需要完成后重写准确总结。

## 真实 API 契约

仅 PATCH /merchants/me；GET /shops；GET /shops/applications；POST /shops/applications/{id}/review；GET /inventory/access-applications；POST /inventory/access-applications/{id}/review；GET /orders；GET /orders/{id}，均 /api/v1 前缀。

- PATCH name 长度1–255，返回 id/name，无 GET me。
- 列表 `{items,limit,offset,total}`，limit1–100，offset0–100000；申请支持 status=PENDING/APPROVED/REJECTED，订单支持 status=PENDING/COMPLETED/CANCELLED 及 shop_id。
- 店铺申请含 merchant_id/shop_name/applicant/reviewer/result shop/time；仓库申请没有 warehouse_id/name 或 merchant_id，不要编造。
- 订单带历史名称/规格/单价/小计/总额快照；商家管理员只有读取权限。
- **真实越权响应：跨商家店铺审批403；跨商家仓库审批404；跨商家订单详情404；商家订单完成/取消403。**

## 隔离测试资源（保留，未清理）

- 专用库 **test_frontend_phase2_92761e61**，已迁移到 phase12；只有本任务测试数据。开发库没有写入。
- 详细非秘密 fixture 元数据在 `PHASE2_TEST_FIXTURE.json`（用户名和字符串ID，无密码/Token）。原始31账号：13基础角色账号+18分页店铺申请人。
- A商家 `9007199254741001`，B商家 `9007199254741002`。A21店铺，B2店铺。A有待批准/驳回开店申请、待批准/驳回仓库申请；B有独立待审申请。
- A订单 `9007199254745001` PENDING（总额3703703670.57，单价1234567890.19×3），`9007199254745002` COMPLETED；B订单 `9007199254745003`。
- 临时测试密码没有写入交接/README。恢复时如无法取回内存测试凭据，仅在该专用测试库为已确认的 fixture 账号设置新随机测试密码并正常登录，或安全重建测试数据；不能借此修改开发账号。
- **中断时已停止本任务的测试 API8012和Vite5174**，避免无人操作时继续测试；测试库保留供续做。原开发 API8000（本阶段开始PID19924）未动。旧测试 PID13000及Vite session78467不再可信，恢复时重新检查端口/进程。
- 恢复 API：以现有 Settings 构建仅覆盖 DB_NAME=test_frontend_phase2_92761e61、APP_ENV=test、随机 AUTH_JWT_SECRET 的子进程环境，绑定127.0.0.1:8012。不得写 backend/.env。
- 恢复前端测试：独立 PowerShell进程 `$env:VITE_DEV_PROXY_TARGET='http://127.0.0.1:8012'; npm.cmd run dev -- --host 127.0.0.1 --port 5174 --strictPort`，不改本地环境文件。
- 本轮最初 test_frontend_phase2_490e1c59 因测试脚本把仓库跨租户404误期望403而重建，**该旧库已删除**。仅保留92761e61，不要误操作其他库。

## 已完成的真实验收（仅这些）

- 通过现有真实 API 注册/审批构造双商家、店铺、待审申请、仓库授权、商品/库存和3订单；这些只是隔离 fixture，未新增前端业务范围。
- HTTP：A只能读自身shops；跨商家店铺审批403、仓库审批404、订单详情404；商家完成/取消订单403。
- 浏览器（IAB，本轮tab2，地址5174，不能盲信旧句柄）：A真实登录进入 /merchant；A列表仅自身21店，下一页和刷新恢复身份正常；A订单仅2条，大ID/金额精确保留；详情历史快照且无写按钮；直接输入B订单地址显示无权/不存在；CANCELLED筛选显示空状态。
- 注意少数 getByText exact waitFor 超时来自文本节点包含链接/标题不同，随后AX明确核实页面正确，不要将其当作产品接口失败。

## 尚未执行/必须继续

- 最终 lint/typecheck/Vitest/build、真实已安装依赖检查、bundle分块大小记录。当前eslint10是本阶段开始的真实清单，Luna表示未主动升级；不要仅凭第一阶段历史9版本回退。新增测试依赖现在可能用 ^ 范围，审查是否应固定。
- 真浏览器 PATCH名称；批准和驳回店铺申请，成功后刷新列表/申请，A店数21→22；仓库批准警示/审批状态和驳回原因；重复点击及409/422反馈。
- B真实登录验证独立列表/申请/订单，与A切换后不残留A数据。
- 实时身份失去MERCHANT_ADMIN后路由拒绝；平台/店主/员工/NONE不能菜单或直接地址进入。禁用401保持第一阶段行为。
- 订单状态/店铺筛选；更多分页/空状态和错误重试；浏览器Vue error/warn清零。
- 审查scope跨身份变化及同租户快速筛选乱序，尤其submit后额外load/403分支；审批页Unicode最大长度与接口一致。
- 保存phase2截图（不覆盖phase1）、验证后端源码基线未变、清理专用库和账号、停止测试服务并可恢复普通预览，再README最终总结。

## 基线与收尾

- `PHASE2_BACKEND_BASELINE.json`：本阶段开始101个后端文件SHA256。完成时对比，不能把哈希检查当后端测试。
- `PHASE2_FRONTEND_BASELINE.json`：开始时前端文件SHA256，用于精确变更清单。
- `PHASE2_TEST_FIXTURE.json`：恢复fixture定位用，无秘密。
- 用户明确要求中断，本文件创建后停止开发。用户说继续时从此处恢复，不自动进入下一阶段。
