# 多商家多店铺 SaaS 电商管理平台 · 后端

当前 Alembic head 为 `20261017_phase12`，后端仍有 20 张业务表、46 个业务 API 和 health，包含认证与身份权限、商家/店铺/员工流程、商家目录、SPU 下架审批、店铺仓库授权、共享库存、人工订单及本阶段的商家资料和店主交接。第十二阶段没有新增表；此前各阶段总结保留为历史记录。
Python >=3.12，同步 SQLAlchemy 2.x、MySQL/PyMySQL、Pydantic v2、Alembic。

## 第十二阶段工作总结（2026-10-17）

本阶段新增 `20261017_phase12` 迁移（父版本 `20261017_orders`），未新增业务表或依赖。新增商家名称修改、店主自愿交接提议/确认、商家管理员强制交接，以及按当前身份和真实业务外键验证的审计查询。商家/店铺/员工/订单等现有授权关系是身份来源；请求中的用户 ID、审计 JSON 和旧 JWT 均不替代数据库关系检查。

### 三个 API 契约

所有请求都需要 Bearer token；请求模型拒绝额外字段。三个路由均复用现有商家/店铺路由与通用 API router，没有新增第九个业务模块。

1. `PATCH /api/v1/merchants/me`：当前商家管理员提交 `{"name":"新名称"}`，成功 HTTP 200 返回 `{"id":123,"name":"新名称"}`。名称会去首尾空白且不能为空，额外字段返回 422；只有数据库中当前 merchant admin 可改名。无有效令牌返回 401，角色不符返回 403，字段验证失败返回 422。
2. `PUT /api/v1/shops/{shop_id}/owner`：同一路径使用严格的 `action` 联合请求体。店主 `PROPOSE`：`{"action":"PROPOSE","target_user_id":123}`；成功 HTTP 200 返回完整 `{"shop_id":123,"owner_user_id":45,"status":"PROPOSED","proposal_id":"<UUID>","target_user_id":123}`。店主需把该 ID 交给商家管理员；没有额外 GET。商家管理员用 `CONFIRM`：`{"action":"CONFIRM","proposal_id":"<UUID>"}`，只能确认与当前 owner、店铺和目标绑定的、数据库真实保存的提议，不能代替 owner 提交同意。管理员可用 `FORCE`：`{"action":"FORCE","target_user_id":123,"reason":"..."}`；reason 去首尾空白、必填且最多 2000 字符。成功 HTTP 200 的响应字段始终为 `shop_id, owner_user_id, status, proposal_id, target_user_id`；CONFIRM/FORCE 的 `proposal_id` 为 null，status 分别是 `CONFIRMED` 或 `FORCED`。无有效令牌返回 401；非当前店主提议或非本商家管理员确认/强制返回 403；失效/重放提议或目标资格冲突返回 409；不合法字段/请求体返回 422。仅 active 且无冲突身份的 `NONE` 用户或本店员工可成为目标；拒绝平台管理员、商家管理员、其他店铺员工、已禁用和冲突身份。
3. `GET /api/v1/audit/events?limit=50&offset=0&event_type=...&start_at=...&end_at=...&target_type=...`：成功 HTTP 200 返回 `{items,limit,offset,total}`。每个 item 固定八个字段：`id, actor_user_id, event_type, target_type, target_id, outcome, event_data, created_at`。limit 为 1–100、offset 为 0–100000；`start_at`、`end_at` 只接受不带时区的 MySQL naive `DATETIME` 格式，应使用响应 `created_at` 同一数据库本地时间。MySQL `SYSTEM` 时区可能不是 UTC；为避免表面成功但筛选偏移，任一带时区值（包括只传一个或 aware/naive 混用）均返回 422。start 晚于 end 返回 422。无有效令牌返回 401；Employee/NONE 角色返回 403；无权限和格式错误的查询参数返回 403/422。过滤参数只能缩小结果，不能扩大调用者权限。

### 交接业务事实、事务与迁移

交接状态保存在 `shops` 新增列，没有 consent/proposal/history 单独新表。owner 自己调用 PROPOSE 才表示真实同意；proposal 使用不可预测 UUID，并绑定 from-owner 与 target；管理员 CONFIRM 必须带确切 UUID。CONFIRM 再检查当前 owner 仍 active 且仍是该店 owner。观察到原 owner 失去资格时，服务会在单独的新事务中锁定并清除过期同意，再返回冲突；账号后来恢复也不能复用旧同意。目标资格变化时请求拒绝，目标资格变化并不会伪造或替换 owner 同意。

角色迁移与 `ShopEmployee` 同事务完成。员工晋升为 owner 时删除其员工关系并保存该员工原 `role_id`。正常 CONFIRM 旧 owner 如有保存的本店岗位，恢复为精确原角色；数据库 `(shop_id, owner_restore_role_id) → shop_employee_roles(shop_id,id)` 复合 RESTRICT 外键防止恢复到别店角色。NONE 来源正常卸任后为 NONE。FORCE 明确采取安全撤权：旧 owner 的员工岗位不恢复，并清除该旧 owner 的恢复来源，避免强制替换悄悄重新授予店铺权限；若旧 owner 有平台、商家管理员、别店店主/员工等其他冲突关系，拒绝交接，不擅自清除这些身份。被禁用的旧 owner 仅允许 FORCE 移除，仍会检查其没有其他冲突关系。订单、店铺 merchant 归属、仓库授权和其他员工不变。已签发 JWT 的下次请求重新读取数据库关系，因此立即反映新身份；旧仓库授权申请仍走现有 service 的资格重查。

锁序按动作执行。PROPOSE 先锁 actor、当前 owner 和目标用户（去重后按 User ID 升序），再锁 Shop 与目标 Employee 关系行；此路径不锁 Merchant。CONFIRM/FORCE 先锁 actor、当前 owner、当前商家管理员及目标用户（CONFIRM 的目标取已 prepare 的提议用户，去重后按 User ID 升序），再依次锁 Shop、当前 Merchant、Employee 关系行。路由/Service 先读取 prepare scalar 后结束认证造成的自动事务，再在新写事务内依上述锁序重查身份，避免 MySQL RR 下旧快照授权。prepare 后身份/owner/proposal 变化则 409 重试，不反向补锁。子 Service 不 commit。迁移增加 `owner_restore_role_id`、待处理 proposal 三列，以及最近完成交接的类型、结果、理由、双方和时间；proposal 写入不覆盖最近已完成交接记录。正常成功、强制成功才更新最近完成记录并清空 pending。理由属于业务状态，与 ownership 更新同一事务保存；强制理由不会只依赖非原子的 audit event。

该列组仅保留**最近一次已完成交接**和**当前一个 pending proposal**，不构成完整历史。后续交接会覆盖最近快照，无法据此还原更早原因。`downgrade` 在任何 DDL 前扫描全部 phase12 状态；有恢复岗位、同意或交接快照时拒绝降级，避免丢失真实同意、岗位 provenance 或理由。CHECK 只接受全空、`NORMAL/COMPLETED` 且 reason 为空、`FORCE/FORCED` 且 trim 后 reason 非空三种完整状态；CHECK 全名通过 `op.f()` 遵守命名约定。

### 审计访问边界和已知历史事件路径

审计事件独立事务写入，失败只记录固定字段的安全日志并吞掉存储异常，不能翻转已完成业务响应；因此审计可能缺失，不是业务真相或完整法定历史。读取不把任意 `event_data` JSON 当租户凭证。`target_type + event_type + target_id` 必须匹配其实际业务表和租户外键/关联；返回字段为固定审计列，`event_data` 只在审计 outcome 与业务状态一致时由业务状态重建 `{decision: APPROVE|REJECT}`，否则 `{}`。reason、自由 JSON、JWT、密码、header 不返回。

当前 `AuditService` 全部 27 种事件的目标和租户关联如下。所有事件都要求真实 `target_type` 与 event type 配对；目录和订单等另外经实际模型外键关联 merchant/shop。删除员工后 `shop_employee_removed` 的 target 行已经不存在，事件 JSON 不能证明租户，因此该事件目前对 tenant 不公开。已批准并创建商家的 merchant application 可由 `created_merchant_id` 关联；未创建商家的申请对 merchant admin 不可验证，因此隐藏。平台的 SPU 下架审核只有 reviewed 事件可读，submitted 不在平台授权范围；reviewed 事件要求业务记录的 `reviewer_user_id` 与该事件的 `actor_user_id` 一致，不要求当前查询的 platform admin 本人就是审核者，因此所有平台管理员都可查询符合这一真实关联的治理审核事件。

| 事件 | target_type → 真实租户关联 | 平台管理员 | 商家管理员 | 店主 |
| --- | --- | --- | --- | --- |
| `merchant_application_submitted`, `merchant_application_reviewed` | `merchant_application` → 真实 MerchantApplication PK | 所有申请 | 仅 `created_merchant_id` 属于本商家 | — |
| `shop_application_submitted`, `shop_application_reviewed` | `shop_application` → `merchant_id` | — | 本商家 | — |
| `employee_application_submitted`, `employee_application_reviewed` | `employee_application` → `shop_id` → Shop.merchant_id | — | 本商家 | 本店 |
| `shop_employee_removed` | `shop_employee` → 删除后无行，JSON 不可信 | — | 不公开 | 不公开 |
| `shop_employee_role_created` | `shop_employee_role` → `shop_id` → Shop.merchant_id | — | 本商家 | 本店 |
| `shop_employee_role_changed` | `shop_employee` → `shop_id` → Shop.merchant_id | — | 当前仍存在的本商家员工 | 当前仍存在的本店员工 |
| `catalog_category_created` | `category` → merchant_id | — | 本商家 | — |
| `catalog_spu_created`, `catalog_spu_updated`, `catalog_spu_published` | `spu` → merchant_id | — | 本商家 | — |
| `catalog_sku_created`, `catalog_sku_updated` | `sku` → SPU → merchant_id | — | 本商家 | — |
| `catalog_offshelf_application_submitted`, `catalog_offshelf_application_reviewed` | `product_offshelf_application` → SPU → merchant_id | reviewer_user_id = event.actor_user_id 的 reviewed | 本商家 | — |
| `warehouse_access_application_submitted`, `warehouse_access_application_reviewed` | `warehouse_access_application` → shop_id → Shop.merchant_id | — | 本商家 | 本店 |
| `inventory_stock_adjusted` | `stock_movement` → merchant_id | — | 本商家 | — |
| `order_created`, `order_completed`, `order_cancelled` | `order` → merchant_id / shop_id | — | 本商家 | 当前本店 owner |
| `merchant_profile_updated` | `merchant` → 当前 merchant PK | — | 当前本商家 | — |
| `shop_owner_transfer_proposed`, `shop_owner_transfer_confirmed`, `shop_owner_transfer_forced` | `shop` → merchant_id / 当前 owner_user_id | — | 本商家 | 仅当前仍是 owner 的本店 |

Employee 和 NONE 无全量审计权限。平台管理员只看明确的平台管理事件（商家入驻申请、真实 reviewer 与 event actor 匹配的下架审核），不因此获得商家目录全量审计或下架提交事件。Tenant 历史关联缺失时默认不公开；不尝试根据不可信 event_data、JWT 或可复用 target ID 猜归属。

### 十项交付

1. 商家资料 PATCH 与名称严格验证；2. 单路径 PROPOSE/CONFIRM/FORCE 合同；3. owner 真实同意、随机提案和失效/重放保护；4. 原岗位来源记录与正常卸任精确恢复；5. FORCE 不恢复旧岗位并保存原因；6. Shop 列、CHECK、同店复合 FK 和拒绝危险 downgrade 的 phase12 migration；7. User→Shop→关系行锁序与数据库身份重查；8. 真实外键限定的审计查询、role matrix 和安全字段输出；9. 写审计失败不影响业务提交；10. MySQL 迁移/角色/审计/竞争/数据库故障回滚测试及本阶段文件说明。

### 五个 why

1. **为什么交接要由店主先提议？** 独立 PROPOSE 保存店主真实同意，管理员只能引用确切 proposal UUID 确认，不能替 owner 填同意。
2. **为什么 owner 与员工关系在同一事务中切换？** owner_user_id 变更、目标员工关系删除/恢复、岗位来源、proposal 消耗与最近交接快照一次提交或一起回滚，避免出现无 owner、双重 owner 身份或半完成的员工关系。
3. **为什么晋升员工时必须删除员工关系？** Shop owner 与 Shop employee 是互斥身份。晋升时保存确切 role_id 后删除 ShopEmployee；正常卸任可用同店复合外键精确恢复，不会留下同时拥有两种角色的关系。
4. **为什么 FORCE 不恢复旧 owner 岗位，并把 reason 放业务表？** 强制替换是安全撤权，恢复员工身份可能重新开放访问；强制原因保存在与所有权变更相同的业务事务，独立审计可能失败或丢失，不能作为唯一理由记录。
5. **为什么审计不靠 JSON target 身份？** JSON 可伪造、可缺失且删除后的 target 无法通过当前关系证明租户；只公开可按事件类型连到现存业务 FK 的记录。独立审计失败不翻转已经提交的业务响应，但审计可能缺行。

### Phase12 文件、验收与风险

新增：`alembic/versions/20261017_phase12.py`、`app/services/owner_transfers.py`、`app/services/merchant_profile.py`、`app/services/audit_query.py`、`app/core/audit_schemas.py`、`tests/test_phase12.py`、`tests/test_phase12_migrations.py`。修改：`app/modules/shops/models.py`、`app/modules/shops/schemas.py`、`app/modules/shops/router.py`、`app/modules/users/models.py`、`app/modules/merchants/schemas.py`、`app/modules/merchants/router.py`、`app/services/audit.py`、`app/api/router.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、`tests/test_orders_migrations.py`、本文档。没有修改既有 migration revision 或增加表。

根代理在 Python 3.14.2、MySQL 9.7.2 完成独立终验。20 张旧业务表均以真实关联 seed 数据执行 phase12 upgrade/downgrade/re-upgrade，逐字段保持；旧人工库存幂等键重放未改变既有数据。独立全量测试为 **109 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告，用时 52.85 秒**；`compileall`、`scripts.check_db` 和 Alembic metadata 检查均通过。真实 Uvicorn 验收三个新增 API 及相关既有流程，OpenAPI 核对总计 46 个业务 API；验证商家改名租户隔离、真实 owner 同意、员工晋升及精确岗位恢复、旧 JWT 立即反映身份、FORCE reason、既有订单字段/快照/仓库授权保持和审计范围。raw MySQL 验证跨店岗位复合 FK 拒绝码 1452、非法 CHECK 拒绝码 3819；存在交接数据时安全 downgrade 在 DDL 前拒绝且 schema/head 不变。开发库仅 upgrade 至 `20261017_phase12`，原有 20 张业务表及数据保持（当前各表为空）；`scripts.check_db` 确认 MySQL 9.7.2 连接成功；Alembic `current`/`check` 另行确认 head 及 metadata 一致。验收后根代理核对专用库为空且含 20 张业务表、版本表、无 trigger/view，再删除 `test_management_phase12_e41b1f73` 和 `backend/.env.test`；本地开发 `.env` 保留，`seller` 数据库未触碰。

限制：pending consent 和最后完成快照不提供完整 transfer history；后续交接会覆盖最后快照。审计写入可丢失，删除的员工事件因无法验证租户而隐藏。跨并发写操作使用较粗的用户/店铺锁，生产负载性能未评估。仅验证 Python 3.14.2 / MySQL 9.7.2；Python 3.12、MySQL 8.4 和生产负载未验证。没有实现前端、部署或性能优化。

## 第十一阶段工作总结（2026-10-17）

本阶段新增 `20261017_orders` 迁移（父版本 `20261017_inventory`），新增 `orders`、`order_items` 两张表，并在 `/api/v1/orders` 下提供创建、列表、详情、完成和取消五个 API。订单创建从当前授权身份推导店铺与商家，复核 SPU 正在销售、使用数据库中的 SKU 价格，并在同一事务中保存价格/名称快照、扣减共享库存及写入 `ORDER_DEDUCT` 流水。取消只允许 PENDING 订单，在同一事务回补库存并写 `ORDER_RESTORE`；完成不更改库存。没有实现支付、发货、退款或前端。

### 两张表、快照与约束

| 表 | 关键字段和数据库约束 |
| --- | --- |
| `orders` | BIGINT 自增主键；商家、店铺、创建人、状态、订单总额、必填请求幂等键与 SHA-256 哈希、数据库创建时间以及可空完成/取消时间。状态限于 `PENDING`、`COMPLETED`、`CANCELLED`；总额为非负 DECIMAL(38,2)；`(shop_id, request_key)` 及 `(merchant_id, id)` 唯一；`(merchant_id, shop_id)` 复合外键保证租户一致，操作人外键及全部外键 RESTRICT。列表索引为 `(merchant_id, created_at, id)`、`(shop_id, created_at, id)` 和 `(shop_id, status, created_at, id)`。 |
| `order_items` | BIGINT 自增主键；订单、商家、SKU、数量、下单单价 DECIMAL(12,2) 和行小计 DECIMAL(32,2)，以及 SKU code、SPU 名称和规格快照。数量为正 BIGINT、价格和小计非负；`(order_id, sku_id)` 与 `(id, merchant_id, sku_id)` 唯一；`(merchant_id, order_id)` 复合外键保证订单租户正确，`(merchant_id, sku_id)` 引用 SKU；外键均 RESTRICT。 |

单价列为 DECIMAL(12,2)、行小计 DECIMAL(32,2)、订单总额 DECIMAL(38,2)，不经 float；计算采用 Decimal `localcontext(prec=50)` 并在写入前检查可表示范围。每个订单最多 100 个 SKU；输入 SKU ID 与数量必须是严格正整数，拒绝重复 SKU，不静默合并或截断。商品、规格、单价、数量、行小计和总额都是下单时快照，后续目录改名、改价或下架不改写历史订单。数据库 CHECK 校验状态、数量、金额和流水来源/方向；raw SQL 测试覆盖租户复合外键、唯一键、CHECK 与 RESTRICT。

### 五个 API

均需 Bearer token，路径以前缀 `/api/v1` 开始，请求模型拒绝额外字段。只有 `POST /orders` 必须提供 `Idempotency-Key`；完成和取消无需 Idempotency-Key（仍需 Bearer 认证）或请求体，成功返回 HTTP 200。订单响应字段为 `id, merchant_id, shop_id, created_by_user_id, status, total_amount, created_at, completed_at, cancelled_at, items`；金额序列化为两位小数字符串。商品项字段为 `id, sku_id, quantity, unit_price, subtotal, sku_code_snapshot, spu_name_snapshot, specification_snapshot`。

1. `POST /api/v1/orders` 接受 `{"items":[{"sku_id":123,"quantity":2}]}`。店主或员工只能为自己的店下单；SKU 必须属于当前商家且对应 SPU 为 `ON_SALE`。成功返回 201 和订单及历史商品快照。缺少 Header 或请求体错误返回 422；非法幂等键、重复 SKU、库存不足或状态冲突返回 409；未知/跨租户 SKU 返回 404；无写权限返回 403。
2. `GET /api/v1/orders?status=PENDING&shop_id=123&limit=50&offset=0` 返回 `{items, limit, offset, total}`。默认 limit=50、范围 1–100，offset 范围 0–100000。店主/员工只能读取身份关联店铺，并且不能用 `shop_id` 扩大范围；商家管理员只读旗下订单，可选 `shop_id` 过滤。平台管理员和 `NONE` 无权访问订单。
3. `GET /api/v1/orders/{order_id}` 返回订单及快照商品项。其他店铺/商家的订单与不存在 ID 一样返回 404。
4. `POST /api/v1/orders/{order_id}/complete` 将授权店铺的 PENDING 订单改为 COMPLETED，不再次扣库存；终态订单不能重复完成或取消。
5. `POST /api/v1/orders/{order_id}/cancel` 将授权店铺的 PENDING 订单改为 CANCELLED，在事务内按历史数量恢复库存，即使 SKU 后来下架也可取消。若数量溢出，整个事务失败并保留 PENDING 和原库存。

幂等键允许可见 ASCII，最多 128 字符。SHA-256 规范内容包括 actor、merchant、shop 及按 SKU ID 排序后的规范 items；SKU 不接受重复或静默合并，订单最多 100 项。相同 `(shop_id, key)`、actor 和规范化 items 重试时，返回原订单 HTTP 201、当前订单状态和原历史快照，不检查当前销售状态、价格或库存，不重复扣减或审计。相同店铺同键但 actor 或规范化内容不同返回 409；不同店铺可使用相同键。并发同键由唯一键兜底，失败事务回滚后在新事务读取胜者，不在失败 Session 中继续查询。幂等已有记录时仅按 Order 主键做当前读并立即返回，不再申请库存锁。

### 事务、库存与审计

共享库存仍属于商家的唯一 Warehouse，不按店铺拆分。锁顺序为 `User（多用户按 ID 升序）→ Shop → Warehouse → SPU（排序）→ SKU（排序）→ Stock（排序）→ Order`。多 SKU 输入先规范化再稳定排序；创建、完成和取消在准备事务中捕获必要整数键后 rollback，再于新写事务先锁 User 并重查身份，避免认证依赖的 RR 旧快照。幂等预查不锁不存在的唯一索引范围，以免跨店首单时争用 gap lock；只对已找到订单的主键执行当前读。所有子服务不提交，订单、库存、流水在一个事务成功或一起回滚；复用 `inventory.lock_stock_row()` 和 `checked_quantity_after()`。

`stock_movements.operation_type` 增加 `ORDER_DEDUCT` 与 `ORDER_RESTORE`，新增可空 `order_item_id` 关联订单流水。CHECK 规定人工流水的幂等键和哈希都非空；订单流水二者都为 NULL，且扣减/恢复方向正确；`(order_item_id, operation_type)` 唯一，每个订单项最多一条扣减和一条恢复流水。订单项的 `(id, merchant_id, sku_id)` 候选唯一键支持流水复合外键。人工库存 POST 请求/响应字段不变，响应仍有六个字段且 `idempotency_key` 是必填字符串；原 `(merchant_id, idempotency_key)` 语义与历史人工 movement 行不变。库存流水 GET 响应新增可空 `order_item_id`。

订单成功提交后通过独立 Session 写审计；审计异常只写安全日志，不撤销已提交订单。审计不是与订单事务原子提交，可能缺行，订单和库存流水才是业务事实来源。订单流水和历史订单没有业务删除 API。迁移 downgrade 会在 DDL 前检查订单、订单项和订单流水；有订单历史时在线 downgrade 明确拒绝并保留 schema/数据。离线 `--sql` 无法执行该在线预检，必须先人工确认目标库没有订单历史。

### 八个 why

1. **为什么创建订单时立即扣减库存？** 本阶段将创建视为库存预留点，避免两个订单同时承诺同一份可用库存。
2. **为什么取消要在同一事务回补？** 订单状态、Stock 数量和恢复流水必须一起提交或一起回滚。
3. **为什么保存历史单价？** 目录价格会变，订单必须保留成交时的价格和行小计，不能用新价格重算历史。
4. **为什么商家管理员只能读订单？** 创建/状态变更属于店铺经营动作；商家身份只授予旗下订单查询权。
5. **如何证明并发下单不会超卖？** Stock 行锁后读取当前数量、检查边界并在订单事务内更新；多 SKU 固定排序避免相互反向抢锁。
6. **如何证明取消不会重复回补？** 只有 PENDING 可取消，Order 锁下再读状态后改为终态，同一订单至多成功恢复一次。
7. **多 SKU 为什么按固定顺序锁？** 所有订单先排序 SPU、SKU、Stock 再锁，避免输入排列相反时形成库存行锁环。
8. **流水和审计事件是什么关系？** `stock_movements` 与库存同事务，是数量变更的业务证据；独立审计是操作记录，事务外审计不可替代流水的原子性。

### 十三项交付

1. `orders` 和 `order_items` ORM 映射；2. 父版本为 `20261017_inventory` 的订单迁移及安全降级；3. 下单时 SKU/SPU 名称与规格快照；4. 数据库价的 Decimal 单价、行小计和总额；5. 严格正整数、多 SKU 去重和最多 100 项校验；6. 店主/员工创建订单及幂等请求；7. 店铺/商家范围订单列表与状态过滤；8. 订单详情和历史商品项；9. PENDING→COMPLETED 的单次状态迁移；10. PENDING→CANCELLED 与原数库存回补；11. 共享 Stock 扣减/回补及关联流水；12. 锁顺序、租户授权与独立审计；13. MySQL 迁移/约束/并发/回滚测试、端到端验收和本文档。

### 文件、迁移和验收

新增：`app/modules/orders/schemas.py`、`app/services/orders.py`、`alembic/versions/20261017_orders.py`、`tests/test_orders.py`、`tests/test_orders_migrations.py`。修改：`app/modules/orders/models.py`、`app/modules/orders/router.py`、`app/services/audit.py`、`app/services/inventory.py`、`app/modules/inventory/models.py`、`app/modules/inventory/schemas.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、`README.md`。根代理另新增 [PHASE11_HANDOFF.md](PHASE11_HANDOFF.md) 作为验收交接记录。没有修改八份历史迁移或增加依赖。

根代理在 Python 3.14.2、MySQL 9.7.2 完成独立终验。18 张旧业务表均有真实关联 seed；从库存迁移升级、降级、再升级后逐字段保持，旧人工流水与 SKU/Stock 时间戳保留；历史人工 key 重放返回原 before/after，不改变数据。raw MySQL 验证租户外键 1452、唯一约束 1062、CHECK 3819 和全部 RESTRICT；有真实订单/订单项/扣减流水时 downgrade 在 DDL 前拒绝且 schema/head/数据不变。真实 Uvicorn 覆盖注册、店铺与员工审批、仓库授权、下单、共享库存竞争、六路同键、反序多 SKU 两店并发、双取消、完成/取消竞争、缺货整单回滚、改价/改名/下架后的快照和重放、下架后取消、最大 Decimal 金额、权限/租户列表及审计。`python -m pytest` 为 **99 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告，用时 37.08 秒**；`compileall -q app scripts tests alembic` 通过。根代理仅将开发库升级到 `20261017_orders`，确认 20 张业务表和 43 个业务 API 加 health；测试服务停止，专用测试库及 `.env.test` 已删除，未访问 `seller`。

风险与限制：仅在 Python 3.14.2、MySQL 9.7.2 验证；Python 3.12、MySQL 8.4 和生产规模负载未测。Warehouse 粗粒度锁和订单列表 N+1 可能限制高负载吞吐。JSON BIGINT 大于 `2^53-1` 时 JavaScript 可能失去整数精度。独立审计非原子，不能保证零丢失；直接 DBA SQL 仍可修改历史，需限制数据库权限。含订单历史时在线 downgrade 安全拒绝，离线 SQL 无法在线预检。

## 第十阶段工作总结（历史，2026-10-17）

本阶段新增迁移 `20261017_inventory`（父版本 `20261017_warehouse_access`）、`stocks` 与 `stock_movements` 两张表，并在 `/api/v1/inventory` 下新增仓库读取、库存读取、流水分页、人工调整四个 API。为让数据库本身校验 SKU 租户归属，在 `skus` 增加仅供服务端使用的 `merchant_id` 并添加复合外键；SKU 客户端请求和响应契约不变。库存共享在商家唯一仓库，不按店铺复制数量。没有新增订单、支付、商品删除、盘点、调拨、采购单、Redis、消息队列、额外幂等表或依赖。

### 两张表和租户约束

| 表 | 关键字段和数据库约束 |
| --- | --- |
| `stocks` | BIGINT 自增主键；`merchant_id`、`warehouse_id`、`sku_id`、非负 BIGINT `quantity`、数据库默认创建/更新时间。唯一键 `uq_stocks_warehouse_sku (warehouse_id, sku_id)` 和供流水引用的 `uq_stocks_merchant_warehouse_sku (merchant_id, warehouse_id, sku_id)`；`ix_stocks_merchant_sku` 支持商家 SKU 查询；数量 `CHECK quantity >= 0`。`(merchant_id, warehouse_id)` 复合外键指向唯一 Warehouse，`(merchant_id, sku_id)` 复合外键指向 SKU，均为 RESTRICT。 |
| `stock_movements` | BIGINT 自增主键；商家、仓库、SKU、操作人、`operation_type`、有符号 `quantity_delta`、before/after 数量、幂等键、SHA-256 请求哈希和数据库创建时间。唯一键 `uq_stock_movements_merchant_idempotency (merchant_id, idempotency_key)`；`ix_stock_movements_merchant_sku_id` 支持商家 SKU 流水分页；仅允许 `MANUAL_ADJUSTMENT`、非零 delta、before/after 非负，并用 DECIMAL 转换后的 CHECK 验证 `before + delta = after`，避免 BIGINT 检查表达式本身溢出。复合外键分别确认仓库与 SKU 属于该商家、且该库存行确实存在；操作人外键和全部删除/更新外键均 RESTRICT。 |

`skus.merchant_id` 由所属 SPU 的商家回填，非空，带 `(merchant_id, id)` 唯一键和 `(merchant_id, spu_id) → spus(merchant_id, id)` 复合 RESTRICT 外键。Warehouse 的 `(merchant_id, id)` 父键沿用第九阶段。库存和流水都引用这些复合父键，数据库会拒绝“甲商家的商家键 + 乙商家的 SKU 或 Warehouse”，应用层也会从当前身份和真实 SKU→SPU 关系再次校验。`merchant_id` 不允许由 API 客户端提交。

租户复合外键所需父键索引为 `uq_spus_merchant_id_id` 和 `uq_skus_merchant_id_id`；SKU 商家查询索引为 `ix_skus_merchant_id`。库存使用 `uq_stocks_warehouse_sku`、`uq_stocks_merchant_warehouse_sku` 和 `ix_stocks_merchant_sku`；流水使用 `uq_stock_movements_merchant_idempotency` 与 `ix_stock_movements_merchant_sku_id`。

### 四个 API

均需 Bearer token，路径以前缀 `/api/v1` 开始。列表默认 `limit=50, offset=0`，limit 为 1–100，offset 为 0–100000；请求模型拒绝额外字段。

1. `GET /inventory/warehouse` 返回 `id, merchant_id, name, created_at`。商家管理员读取自己的仓库；店主和员工只有在其身份关系有效且所属店已获仓库授权后才可读取；平台管理员和 `NONE` 返回 403。
2. `GET /inventory/stocks` 返回 `{items, limit, offset, total}`。每项为 `sku_id, sku_code, specification, spu_id, spu_name, warehouse_id, quantity`。商家管理员读取本商家；店主/员工需先验证当前身份、店铺商家归属和该店实际授权。服务端固定商家范围，不接收或采用客户端提供的 `merchant_id`、`warehouse_id` 或 `shop_id`。同商家授权店铺读取同一份仓库库存。
3. `GET /inventory/movements?sku_id=123&limit=50&offset=0` 仅当前商家管理员可用；结果始终限定在身份对应商家。支持可选 SKU 筛选和同样的分页结构。每个流水项返回 `movement_id, sku_id, quantity_before, quantity_delta, quantity_after, idempotency_key, merchant_id, warehouse_id, actor_user_id, operation_type, created_at`；不会返回内部 `request_hash`。店主/员工即便已获仓库授权也返回 403。
4. `POST /inventory/operations` 仅允许当前 `MERCHANT_ADMIN`。请求体为 `{"sku_id":123,"delta":10}`，Header 必填 `Idempotency-Key`。delta 必须是严格 JSON 整数，不能是 bool、浮点数或字符串；范围是有符号 BIGINT，且不能为 0。成功返回 HTTP 200 和 `movement_id, sku_id, quantity_before, quantity_delta, quantity_after, idempotency_key`。客户端不能提交商家、仓库、操作人或目标库存。库存不足、溢出、同键不同请求和缺失仓库返回 409；非法幂等键返回 409；缺少 Header 和错误请求体返回 422；跨商家/未知 SKU 返回 404；其他岗位无写权限返回 403。

示例：

```http
POST /api/v1/inventory/operations
Authorization: Bearer <token>
Idempotency-Key: adjustment-20261017-001
Content-Type: application/json

{"sku_id":123,"delta":10}
```

```json
{"movement_id":501,"sku_id":123,"quantity_before":0,"quantity_delta":10,"quantity_after":10,"idempotency_key":"adjustment-20261017-001"}
```

响应时间字段由数据库生成。BIGINT ID 和数量目前以 JSON 整数返回；JavaScript/Vue 对超过 `2^53 - 1` 的数值无法精确表示。未来前端需要采用能保留整数精度的 JSON 解析与展示方式，本阶段按既有 API 类型没有改成字符串。

### 幂等、事务和锁顺序

幂等键作用域为 `(merchant_id, Idempotency-Key)`，最大 128 字符，只接受可见 ASCII `!` 到 `~`，拒绝空值、空格、首尾空白和其他字符；不会通过 trim 把不同输入静默合并。SHA-256 的规范 JSON 包含当前操作人、商家、Warehouse、目标 SKU、固定操作类型 `MANUAL_ADJUSTMENT` 和 delta。键本身有独立的数据库唯一约束，哈希不会替代唯一键。

事务在确认当前锁定的商家管理员、仓库和 SKU 租户关系后，先查询该商家是否已有此键；同哈希直接返回流水保存的原始 before/after，哪怕后续库存已经变化或已到零/上限，也不再检查当前增量可否重复应用、不改数量、不新建流水或审计。哈希不同返回 409。首次请求锁定库存行，检查非负和 BIGINT 上限，先插入流水再更新数量；两步都在同一个事务提交。若两个事务同时使用同键，数据库唯一键是最后保护：重复插入导致的 MySQL 1062 会先让失败事务退出并回滚，再通过新的 Session 查询原流水；绝不在失败 Session 里继续查。若并发胜者提交晚于幂等预查询，失败事务仍会在唯一冲突后读取胜者结果。

所有多行写入遵循 `users（多个用户时按 ID 升序）→ Shop（需要店铺范围时）→ Warehouse → SPU → SKU → Stock → StockMovement`。库存调整在准备只读身份事务中仅取得 SKU 的不可变 SPU ID，之后 rollback 并开启全新事务；第一把写锁是当前操作人 User。锁用户后才重新从数据库读取身份，避免 MySQL REPEATABLE READ 在等待用户锁后复用旧身份快照。调整接着锁 Warehouse、SPU、SKU、Stock，再插入流水。Catalog 创建 SKU 时也先锁商家 Warehouse，再锁 SPU；创建 SKU 和数量为 0 的 Stock 同事务，任一插入失败两者回滚。Catalog 其他已存在的 SPU→SKU 写入不再反向申请 Warehouse 锁。仓库授权 Service 的用户→Shop→Warehouse 子序列也不会和库存/Catalog 形成反向等待。

`app.services.inventory.lock_stock_row()` 和 `checked_quantity_after()` 为后续业务提供真实可复用的行锁与数量边界检查；它们不自行开始/提交事务，也不提供订单类型或订单 API。未来调用方需先遵守上述身份授权与全局锁序，并把自己的业务记录与库存变更放进同一事务。

### 历史数据迁移与独立审计

在线迁移在任何 DDL 之前先查找“SKU 无有效 SPU”或“SKU 所属商家没有唯一 Warehouse”；发现不合法数据时以包含 SKU/SPU 样本 ID 的明确错误停止，不猜测其他商家 Warehouse。SKU→SPU 商家关系已有外键，Warehouse 的 `merchant_id` 已有唯一约束。检查通过后新增可空 `skus.merchant_id`，按 SPU 商家回填（显式保留历史 `updated_at`），确认无空值，再改成非空、增加复合父键/外键，建立两张新表并按每个 SKU 商家的唯一仓库创建零库存行。测试覆盖失败发生在 DDL 之前、修复缺仓后升级成功、历史 SKU 数量为零。迁移没有改写任何旧 revision。

MySQL DDL 不具备事务回滚保证，因此部署时应先备份并处理不满足仓库前置条件的历史数据；在线检查能在迁移 DDL 前发现 SKU 缺 Warehouse。`alembic upgrade --sql` 离线输出仍包含数据回填 DML，但无法读取目标库执行上述在线预检，离线运行者必须先自行核对 SKU、SPU、Warehouse 关系。

库存成功提交后通过独立 Session 调用 AuditService 白名单事件 `inventory_stock_adjusted`，仅写操作人、商家、Warehouse、SKU 和流水 ID，不记请求头、幂等键、JWT、密码或哈希。独立审计失败只记录安全错误类型，库存结果和流水保持成功。独立审计不保证零丢失：进程可能在业务提交和审计提交之间退出，审计库故障也会缺少审计行；`stock_movements` 才是与库存变更同事务的核心业务流水。当前只有读取流水的 API，没有任何业务流水修改或删除 API；这不阻止拥有数据库权限的人直接改 SQL，部署时仍需限制生产数据库账户权限。

### 九个 why

1. **为什么库存调整必须有幂等键？** 客户端在超时后无法判断服务是否已经提交；重试用相同键可安全取得最初结果，不会重复加减。
2. **为什么不能只在 Python 里查询有无请求？** 并发事务可能同时查到不存在；`(merchant_id, idempotency_key)` 唯一约束由 InnoDB 原子裁决最后胜者。
3. **为什么数量和流水需要同一事务？** 单独提交任何一边都会留下无法解释的数量或历史；单次提交保证成功或一起回滚。
4. **为什么数量和 SKU 分表？** SKU 描述商品规格，Stock 是会变化的仓库状态；分表让并发数量锁、流水引用和 SKU 目录更新各自承担明确职责。
5. **为什么并发扣减会超卖？** 如果两个事务都用旧数量计算再直接写回，它们可能都认为余额足够；行锁使读取与更新按序执行，非负 CHECK 是数据库兜底。
6. **为什么库存读取和库存修改权限不同？** 仓库授权表示店铺获准使用所属商家仓库；本阶段仅开放共享库存读取，不授予人工调整或完整流水访问权。未来订单应按固定的商家仓库库存策略，在订单业务事务中锁定并检查库存。
7. **如何证明相同键不会重复扣减？** 首次事务必须写唯一幂等流水，且与数量更新原子提交；重试读回原结果，竞态中的第二个流水 insert 则违反唯一键并回滚。
8. **如何在 MySQL RR 避免丢失更新？** 请求在 User 锁后校验实时身份，随后对 Stock 执行 `SELECT ... FOR UPDATE` 当前读，在锁内读取、校验并写回数量。
9. **如何避免 SKU 创建与库存写入的反向锁序？** 两者共享用户、Warehouse、SPU 的顺序；SKU 创建与零库存初始化同一事务，并在取得 SPU 前先锁 Warehouse。既有只锁 SPU/SKU 的写入不再向后取 Warehouse。

### 文件、迁移和验收

新增：`app/services/inventory.py`、`alembic/versions/20261017_inventory.py`、`tests/test_inventory.py`。修改：`app/modules/catalog/models.py`、`app/modules/inventory/models.py`、`app/modules/inventory/schemas.py`、`app/modules/inventory/router.py`、`app/services/catalog.py`、`app/services/audit.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_catalog.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、`tests/test_offshelf_applications.py` 和本 README。没有修改历史迁移。

根代理在 Python 3.14.2、真实 MySQL 9.7.2 的专用库 `test_inventory_phase10_e9ae5870` 完成独立终验。原有 16 张业务表都含关联 seed 数据；升级、降到 `20261017_warehouse_access`、再升级后逐字段不变，旧 SKU 的 `created_at=2001` 和 `updated_at=2002` 保留，新增商家键正确且每个旧 SKU 获得 0 库存。缺 Warehouse 的 SKU 使迁移在 DDL 前失败，schema/head 未变化；补建仓库后迁移通过且 SKU 时间值不变。真实 Uvicorn HTTP 走完身份注册、管理员初始化、商家/店铺/员工审批和仓库授权；同商家两家授权店和员工看到同一库存。6 路同键只产生一条流水，8 路不同键增加无丢失，8 路并发 `-4` 有 3 成功、5 冲突且余额为 3。扣至 0 和增至 BIGINT 上限后，同一原请求重试均返回原响应；库存后来改变后重试仍返回原 before/after。跨租户、未授权、平台身份和员工写入按预期拒绝；请求/header 校验、SKU 更新后库存关联、没有流水修改/删除 API 均通过。真实约束检查覆盖跨租户、缺仓外键 1452，重复库存/幂等键 1062，非负、非零、操作类型、数量平衡 CHECK 3819，幂等键区分大小写、不同商家可复用同一键及所有外键 RESTRICT。

全量 pytest 最终为 **91 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告，用时 26.62 秒**；`compileall -q app scripts tests alembic`、`alembic check` 与 `scripts.check_db` 通过，7 份历史迁移 SHA256 与基线一致。根代理只将开发库 `ecommerce_saas_dev` 升级到 `20261017_inventory`，确认 18 张业务表、38 个业务 API 加 health、原 16 张表数据保持且本阶段两表为空；未访问 `seller`。测试服务已停止，专用测试库和任务 `.env.test` 已删除，原 `.env` 保留。Python 3.12、MySQL 8.4、生产规模负载和多 MySQL 版本仍未验证；数据库 BigInt JSON 精度和由 DBA SQL 直接更改流水的权限控制是已知部署事项。

## 第九阶段工作总结（历史，2026-10-17）

本阶段新增迁移 `20261017_warehouse_access`（父版本 `20261017_offshelf`）、两张业务表和 `/api/v1/inventory` 下三个仓库授权 API。更新现有 Warehouse ORM 和 inventory router；未添加库存数量、库存流水、订单或其他业务 API，没有新增依赖或架构层，也没有修改历史迁移。建店和员工流程不创建仓库授权；平台管理员也没有自动的商家仓库权限。

### 两张表与租户约束

| 表 | 字段、约束和用途 |
| --- | --- |
| `warehouse_access_applications` | 自增 BIGINT `id`；必填 `shop_id`、`applicant_user_id`，分别 RESTRICT 引用 `shops.id` 和 `users.id`；区分大小写的 `status` 只接受 `PENDING`、`APPROVED`、`REJECTED`；可空 `reviewer_user_id`、`rejection_reason`、`reviewed_at`；数据库默认 `created_at`。内部 STORED generated `pending_shop_id` 仅在 PENDING 时等于 `shop_id`，否则为 NULL；其唯一约束保证同店最多一份待审申请。内部生成列不出现在 API。 |
| `warehouse_accesses` | 自增 BIGINT `id`；`shop_id` 全局唯一；必填 `warehouse_id`、`merchant_id`、`granted_by_user_id` 和数据库默认 `granted_at`。`(merchant_id, shop_id)` 复合外键引用 `shops(merchant_id, id)`，`(merchant_id, warehouse_id)` 引用 `warehouses(merchant_id, id)`；审核人引用 `users.id`。所有外键均 RESTRICT。`merchant_id` 是服务端从店铺读取的租户键，不由客户端提交。 |

为使 MySQL 能引用复合外键，迁移在父表 `shops` 和 `warehouses` 分别新增显式唯一键 `(merchant_id, id)`。它们与原有主键、商家店名唯一键和仓库 `merchant_id` 单列唯一键并存，不改变原表字段值或既有行。复合键是数据库强制“店铺与仓库属于同一商家”所需的父键。申请表另有 `shop_id`、`applicant_user_id` 和 `(status, id)` 索引以支持外键及列表过滤；授权表的 `merchant_id` 有显式索引，复合外键列也由 MySQL 保持可用的子表索引。降级先删除两张新表，再移除这两个新增唯一键；阶段九 MySQL 降级实测同时确认旧 `shops` 和 `warehouses` 外键依赖的索引仍可用，避免 MySQL 1553。

### 三个 API

全部需 Bearer token。路径前缀为 `/api/v1`。分页沿用 `{items, limit, offset, total}`，默认 limit 50、offset 0；limit 为 1–100，offset 为 0–100000。请求模型拒绝未声明字段。

1. `POST /api/v1/inventory/access-applications` 接受无请求体或严格空对象 `{}`，成功返回 201 和申请对象。只有当前数据库身份为店铺 `SHOP_OWNER` 才能申请；店铺和商家由当前身份关系推导，不能指定 `shop_id` 或 `warehouse_id`。服务端从该店铺所属商家查唯一 Warehouse。仓库尚不存在返回 409 且不创建申请；已有该店铺授权或已有待审申请返回 409。员工、商家管理员、平台管理员和 `NONE` 返回 403。响应字段为 `id, shop_id, applicant_user_id, status, reviewer_user_id, rejection_reason, created_at, reviewed_at`。
2. `GET /api/v1/inventory/access-applications?status=PENDING&limit=50&offset=0` 返回上述申请对象的分页。店主只看自己的店；当前商家管理员只看本商家的店；其他身份返回 403。可选 `status` 只接受三种申请状态，分页参数不能扩大租户范围；未知的租户 query 参数不参与范围计算。
3. `POST /api/v1/inventory/access-applications/{application_id}/review` 接受 `{"decision":"APPROVE"}` 或 `{"decision":"REJECT","rejection_reason":"补充仓库资料"}`。只允许当前数据库关系验证通过的该商家管理员。驳回原因必填、去除首尾空格后不能为空且最多 2000 字；批准时可省略 `rejection_reason` 或传 `null`，非 null 值返回 422。批准在同一事务中创建仓库授权并将申请改为 `APPROVED`；驳回只将申请改为 `REJECTED`、记录审核人和原因，不创建授权。批准会复核申请人仍是该店当前启用店主、审核人仍是本商家管理员、店铺与商家仓库关系仍一致且尚无授权。跨商家或未知申请为 404；非商家管理员无论 ID 是否存在均为 403；已处理申请为 409；格式错误为 422。

示例批准响应：

```json
{
  "id": 101,
  "shop_id": 17,
  "applicant_user_id": 42,
  "status": "APPROVED",
  "reviewer_user_id": 21,
  "rejection_reason": null,
  "created_at": "2026-10-17T09:30:00",
  "reviewed_at": "2026-10-17T09:35:00"
}
```

上例时间值仅作示意。驳回保留为历史申请。同店驳回后可提交新申请；旧申请仍为 `REJECTED`，不能再次审核或改写新申请。授权是店铺到该商家唯一仓库的关系，只用于后续服务通过 `app.services.warehouse_access.is_authorized(session, shop_id)` 查询真实的、租户一致的授权关系；它本身不授予查看库存、调整库存或写库存流水的权限。本阶段没有实现这些操作 API。

### 身份、锁、事务和审计

JWT 只标识当前用户，角色、店铺和商家均由 `IdentityService` 每次从当前数据库关系推导。提交时只接受当前店主。所有写入开始前先结束认证依赖触发的只读 Session 事务；申请审核先在独立只读事务读出不可变的申请人和店铺 ID，再 rollback，随后开启全新的顶层写事务，避免 MySQL REPEATABLE READ 在锁等待后保留旧快照。服务内不调用 `commit()`；由顶层 Service 的事务上下文提交业务变更。

锁顺序为涉及用户按 ID 升序、`Shop`、`Warehouse`、申请及授权行。提交在锁定用户后重查店主身份，锁店铺和仓库后检查同店授权和待审申请。审核锁定申请人、审核人后重查当前管理员和店主身份，再依次锁店铺、仓库、申请和授权。负责人或管理员关系在 MySQL 用户行锁等待期间改变时，锁后的当前关系决定是否允许操作。并发申请还受 Shop 行锁串行化和 nullable generated-column 唯一键保护；双重审批只能创建一条授权，`warehouse_accesses.shop_id` 唯一键是最终数据库约束。后续任何负责人或商家管理员关系变更都须遵守相同的用户行锁顺序。

申请提交、批准和驳回在业务提交后调用独立 Session 的 `AuditService`。审计事件只记录申请 ID、店铺 ID、仓库 ID、申请人或审核人整数 ID，以及固定的 APPROVE/REJECT 决策；驳回自由文本、任意请求字段和秘密均不进审计上下文。审计写失败仅安全记录事件、目标 ID 和异常类型，HTTP 业务成功与已提交授权仍保留。业务提交和独立审计不是原子事务，进程在两次提交之间中断或审计数据库不可用时可能缺少审计行；本阶段没有 outbox 或自动重试。

### 五个 why

1. **为什么店铺创建不自动授权仓库？** 授权是店主显式申请、商家管理员审核后建立的关系。把它附带到建店会跳过负责人知情和商家审核。
2. **为什么授权同时保存 `merchant_id` 并加复合外键？** `merchant_id` 只能从数据库中的 `Shop` 真实关系推导，不能相信 JWT 声明或客户端字段。两个各自合法的 ID 仍可能把甲商家的店连接到乙商家的仓库；复合外键让数据库也能拒绝这种错配。
3. **为什么仓库授权不等于库存操作权？** 授权只确认店铺使用所属商家仓库的关系。岗位和平台角色不会因此得到仓库权限，数量读取、调整及流水需要未来独立定义的策略和 API。
4. **为什么批准时再次核验店主和管理员？** 申请到审核之间两种身份关系都可能变化。审核事务在锁后读取数据库当前关系，防止失效的旧身份完成授权。
5. **为什么同时使用行锁和唯一键？** 同店请求在应用层按 Shop 行串行检查，generated-column 唯一键再阻止任何并发或绕过服务的重复待审申请；授权行的 `shop_id` 唯一键保证同店最多一个授权。

### 文件、迁移和验收

新增文件：`app/modules/inventory/schemas.py`、`app/services/warehouse_access.py`、`alembic/versions/20261017_warehouse_access.py`、`tests/test_warehouse_access.py`。修改文件：`app/modules/inventory/models.py`、`app/modules/inventory/router.py`、`app/modules/shops/models.py`、`app/services/audit.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py` 和 `README.md`。历史迁移没有修改。

Luna 和根代理在 Python 3.14.2、MySQL 9.7.2 上分别完成全量验收：最终 **84 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告**（根代理用时 22.50 秒）；`compileall -q app alembic tests scripts`、MySQL `alembic check` 均通过且无新差异。根代理在专用库 `test_warehouse_access_phase9_b3ed3d8b` 的原 14 张业务表中逐表写入真实关联标记记录，执行 upgrade→downgrade 到 `20261017_offshelf`→re-upgrade，14 张旧表逐行逐字段不变；检查新增 CHECK、generated nullable 列、待审与授权唯一键及两组复合外键。直接约束验证覆盖跨商家两种错配和不存在 Warehouse 的外键错误 1452、重复待审/重复授权错误 1062、小写状态 CHECK 错误 3819；SQLAlchemy `before_insert` 事件在真实 MySQL 连接上先更新申请为 `APPROVED`，再触发授权 insert 外键失败，确认事务回滚后申请仍为 `PENDING` 且没有授权。授权 helper 在无授权/有授权时分别返回 false/true。

根代理还在随机本地端口启动真实 Uvicorn，按注册、CLI 创建测试管理员、商家和店铺/员工审批流程建立数据库身份关系后实测三个授权 API。并发申请结果为 201/409，并发审核为 200/409；跨商家审核 404，平台管理员和员工访问受限接口 403，伪造 body 422，伪造租户 query 不扩大范围；驳回后重申请成功，五条审计事件落库。不存在任何未来库存操作路径。开发库 `ecommerce_saas_dev` 只升级到 `20261017_warehouse_access`，最终有 16 张业务表和 34 个 API，`current`/`check` 通过；原 14 张业务表都为空且值未变，两张新表也为空。六份历史迁移 SHA256 与验收前基线一致。本阶段创建的专用测试库及临时 `.env.test` 已在根代理终验后删除；本地 `.env` 和开发库保留，`seller` 未触碰。

限制：Python 3.12 和 Compose 声明的 MySQL 8.4 未实测；未验证生产规模负载或其他 MySQL 版本。仓库行锁会串行化同一商家的授权写操作。若商家数据缺失唯一 Warehouse，提交返回 409，管理员批准也不会创建仓库或授权；本阶段默认既有商家审批流程已创建仓库。手工 SQL 可绕过用户根行锁协议，但不能绕过外键、CHECK、待审唯一键和每店唯一授权键。库存读写、盘点、流水、幂等和订单均未实现。

## 第八阶段工作总结（历史，2026-10-17）

本阶段新增迁移 `20261017_offshelf`（父版本 `20261017_catalog`）和 `product_offshelf_applications` 一张业务表，并在 `/api/v1/catalog` 下增加申请、分页读取、平台审核三个 API。旧五条迁移、订单/库存、前端和其他业务模块均未修改；没有新增依赖或架构层。下架只改变 SPU 的目录状态，不删除商品、SKU、价格或申请记录。商家仍可通过现有 publish API 重新上架已批准的 SPU；本流程没有永久封禁。

### 申请表与数据库约束

| 字段 | 约束和用途 |
| --- | --- |
| `id` | 自增 BIGINT 主键。 |
| `spu_id` | 必填 BIGINT，外键到 `spus.id`，RESTRICT；有单列索引。商家归属只从 SPU 读取，不重复存 `merchant_id`。 |
| `applicant_user_id` | 必填 BIGINT，外键到 `users.id`，RESTRICT；有单列索引。申请人必须是该 SPU 当前商家管理员。 |
| `status` | 区分大小写，默认 `PENDING`；CHECK 仅接受 `PENDING`、`APPROVED`、`REJECTED`。 |
| `reviewer_user_id` | 可空 BIGINT，外键到 `users.id`，RESTRICT。 |
| `rejection_reason` | 可空 VARCHAR(2000)；只保存驳回原因，不进入审计上下文。 |
| `created_at`、`reviewed_at` | 数据库默认创建时间和可空审核时间。 |
| `pending_spu_id` | 内部 STORED generated 列：仅当状态为 `PENDING` 时等于 `spu_id`，其他状态为 NULL；不出现在 API。它带唯一约束，数据库可靠保证同一 SPU 至多一条待审申请，已驳回或批准的历史记录不阻止再次申请。 |

另有 `(status, id)` 索引支持待审/分页查询。外键删除和更新都使用 RESTRICT。MySQL 唯一索引允许多个 NULL，因此历史记录不占用待审 SPU 唯一键。应用还会在锁定 SPU 后检查待审记录；唯一键是并发写入的最终约束。

### 三个 API

以下路径均需 Bearer token，body 拒绝额外字段。列表沿用 `{items, limit, offset, total}`，默认 `limit=50`、`offset=0`，limit 为 1–100、offset 为 0–100000。响应申请对象字段为 `id, spu_id, applicant_user_id, status, reviewer_user_id, rejection_reason, created_at, reviewed_at, spu`；`spu` 只含 `id, name, status, merchant_id`，不含描述、SKU、价格或用户资料。嵌套 SPU 状态是读取时的当前值，不是申请时快照；因此商家合法重新上架后，历史申请仍为 `APPROVED`、当前 SPU 可为 `ON_SALE`，旧申请也不能再次审核或改写新申请。

1. `POST /api/v1/catalog/offshelf-applications` 接受 `{"spu_id": 77}`，成功返回 201 和待审申请对象。只有当前数据库身份为该 SPU 商家的 `MERCHANT_ADMIN` 可提交；SPU 必须处于 `ON_SALE` 且没有其他待审申请。跨商家或未知 SPU 返回 404，SPU 已下架或已有待审申请返回 409。
2. `GET /api/v1/catalog/offshelf-applications?status=PENDING&limit=50&offset=0` 返回分页。商家管理员只看自己商家的申请，可按 `PENDING`、`APPROVED`、`REJECTED` 过滤；平台管理员只能看到 `PENDING`。平台请求即使带 `status=REJECTED` 也固定筛选待审项，不会展开商品目录管理权限。店主、员工和 `NONE` 返回 403。
3. `POST /api/v1/catalog/offshelf-applications/{application_id}/review` 只允许当前平台管理员审核。批准 body 为 `{"decision":"APPROVE"}`；驳回 body 为 `{"decision":"REJECT","rejection_reason":"停售资料待补"}`。驳回原因必填、去除首尾空格后不能为空且最多 2000 字；批准请求省略原因，若填写非空原因返回 422。只允许审核 `PENDING` 申请；申请人不可自审。批准时再次确认申请人仍是该 SPU 的商家管理员且 SPU 仍为 `ON_SALE`；SPU 已下架或申请人的商家归属改变返回 409 并保持待审，申请人已停用或身份关系冲突返回 403。成功时在同一事务将 SPU 改为 `OFF_SALE`、申请改为 `APPROVED`。驳回将申请改为 `REJECTED`、保存原因，SPU 状态不变；即使申请人之后失效或身份变化，平台仍可驳回待审申请。非平台用户无论申请 ID 是否存在都返回 403；平台对未知 ID 得到 404，重复审核得到 409。

示例成功响应（时间字段由数据库生成，以下值仅为示例）：

```json
{
  "id": 101,
  "spu_id": 77,
  "applicant_user_id": 21,
  "status": "APPROVED",
  "reviewer_user_id": 4,
  "rejection_reason": null,
  "created_at": "2026-10-17T09:30:00",
  "reviewed_at": "2026-10-17T09:35:00",
  "spu": {"id": 77, "name": "日常运动鞋", "status": "OFF_SALE", "merchant_id": 9}
}
```

提交申请返回 `201` 的同一结构，状态为 `PENDING`、reviewer/reason/reviewed_at 为 null，SPU 状态为 `ON_SALE`；驳回响应将状态设为 `REJECTED` 并返回驳回原因，SPU 状态不变。GET 响应为分页对象，例如 `{"items":[<上述申请对象>],"limit":50,"offset":0,"total":1}`；平台列表只含待审项。

状态流转为 `PENDING → APPROVED` 或 `PENDING → REJECTED`。已结束的申请不能改写；驳回保持 SPU 原状态，若仍为 `ON_SALE` 可重新申请；批准后 SPU 为 `OFF_SALE`，商家合法重新上架后可再申请，形成新的历史记录。没有申请删除、SPU/SKU 删除或永久下架 API。

### 身份、事务、锁与审计

租户和角色从数据库关系推导，JWT 中的角色声明不可信。申请提交先锁申请人 `users` 行并复核其商家管理员身份，再锁 SPU、检查状态/待审记录并插入申请。审核准备阶段先在可回滚的只读事务读取申请中不可变的 applicant/SPU ID，然后结束该事务；新写事务按用户 ID 升序锁审核人与申请人，锁后复核平台身份，再按 `SPU → application` 顺序锁行并复核 ID、状态和资格。把准备读取与写事务分开，可避免 MySQL REPEATABLE READ 在等待用户锁后仍沿用旧身份快照。SKU 修改也遵守 `user → SPU → SKU` 顺序；SPU 修改、publish 和 SKU 创建都先锁用户再锁 SPU，因此审核与既有商品写入按相同顺序竞争，不形成反向锁序。

并发提交同一 SPU 由 SPU 行锁串行化，生成列唯一键提供数据库兜底；两个平台同时审核同一申请，只有一个能从 `PENDING` 提交，另一个返回 409。审核与 publish 竞态保留现有可重新上架行为：若 publish 先看到 `ON_SALE`，它返回 409，随后批准最终变为 `OFF_SALE`；若批准先下架，publish 随后可以成功重新上架，最终为 `ON_SALE`。两种结果都由 SPU 行锁排序决定。

提交、批准、驳回在业务事务提交后由 `AuditService` 的独立 Session 写入。只接受固定事件和严格整数 ID 白名单：target 是申请 ID，事件上下文包含 SPU ID 及提交人或审核人 ID；审核事件还记录 `APPROVE`/`REJECT`。不记录驳回自由文本、请求原文、价格或秘密。无效审计上下文不会打开审计 Session；审计数据库失败只记录事件类型、目标 ID 和异常类型，成功业务响应及业务行不回滚。两次提交不原子；若独立审计写入失败或进程在业务提交后退出，商品和申请已经提交但对应审计可能缺失。

### 五个 why

1. **为什么锁 SPU 行？** 申请检查、重复待审检查、平台批准、SKU 写入和 publish 都围绕同一个 SPU。共享行锁协议使这些状态变更按一个顺序提交，并与唯一生成列共同挡住并发重复申请。
2. **为什么审批下架而不物理删除？** 订单、商品售后和审计需要稳定的 SPU/SKU 引用与当时的商品信息。下架保留商品和 SKU；未来订单必须在订单事务中重新检查 `ON_SALE`，并保存合法订单所需的商品/价格历史快照。本阶段没有实现订单或快照。
3. **为什么平台管理员只能看待审申请，不能读普通目录？** 平台身份只承担有限审核职责。审核列表返回足够识别申请和 SPU 状态的信息，不扩大到商品描述、SKU、价格或商家日常管理数据。
4. **为什么保留已存在的 SKU 和价格？** 下架是 SPU 销售状态变化，不是目录擦除。SKU 与价格仍供商家之后重新上架使用，也避免本阶段破坏引用它们的业务记录；未提供物理删除入口。
5. **为什么审计独立提交？** 它复用项目现有“业务提交后独立审计”约定，避免审计数据库故障反向回滚已经成功的业务操作。代价是审计不是原子记录，失败时可缺失；本阶段没有 outbox 或重试投递。

### 文件、迁移与验收

新增：`app/modules/catalog/offshelf_schemas.py`、`app/services/offshelf_applications.py`、`alembic/versions/20261017_offshelf_applications.py`、`tests/test_offshelf_applications.py`。修改：`app/modules/catalog/models.py`、`app/modules/catalog/router.py`、`app/services/catalog.py`、`app/services/audit.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、`README.md`。五条历史迁移未改；无 DELETE API、库存、仓库授权、订单或前端改动。新迁移 SHA256 为 `a4686dc95b5e994c40ab008e9d3aaa917391e2e15471bccf2cb3ceeba8dd2c74`；五条旧迁移哈希保持不变。

Luna 和根代理都在专用 MySQL 9.7.2 测试库完成全量测试；最终结果 **73 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告**。`compileall -q app alembic tests scripts`、MySQL `alembic current` 和 `alembic check` 均通过。真实并发/HTTP 测试覆盖提交竞争、双平台审核、approve 与 publish 竞争、审核人用户行锁等待后复核身份、权限与跨租户、状态错误、失败 flush 双行回滚、SKU 价格保留、真实独立审计成功及审计故障响应。

根代理另在每张 13 张旧业务表都包含真实关联标记数据的 MySQL 库上升级到新 head，验证 STORED nullable generated 列、1062 待审唯一冲突、3819 大小写 CHECK 冲突、1452 外键冲突，以及 `REJECTED` 释放唯一槽后可新建待审申请。降级到 `20261017_catalog` 再升级后，13 张旧表每字段值均未变化；只有新申请表在降级时移除。MySQL DDL 非事务性，迁移中断后应先核对实际表结构再继续操作。

开发库 `ecommerce_saas_dev` 已仅升级到 `20261017_offshelf`；`current` 和 `check` 无差异。升级前 13 张旧表都为空且值未变，新增申请表为空；未创建开发平台管理员、未重建/降级开发库，`seller` 未触碰。阶段八专用测试库及 `.env.test` 已在根代理终验后删除；本地 `.env` 保留。环境为 Python 3.14.2 和 MySQL 9.7.2；声明最低 Python 3.12 和 Compose MySQL 8.4 未实测。

局限：业务与独立审计不是原子事务；申请/审核及 SPU/SKU 历史可由相应数据库行保留，但对申请表做显式 downgrade 会删除该表内的申请历史，独立审计行也可能因审计失败缺失。没有库存、仓库授权、订单、商品历史快照、审计 outbox/重试或物理删除功能。

## 第七阶段工作总结（历史，2026-10-17）

第七阶段只新增 `categories`、`spus`、`skus` 三张表和 `/api/v1/catalog` 下九个 API；迁移 `20261017_catalog` 的父版本是 `a6e73c92f014`。此前四条迁移和员工、审批、库存、订单等其他业务逻辑未修改，没有新增依赖或架构层。商品目录归商家所有，由同一商家的所有店铺共用；售价也在商家目录内统一维护。

### 三张表与数据库约束

| 表 | 字段和约束 |
| --- | --- |
| `categories` | 自增 BIGINT `id`、非空 `merchant_id`、`name`（最多 255 字符）、数据库默认 `created_at`。商家外键 RESTRICT；`(merchant_id, name)` 唯一；`(merchant_id, id)` 是 MySQL 复合外键所需的显式唯一键。 |
| `spus` | 自增 BIGINT `id`、非空 `merchant_id`、`category_id`、`name`（最多 255 字符）、可空 `description`（最多 10000 字符）、`status`（默认 `OFF_SALE`）、数据库默认 `created_at` 和自动更新 `updated_at`。状态 CHECK 只允许 `OFF_SALE`、`ON_SALE`。`merchant_id` 外键指向 `merchants`；`(merchant_id, category_id)` 复合外键指向 `categories(merchant_id, id)`，防止 SPU 引用另一商家的分类。 |
| `skus` | 自增 BIGINT `id`、非空 `spu_id`、`sku_code`（最多 100 字符）、手工录入 `specification`（最多 1000 字符）、非负 `DECIMAL(12,2) price`、数据库默认 `created_at` 和自动更新 `updated_at`。SPU 外键 RESTRICT；`(spu_id, sku_code)` 唯一；价格有数据库 CHECK。 |

目录对象都使用 RESTRICT 外键，没有物理删除代码。上架只检查 SPU 至少有一条现存 SKU；本阶段没有 SKU 删除、禁用状态或库存字段，因而 SKU 价格 CHECK 和存在性是当前“有效 SKU”的定义。上架不表示有库存。SKU 对应仓库的库存数量与库存流水属于未来库存模块，不参与这里的商品目录。

### 九个 API

所有请求均需有效 Bearer token，路径均以 `/api/v1` 为前缀。分类和 SPU 列表使用 `{items, limit, offset, total}`，默认 `limit=50`、`offset=0`，limit 为 1–100，offset 为 0–100000。请求模型拒绝未声明字段；body 中没有 `merchant_id`、`spu_id` 或状态可写字段。读接口会忽略未知的 `merchant_id` query 参数，始终用数据库推导身份限定商家；跨商家 SPU/SKU 查询按 404 处理以免泄露资源是否存在。

1. `GET /catalog/categories?limit=50&offset=0` 返回分类分页。分类对象字段精确为 `id, merchant_id, name, created_at`。
2. `POST /catalog/categories` 接受 `{"name":"鞋履"}`，返回 201 分类对象。当前商家内重名返回 409；请求不能选择商家。
3. `GET /catalog/spus?category_id=12&status=OFF_SALE&limit=50&offset=0` 支持可选分类和状态过滤，返回 SPU 分页。SPU 列表项字段精确为 `id, merchant_id, category_id, name, description, status, created_at, updated_at`。
4. `POST /catalog/spus` 接受 `{"category_id":12,"name":"日常运动鞋","description":"轻便鞋款"}`，description 可省略或为 null；返回 201 SPU 列表项，初始状态固定为 `OFF_SALE`。异商家分类返回 404。
5. `GET /catalog/spus/{spu_id}` 返回同样的 SPU 字段以及 `skus` 数组。SKU 对象字段精确为 `id, spu_id, sku_code, specification, price, created_at, updated_at`；空 SKU 返回空数组。
6. `PATCH /catalog/spus/{spu_id}` 只允许 `name`、`description`、`category_id` 中至少一个字段。例如 `{"description":null,"category_id":19}`。`name` 与 `category_id` 不能为 null；`status`、`merchant_id` 等额外字段返回 422。成功返回 SPU 列表项。
7. `PATCH /catalog/spus/{spu_id}/publish` 无请求体。至少存在一个 SKU 才能从 `OFF_SALE` 上架为 `ON_SALE`；无 SKU 或重复上架返回 409，成功返回 SPU 列表项。
8. `POST /catalog/spus/{spu_id}/skus` 接受 `{"sku_code":"SHOE-40","specification":"颜色黑，尺码40","price":"199.90"}`，返回 201 SKU 对象。编码在同一 SPU 下重复返回 409。价格在 API 响应中是保留两位小数的字符串，客户端应以十进制字符串提交。
9. `PATCH /catalog/skus/{sku_id}` 只允许 `sku_code`、`specification`、`price` 中至少一个字段，不允许改 `spu_id`；成功返回 SKU 对象。编码冲突返回 409。

输入校验拒绝负数、非有限金额、超出 10 位整数范围或超过两位小数的价格；最大值为 `9999999999.99`。金额使用 Python `Decimal` 与 MySQL `DECIMAL(12,2)`，不使用二进制 float 做金额运算。未认证或无效令牌返回 401；`NONE`、`PLATFORM_ADMIN` 以及试图写入的店主/员工返回 403；未知或跨租户资源返回 404；格式不符返回 422，重复业务操作返回 409。成功上架只表示目录状态为 `ON_SALE`，不代表任一店铺或仓库有库存。没有下架和删除 API；下架或商品删除须由后续审批流程处理，本阶段没有实现该审批。

### 身份、事务、审计与五个 why

目录专用授权策略允许 `MERCHANT_ADMIN` 读写自身商家商品；真实关联到该商家的 `SHOP_OWNER`、`SHOP_EMPLOYEE` 只读。`PLATFORM_ADMIN` 和 `NONE` 没有目录访问权。身份范围来自 `IdentityService` 和数据库关系，用户行锁后写服务会再查一次真实用户身份和商家；请求参数不能改变租户。所有店铺共享商家分类、SPU 和 SKU 售价。

路由在回滚认证依赖开启的只读 Session 事务前复制 actor ID；顶层 Catalog Service 再开始独立写事务，子服务不提交。SKU 创建与上架都锁所属 SPU，防止无 SKU 的 SPU 在并发下错误上架。业务成功提交后，既有 `AuditService` 用独立 Session 记录固定白名单事件：分类创建、SPU 创建/修改/上架、SKU 创建/修改；仅记录固定整数 ID 和结果，不写自由请求文本或秘密。审计失败保留业务成功并仅记录安全日志，业务提交与审计仍不是原子操作。

五个 why：①商品归商家、订单归店，因为店铺共用目录售价，而交易订单需要保留下单店铺和交易上下文；②分类的 `merchant_id` 标识分类所有者，SPU 冗余保存 `merchant_id` 便于直接按商家过滤，复合外键同时保证 SPU 与分类所有者一致，避免两处归属字段错配；③库存与 SKU 分离，避免目录定义冒充仓库级库存和流水；④客户端 `merchant_id` 不可信，租户必须从真实身份关系推导；⑤删除商品应走下架审批，避免擦除订单和审计所需的历史引用，本阶段只支持上架且未实现审批。

### 文件清单、迁移和验证

新增文件：`app/modules/catalog/schemas.py`、`app/services/catalog.py`、`alembic/versions/20261017_catalog.py`、`tests/test_catalog.py`。修改文件：`app/modules/catalog/models.py`、`app/modules/catalog/router.py`、`app/core/permissions.py`、`app/services/audit.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、`README.md`。旧迁移没有修改。迁移 downgrade 按 `skus`、`spus`、`categories` 顺序整表移除，以避开 MySQL 1553 索引依赖问题。

根代理终验使用 MySQL 9.7.2 专用测试库 `test_catalog_phase7_39798f30`，已在终验后删除；临时 `.env.test` 也已删除，本地 `.env` 保留。Pytest 全量结果为 **66 passed、0 skipped、1 条既有弃用警告（10.87 秒）**，`compileall -q app alembic tests scripts` 通过。

另在预置十张旧业务表且每张表均有真实关联标记数据的 MySQL 库上，从 `a6e73c92f014` 升级到本迁移、插入三张目录表数据、降级回阶段六并再次升级；十张旧表逐字段逐值不变，只有本阶段三张目录表在降级时被移除。反射确认复合外键、精确 CHECK 名 `ck_spus_status` 与 `ck_skus_price_nonnegative`、`DECIMAL(12,2)`；`current` 和 `check` 通过。

Uvicorn 实测九个 API 完整流程：两商家同名分类可并存、同商家重名 409、跨商家分类 404；同商家两店主及员工都能读取共用的 SPU 和 SKU 售价，所有六个写端点对店主、员工、平台管理员及 `NONE` 均返回 403；跨商家 SPU/SKU 读写返回 404，伪造 body 返回 422，伪造 query 租户参数不能扩大范围。价格 `0.10`、`0.20`、`0` 和最大值 `9999999999.99` 精确往返，非法 POST/PATCH 价格返回 422；空 SKU 阻止上架，有 SKU 才能上架，重复上架 409，普通 PATCH 写状态 422。SKU 同 SPU 编码唯一、不同 SPU 可复用编码，修改冲突会保留原值。删除路由返回 405，且没有新增库存、订单或下架审批表；审计六种事件只写固定 ID 上下文。移除员工后，旧 JWT 的 `/me` 即时返回 `NONE`，目录访问返回 403。Uvicorn 已停止。

开发库 `ecommerce_saas_dev` 已仅升级到 `20261017_catalog`，`current` 和 `check` 通过；原十表均为零行且字段值未变，新三表为空。没有重建、降级开发库或创建开发管理员，`seller` 未触碰。四条旧迁移哈希保持不变；新迁移冻结 SHA256 为 `1e5376f3ff5ac404cd83e7fe7fb3e65a68059b6a0478135fe921bb9ab0dc1a84`。当前 head 为 `20261017_catalog`，共 13 张业务表，本阶段只增加三张。

剩余风险：业务提交和独立审计提交不是原子操作，进程在两次提交间退出时可能缺少审计记录；商家用户行锁可能限制并发吞吐；SPU 创建目前没有请求幂等键，客户端超时重试可能创建重复 SPU。MySQL DDL 不具备通用事务回滚语义，失败后需检查实际结构；MySQL 8.4 Compose 版本和 Python 3.12 本阶段未实测，当前验收环境为 Python 3.14.2、MySQL 9.7.2。商品下架/删除审批、仓库授权和库存流水未实现，不能由目录 API 表达库存。

下一阶段实现库存时，需要为已有 SKU 建立对应仓库的零库存记录，并在同一业务事务中为新 SKU 初始化库存；本阶段没有创建库存记录或实现该初始化流程。

## 第六阶段工作总结（历史，2026-10-16）

第六阶段新增 `a6e73c92f014`，父版本为 `789fe3dda218`。只新增 `shop_employee_roles`、`shop_employees`、`employee_applications` 三张表和员工管理 API；旧迁移 `2ba874a754ef`、`204e6bcc8f59`、`789fe3dda218` 均未修改。现有店铺在迁移时各获一个“普通员工”岗位；本阶段扩展既有店铺审批，在批准新店时同一业务事务中创建该岗位。没有新增依赖、目录 API、权限表或其他业务表。

### 新表、外键和约束

| 表 | 字段和关键约束 |
| --- | --- |
| `shop_employee_roles` | 自增 BIGINT `id`、非空 `shop_id`、非空 `name`（最多 100 字符）、数据库默认 `created_at`。`shop_id` 外键引用 `shops.id` 并使用 RESTRICT；`(shop_id, name)` 唯一，`(shop_id, id)` 另有唯一键以供 MySQL 复合外键引用。每个店铺的默认岗位名为“普通员工”。 |
| `shop_employees` | 自增 BIGINT `id`、非空 `shop_id`、非空 `user_id`、非空 `role_id`、数据库默认 `joined_at`。`user_id` 全局唯一，因此同一用户最多加入一个店铺。`(shop_id, role_id)` 复合外键引用 `shop_employee_roles(shop_id, id)`，数据库可靠拒绝跨店岗位分配。店铺、用户和岗位外键都使用 RESTRICT。 |
| `employee_applications` | 自增 BIGINT `id`、非空 `shop_id`、非空 `applicant_user_id`、区分大小写的 `status`、可空 `reviewer_user_id`、`rejection_reason`、默认 `created_at` 和可空 `reviewed_at`。CHECK 将状态限定为 `PENDING`、`APPROVED`、`REJECTED`；店铺、申请人和审核人外键均 RESTRICT。 |

角色名在每店按 MySQL `utf8mb4_unicode_ci` 排序规则唯一。数据库没有“每用户一个待审申请”的可移植部分唯一约束；所有身份申请写入先按用户 ID 升序锁 `users` 行，再在同一事务检查待审状态，从而保证同一用户全局至多一个待审员工申请。员工用户 ID 唯一键与身份锁协议共同防止重复加入。

### 八个 API

以下路径均以 `/api/v1` 为前缀，均需有效 Bearer token。分页接口沿用 `{items, limit, offset, total}`，默认 `limit=50`、`offset=0`；`limit` 为 1–100，`offset` 为 0–100000。请求模型拒绝额外字段，状态与决策枚举区分大小写。

1. `POST /employees/applications` 接受 `{"shop_id": 12}` 并返回 201 的申请对象，字段精确为 `id, shop_id, applicant_user_id, status, reviewer_user_id, rejection_reason, created_at, reviewed_at`。申请人必须是当前数据库身份 `NONE` 的启用用户，店铺必须真实存在。同一用户已有待审申请或身份不合格返回 409；未知店铺返回 404。
2. `GET /employees/applications?status=PENDING&limit=50&offset=0` 支持三种状态过滤。店主只看自己店铺的申请；其他身份只看本人申请。租户范围取自当前身份和数据库关系，查询参数不能扩大范围。
3. `POST /employees/applications/{id}/review` 只允许申请所属店铺的当前店主。通过请求为 `{"decision":"APPROVE"}`；拒绝请求为 `{"decision":"REJECT","rejection_reason":"材料不完整"}`。拒绝原因必填且最多 2000 字，批准请求不接受拒绝原因。批准时重新检查申请人身份为 `NONE`，创建使用“普通员工”岗位的员工关系，并把申请更新为 `APPROVED`；拒绝仅更新申请。响应字段与提交申请相同。重复审核、身份竞争或唯一性冲突返回 409，跨店和非店主返回 403，未知申请返回 404，格式错误返回 422。
4. `GET /shops/{shop_id}/employees?limit=50&offset=0` 仅供本店当前店主分页读取员工；员工对象字段精确为 `id, shop_id, user_id, role_id, role_name, joined_at`。它不是用户目录。
5. `DELETE /shops/{shop_id}/employees/{user_id}` 仅供本店当前店主移除员工，成功返回 HTTP 204 和空响应体。它只删除员工关系，不删除或停用用户账号，因此该用户可用仍有效的旧 token 立即以 `NONE` 身份访问，并可再次申请。不存在的目标用户返回 404。
6. `GET /shops/{shop_id}/employee-roles?limit=50&offset=0` 仅供本店店主分页读取本店岗位；岗位对象字段精确为 `id, shop_id, name, created_at`。
7. `POST /shops/{shop_id}/employee-roles` 接受 `{"name":"收银"}` 并返回 201 的岗位对象（字段精确为 `id, shop_id, name, created_at`）；本店重名返回 409。没有岗位删除 API。
8. `PATCH /shops/{shop_id}/employees/{user_id}/role` 接受 `{"role_id": 34}`，只能将本店员工改派到同店岗位。不存在的目标员工或本店不存在该岗位返回 404，跨店调用返回 403。

员工列表和岗位目录均限当前店主使用；普通员工、商家管理员、平台管理员都没有员工管理权限。应用列表的店主范围来自真实店铺关系，不能由请求体或 query 中的租户 ID 改写。

### 身份、岗位和固定权限

`IdentityService` 每次从 `shop_employees` 与真实 `shops` 联结推导 `SHOP_EMPLOYEE`、`shop_id`、`merchant_id`。员工关系与平台管理员、商家管理员、店主关系互斥；发现冲突时身份解析拒绝访问。JWT 只用于标识用户，角色或租户声明不可信；员工批准、移除或岗位调整后，旧 token 的下一次请求会重新读数据库，因此批准立即授予员工身份，移除立即撤销。

岗位名只用于展示，不决定权限。当前固定策略允许普通员工仅访问自己所属店铺的店铺操作范围和订单动作；员工不能通过店铺管理检查，也不能管理员工或岗位。商家管理员继续只有订单读取能力，平台管理员不会因此获得商家或店铺数据权。项目没有 `permissions` 表，也没有按岗位配置权限的 API；之后若需要不同岗位权限，应单独设计授权关系和策略，不能把自由文本岗位名当作权限声明。

### 事务、并发和审计边界

认证依赖可能先触发 SQLAlchemy Session autobegin。写路由会在回滚只读事务前复制必要整数 ID；顶层写 Service 在干净 Session 上独占 `session.begin()`，子逻辑不提交。申请、审核、员工移除和岗位调整先按涉及用户 ID 升序 `SELECT ... FOR UPDATE` 并刷新 identity map，再锁申请或员工关系；审核锁申请之后重新读取申请、店主和申请人的当前数据库身份。这样员工审批会与商家/店铺审批争用同一用户根行，不能给一个账号并发分配两种身份。审批者也会在锁后重新确认仍是当前店主；未来店主变更必须遵守同样的升序用户锁协议。

员工移除与岗位调整都先锁店主和员工用户行，再锁员工关系，因此并发时岗位更新要么先提交、随后被移除，要么在移除后返回未找到；二者不会重建或遗留员工关系。岗位更新不会改变 `SHOP_EMPLOYEE` 身份或固定权限。复合外键和唯一键仍作为数据库最终约束。

成功写入后，申请提交/审核、员工移除和岗位创建/更新会由独立 Session 与事务写入审计事件。审核只记录 APPROVE/REJECT；管理事件只记录固定整数 ID 白名单，不记录秘密或自由文本。审计失败会安全记录异常类型并保留业务成功结果。业务和审计不是原子事务，没有 outbox；若进程恰在两次提交之间退出，审计记录可能缺失。

锁协议要求所有身份写入遵循统一的用户 ID 升序根行锁；粗粒度用户锁可能限制高并发吞吐，直接手工 SQL 写入也会绕过身份互斥与并发协议。MySQL DDL 不具备通用事务回滚语义，迁移失败时应先检查已落地结构，再按迁移说明安全恢复；应用迁移不等于业务数据事务。独立审计事务可能因进程中断或审计存储故障而缺失记录，后续若要求可靠投递应另行设计 outbox。

五个 why：①JWT 中的角色不可信，因为角色和店铺范围会变化，必须由数据库关系实时推导；②不建 `permissions` 表，因为当前产品只定义固定店铺策略，岗位名称只显示，且没有按岗位授权需求；③跨店隔离来自真实 `shop_id` 和复合外键，客户端不能选择其他店铺岗位；④用户升序根行锁统一串行员工、店铺、商家审批并避免互斥身份竞态；⑤移除只删成员关系而保留用户账号，能立即撤权并支持重新申请，也避免把店铺用工关系误当账号生命周期。

### 文件、迁移与真实 MySQL 验证

新增文件：`app/modules/employees/schemas.py`、`app/services/employees.py`、`alembic/versions/a6e73c92f014_shop_employees.py`、`tests/test_employees.py`。修改文件：`app/modules/employees/models.py`、`app/modules/employees/router.py`、`app/services/identity.py`、`app/core/permissions.py`、`app/services/shop_applications.py`、`app/services/audit.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、`tests/test_shop_applications.py` 和 `README.md`。ORM 仍由 `app.db.models` 注册现有模块，没有增加目录或新架构层。

专用 MySQL 9.7.2 测试库 `test_employee_phase6_07e652f2` 运行真实迁移 backfill、复合外键/状态 CHECK/唯一约束、增量 downgrade 到 `789fe3dda218` 后逐值验证七张历史业务表数据保留，再 upgrade，以及全 base downgrade/re-upgrade。测试结束后测试库和 `.env.test` 已删除。迁移验收也在预置七张历史表各有数据的库上完成增量 downgrade/upgrade 往返；原表数据逐值不变，每家历史店回填一个“普通员工”岗位，Alembic `check` 无差异。开发库 `ecommerce_saas_dev` 已仅执行 upgrade 到 `a6e73c92f014`，`current` 和 `check` 通过；原七表当时均为零行且值未变，新三表为空。没有重建或降级开发库，没有访问 `seller`，旧三条迁移内容未修改。

测试覆盖八个 HTTP 方法及其分页/租户隔离、重复/非法/拒绝申请、跨店角色 FK、单用户员工唯一、员工无管理权、商家/店铺身份竞争、重复并发审核与申请、移除和岗位调整竞态、旧 token 失权/重新申请、事务回滚、默认岗位写入失败回滚以及审计成功/失败。根代理全量结果为 **62 passed、0 skipped、1 条既有弃用警告（9.24 秒）**；Luna 对最终小改后的 `test_employees.py` 结果为 **15 passed**。`compileall -q app alembic tests scripts` 通过。根代理还通过 Uvicorn 实测八个 API 的完整生命周期和权限边界，包括跨店重申、岗位跨店拒绝、移除后旧 token 身份变化和审计边界。测试库及 `.env.test` 已清理；没有配置专用测试库时，MySQL 集成测试按 fixture 规则 skip，不代表本次未执行。

实测环境为 Python 3.14.2、MySQL 9.7.2；Python 3.12 和 Compose 声明的 MySQL 8.4 未在本阶段实测。测试不能证明业务提交和审计提交原子，也不能保证绕过应用服务的手工 SQL 遵循身份锁协议。

## 第五阶段工作总结（历史，2026-10-16）

第五阶段在既有 `shops` 身份表上增加店铺申请和商家审核流程，并提供受限商家目录。只新增 `shop_applications` 一张表和五个 HTTP API；
没有修改旧身份表、第四阶段迁移、依赖版本或其他业务表。ORM 仍由八个模块的 Router→Service→ORM 组成，SQLAlchemy 同步 Session 使用 MySQL；
顶层业务 Service 拥有写事务，审计在独立 Session 和事务中提交。

### 新表

迁移 `789fe3dda218` 以 `204e6bcc8f59` 为父版本，仅创建 `shop_applications` 及申请人、商家两个普通索引。字段为自增 BIGINT `id`；
非空 `merchant_id`、`applicant_user_id` 外键；非空 `shop_name`；非空 `status`（`utf8mb4_bin` 并由 CHECK 限定为 `PENDING`、`APPROVED`、`REJECTED`）；
nullable `reviewer_user_id`、`rejection_reason`、`created_shop_id`、`reviewed_at`；非空默认数据库当前时间的 `created_at`。所有外键均为 RESTRICT。
没有待审申请唯一索引；同一用户跨商家最多一份待审申请由用户行锁和事务内复查保障。

`shops.owner_user_id` 原有唯一约束和 `(merchant_id, name)` 唯一约束保留。不同商家可拥有同名店铺。同商家申请可并行待审；提交时检查已有店铺，批准时再次检查，
同商家审批由管理员用户锁串行化，数据库唯一约束仍是并发最终保障。用户唯一负责人约束同样由 `shops.owner_user_id` 保持。

### 五个新增 API

本阶段五个接口均要求有效 Bearer token。

`GET /api/v1/merchants?limit=50&offset=0` 为登录用户提供受限目录：

```json
{"items":[{"id":12,"name":"Acme"}],"limit":50,"offset":0,"total":1}
```

目录只返回商家 `id` 和 `name`，不暴露管理员、联系方式或其他内部字段；limit 为 1–100，offset 为 0–100000。

`POST /api/v1/shops/applications` 只接受当前有效且身份为 `NONE` 的用户。用户选择目录中的真实商家，客户端不能指定负责人：

```json
{"merchant_id":12,"shop_name":"Downtown Shop"}
```

成功返回 201：

```json
{"id":31,"merchant_id":12,"applicant_user_id":42,"shop_name":"Downtown Shop","status":"PENDING","reviewer_user_id":null,"rejection_reason":null,"created_shop_id":null,"created_at":"2026-10-16T12:00:00","reviewed_at":null}
```

同一用户至多存在一份 `PENDING` 店铺申请，范围跨商家。提交时商家必须真实存在，名称按数据库的商家内大小写不敏感排序规则检查现存店铺名；owner 字段或其他额外请求字段返回 422。
无效商家返回 404，身份不合格、同名现存店铺或重复待审返回 409。

`GET /api/v1/shops/applications?status=PENDING&limit=50&offset=0` 返回 `{ "items": [...], "limit": 50, "offset": 0, "total": n }`；status 可为三种申请状态，分页上限为 100。
商家管理员只看到当前所属商家的申请；其他角色（包括普通用户、店主和平台管理员）只看到本人申请。没有客户端租户参数参与查询范围；未知的 `merchant_id` query 参数会被忽略，数据库范围仍由当前身份确定。

`POST /api/v1/shops/applications/{id}/review` 只允许当前申请所属商家的有效管理员审核，申请人不能自审；审批通过请求为
`{"decision":"APPROVE"}`，拒绝请求为 `{"decision":"REJECT","rejection_reason":"材料不完整"}`。拒绝原因必填，最多 2000 字；批准时不能携带拒绝原因。
成功返回更新后的申请对象。未知申请为 404；跨商家、非管理员或自审为 403；已处理、身份不合格、名称冲突或负责人/名称唯一约束冲突为 409；请求格式错误为 422。

批准在一个事务内重新校验申请人仍启用且身份为 `NONE`、审核人仍是该商家管理员、商家内店名仍可用，创建绑定该 `merchant_id` 和申请人 `owner_user_id` 的 `Shop`，
并将申请更新为 `APPROVED` 后一次提交。身份或唯一性冲突会回滚新店和申请更新。拒绝只更新审核字段与 `REJECTED` 状态，不创建店；即使申请人之后停用或因其他流程获得身份，商家仍可拒绝其遗留申请。

`GET /api/v1/shops?limit=50&offset=0` 返回分页店铺列表。商家管理员只能读取自身商家店铺，店主只能读取自身店铺。普通用户和平台管理员没有内部店铺读取权；提供 `merchant_id` query 参数不会扩大范围。
店铺响应字段为 `id`、`merchant_id`、`name`、`owner_user_id`、`created_at`。

### 权限、事务及并发

店铺申请人由认证用户得出，负责人只在审核通过时由服务端绑定申请人。提交的 `merchant_id` 只用于选择真实 Merchant，不作为已授权租户身份。
申请查询、审核和店铺读取都从数据库当前身份关系推导商家或店铺范围。`PLATFORM_ADMIN` 不会自动获得商家或店铺数据权限。
审批会重新验证申请人身份，因为身份可能在申请后经其他审批改变；只在提交时校验 `NONE` 会让旧申请在用户已有身份后继续创建第二种身份。
建店不自动授予仓库访问权，因为店铺负责人关系和仓库/库存授权是不同的数据权限；本阶段也没有仓库授权表或权限 API。
本阶段停止在商家目录、店铺申请、审核与店铺列表；没有负责人更换、员工、商品、库存、订单或仓库授权流程。

写路由先从认证只读事务捕获整数 ID 和申请的申请人/商家等不可变信息，然后回滚只读事务。新写事务按用户 ID 升序锁齐相关 `users` 行，使用 `FOR UPDATE` 和 `populate_existing`，
之后审核才锁申请行；锁齐前不会做普通一致性查询。锁后重新查询真实身份和商家关系，因此同一用户的商家审批与店铺审批竞争时，不会同时得到两种身份。
同一申请人提交两份不同商家的申请也由申请人根行锁串行化；不同申请人争同商家同一名称时，商家管理员用户行锁让后续事务在最新 RR 视图中复查名称，`shops` 唯一约束负责兜底。
MySQL 1062 会回滚并映射为 409；其他完整性错误不会错误包装成重复冲突。此锁协议要求后续任何身份写入继续先按 ID 升序锁用户，再锁申请。

四项事务选择的原因：①先在认证只读事务中复制 ID 并回滚，避免依赖查询遗留的 RR 快照或在已有事务中嵌套 `begin()`；②先按升序锁 `users` 根行，
让商家审批、店铺审批和同一用户的申请相互串行，并使身份关系普通查询在取得锁后读取最新提交状态；③同商家店名检查复用该 Merchant 当前管理员的用户行锁，
以商家为粒度串行名字竞争，同时让不同商家独立推进，数据库复合唯一约束仍作最终保护；④审计使用业务提交后的独立事务，是为了审计不可用时保留已成功业务响应，
代价是没有 outbox 时进程可能在两个提交之间退出并丢失事件。

提交和通过/拒绝审核成功后，`AuditService` 使用独立 Session 和事务记录固定白名单事件。店铺提交只记录空 JSON，审核只记录 `APPROVE`/`REJECT`；不会记录请求体、拒绝理由、JWT 或秘密。
审计事件使用 `target_type=shop_application`；查询事件时必须同时按 `target_type` 和 `target_id`，因为多态目标表的自增 ID 可能碰撞。审计工厂或提交失败只记录安全错误类型并仍返回已成功业务结果。
业务和审计事务并非原子，没有 outbox 或队列保证；进程在业务提交与审计提交之间退出时可能漏记审计事件。

### 文件及验证

本阶段新增/修改文件：

- 新增 `app/modules/shops/schemas.py`、`app/services/shop_applications.py`、`app/services/merchant_directory.py`、`alembic/versions/789fe3dda218_shop_applications.py`、`tests/test_shop_applications.py`。
- 修改 `app/modules/shops/models.py`、`app/modules/shops/router.py`、`app/modules/merchants/schemas.py`、`app/modules/merchants/router.py`、`app/services/audit.py`、`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py` 和本文档。
- 未修改 `app/db/models.py`：它已导入 shops 模块模型，新增映射自动进入现有 `Base.metadata`。

父代理在专用测试库 `test_ecommerce_shop_70c4305ddf5c`（MySQL 9.7.2）执行空库 base→head，并验证增量 downgrade 至 `204e6bcc8f59` 只移除 `shop_applications`、
六张旧表原有数据逐值保留，再 upgrade 至 `789fe3dda218`；全 base downgrade/re-upgrade 由迁移测试覆盖。降级只 `drop_table`，不先删除外键支持索引，规避 MySQL 1553。
真实 `alembic check` 无 schema 差异。Luna 与父代理各自运行 `python -m compileall -q app alembic tests scripts` 和全量测试，均为 **47 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告**。

父代理还用真实 Uvicorn 和 MySQL 完成注册、CLI 平台管理员初始化、两商家入驻审批并创建默认仓库、仅返回商家目录安全字段、两商家批准同名店铺、旧 token 的 `/me` 即时显示 `SHOP_OWNER`、
通过伪造 `merchant_id` 查询仍只返回真实租户、跨商家审核 403、普通用户/平台管理员读取店铺 403、重复审核 409、拒绝不创建店铺、独立审计结果正确和仓库数量未变化。CLI 初始化只在测试库执行，开发库没有平台管理员。
开发库 `ecommerce_saas_dev` 已只执行 upgrade 到 `789fe3dda218`；`current` 和 `check` 通过，原六张表行值未变化（当时均为 0 行），新 `shop_applications` 为 0 行。没有降级或重建开发库，`seller` 未触碰。
父代理随后删除了专用测试库和临时 `.env.test`；本地 `.env` 和开发库保留。未配置测试库时，MySQL 集成测试会明确 skip，因此本次 47 项通过不能理解为无配置时默认全通过。

验收环境为 Python 3.14.2 与 MySQL 9.7.2；本次未单独验证最低声明 Python 3.12 或 Docker Compose 所声明的 MySQL 8.4。
剩余风险为审计与业务提交不原子、同商家店铺审核因管理员用户行锁而串行，以及未来负责人变更必须遵循相同锁序。阶段五范围止于商家目录、店铺申请/审核和店铺读取；负责人更换、员工、仓库授权、商品、库存及订单仍未实现。新建店铺不自动得到仓库权限。

## 第四阶段历史工作总结（2026-10-16）

第四阶段只增加商家入驻流程及其审计，新增 `merchant_applications`、`warehouses`、`audit_events` 三张 InnoDB 表，
新增 Alembic revision `204e6bcc8f59`（父 revision `2ba874a754ef`）。首个身份迁移文件未修改，其 SHA256 仍为
`5056a232471e6c64956dfe502f1db083e1cc39598381b2fce904563c4f38d55d`。本阶段未增加运行依赖、本阶段范围外的表或接口。

### 平台管理员 CLI

平台管理员只能通过本地显式命令初始化，不提供公开管理员 API。命令只提升已存在且启用的用户；必须显式重复完全一致的用户名，
不会创建用户、生成密码、硬编码或输出密码/JWT/数据库凭据：

```powershell
.\.venv\Scripts\python.exe -m scripts.init_platform_admin --username alice-shop --confirm-username alice-shop
```

命令先锁定目标用户，再读取其数据库身份关系。只有 `NONE` 身份可被提升；已有商家管理员/店主身份、身份关系冲突、禁用用户、未知或
大小写不完全相同的目标均拒绝。已经是有效平台管理员时安全返回“already initialized”，不会重复或错误提升其他账户。成功与拒绝消息只包含
目标用户名或原因类型，不输出真实秘密。测试覆盖成功、重复、用户名确认不匹配、未知、禁用及商家/店主冲突。
第四阶段验收时开发库没有平台管理员；开发者应先注册自己的账号，再将自己的精确用户名填入命令两处参数，不应把他人账号或测试凭据用于初始化。

### 新表和关系

| 表 | 字段和约束 |
| --- | --- |
| `merchant_applications` | 自增 BIGINT `id`；非空 `applicant_user_id` 引用 `users.id`；非空 `merchant_name` VARCHAR(255)；`status` VARCHAR(16) 默认 `PENDING`，使用区分大小写的 `utf8mb4_bin` 并由 CHECK 限定为 `PENDING`/`APPROVED`/`REJECTED`；nullable `reviewer_user_id` 引用 users、`rejection_reason` TEXT、`created_merchant_id` 引用 merchants、`reviewed_at`；`created_at` 非空且默认数据库当前时间；申请人有索引。 |
| `warehouses` | 自增 BIGINT `id`；非空 `merchant_id` 引用 `merchants.id` 且唯一（每个商家至多一个仓库）；非空 `name` VARCHAR(255)；`created_at` 默认数据库当前时间。审批创建固定名 `Default warehouse`。 |
| `audit_events` | 自增 BIGINT `id`；非空 `actor_user_id` 引用 `users.id`；`event_type`、`target_type`、`outcome`；`target_id` BIGINT；非空 JSON `event_data`；`created_at` 默认数据库当前时间。`target_type`/`target_id` 是多态目标，不伪造跨多张表的外键。 |

三个新表使用 `utf8mb4`、InnoDB；外键更新/删除均为 RESTRICT。审批通过不创建店铺或库存，也不产生 stocks 表。

### 商家申请 API

三个接口均挂在 `/api/v1/merchants`。访问者必须带有效 Bearer token。

`POST /api/v1/merchants/applications` 只接受当前身份为 `NONE` 的有效用户：

```json
{"merchant_name":"Acme Store"}
```

成功返回 201，响应字段为 `id`、`applicant_user_id`、`merchant_name`、`status`、`reviewer_user_id`、`rejection_reason`、
`created_merchant_id`、`created_at`、`reviewed_at`。新申请的状态为 `PENDING`，审核字段为空。同一用户已存在待审申请时返回 409；
身份后来成为平台管理员、商家管理员或店主时不能再提交。

`GET /api/v1/merchants/applications?limit=50&offset=0&status=PENDING` 返回 `{ "items": [...], "limit": 50, "offset": 0, "total": n }`。
分页上限为 100。一般用户（包括已有商家/店主身份的申请人）只能看到自己的申请；平台管理员只能看到待审申请，传入其他状态过滤器返回 422。
后端始终按 `applicant_user_id` 或待审状态构造查询范围，没有接受租户 ID 参数。

`POST /api/v1/merchants/applications/{id}/review` 只允许当前平台管理员。批准请求为
`{"decision":"APPROVE"}`，驳回请求为 `{"decision":"REJECT","rejection_reason":"材料不完整"}`；驳回原因必填，最长 2000 字，
批准时不允许传原因。成功返回 200 和更新后的申请结构。未知申请为 404；身份/状态冲突、重复待审或重复审核为 409；非平台审核者或自审为 403；
格式与决策原因校验失败为 422。

批准时，顶层审批 Service 在同一 MySQL 事务中复核审核人与申请人的有效身份，创建 Merchant（管理员为申请人）、创建唯一默认仓库，并将申请改为
`APPROVED`、写入审核人/数据库当前时间/新 merchant ID 后一次提交。任何一步失败均回滚商家、仓库及申请更新。驳回只把申请改为
`REJECTED` 并记录审核字段，不创建商家或仓库。`PENDING` 仅能转换一次。
申请人的有效、无冲突 `NONE` 身份只在批准时要求；若同一用户已有另一份申请获批，平台管理员仍可驳回该用户遗留的待审申请。

### 事务与并发锁顺序

认证依赖先读用户与身份，SQLAlchemy 因此可能已 autobegin。写路由先由 Service 捕获所需 ID；review 在既有只读请求事务中取得申请人 ID，
然后检查 Session 没有待写对象并回滚只读事务。业务写入只由顶层 Service 的 `with session.begin():` 拥有；Service 拒绝带已有事务的调用，
没有无条件嵌套 begin，也没有子 Service 私自提交。回滚后只使用复制出的整数 ID，不再从已过期 ORM 对象读取字段。

MySQL 默认 RR 下，每个写事务遵循固定顺序：先用 `SELECT ... FOR UPDATE` 按用户 ID 升序锁定全部涉及用户（申请只锁申请人；审核锁平台审核者和申请人），
然后 `FOR UPDATE` 锁申请行并重新确认申请人和状态，最后才普通读取身份关系。锁查询用 `populate_existing` 刷新 Session identity map。
申请人 ID 在只读阶段定位，所以新事务不会在等待用户锁前建立旧的一致性读快照；锁齐后普通身份关系查询的 RR 快照能看到先完成的审批。
不对“尚无商家关系”的范围加 gap lock，避免跨申请的 gap-lock 死锁。两个申请同时争同一个申请人时，第一个提交后第二个会重新读取关系并冲突；
同一申请并发审核只有一个状态转换成功。此协议要求后续所有平台身份/商家身份写入都遵循同一用户行锁顺序。

申请提交、批准或驳回提交后，`AuditService` 以新 Session 和独立事务追加审计事件。当前只允许固定事件类型及字段白名单：提交记录空 JSON，
审核记录 APPROVE/REJECT 决策；不会记录密码、JWT、请求头或自由文本驳回原因。审计事务失败只向服务端日志写事件/目标/ID/异常类型，
不记录异常消息或 SQL 参数；即使审批已成功提交，API 仍返回成功。由于审计不与业务事务原子提交，也没有消息队列/outbox 保证，进程在两事务之间
退出或审计库暂时不可用时可能漏记事件。测试模拟了独立审计 Session 和工厂失败，并确认批准返回 200 时已提交的商家、仓库和 `APPROVED` 状态不被改写；审计失败不会改变已成功的业务结果。

### 第四阶段文件、迁移和验证

本阶段新增/修改 `app/modules/merchants/models.py`、`app/modules/merchants/schemas.py`、`app/modules/merchants/router.py`、
`app/modules/inventory/models.py`、`app/services/merchant_applications.py`、`app/services/audit.py`、
`scripts/init_platform_admin.py`、`app/db/models.py`、`alembic/versions/204e6bcc8f59_merchant_onboarding_and_audit.py`、
`tests/conftest.py`、`tests/test_alembic.py`、`tests/test_health.py`、`tests/test_mysql_migrations.py`、
`tests/test_merchant_applications.py` 和本文档；没有修改 `2ba874a754ef`。

在父代理提供的隔离 MySQL 测试库真实运行 Alembic upgrade、downgrade 到 `2ba874a754ef`、再 upgrade head；迁移往返及 `alembic check` 均通过。
降级按外键依赖顺序直接删除新表，由 MySQL 随表删除其外键和索引。
降级往返专门保留了 sentinel 旧用户，并验证原有三张表和旧数据未被删除。迁移及 HTTP/CLI 验收完成后，父代理整体删除本次专用测试库和 `.env.test`。
第四阶段验收当时，开发库 `ecommerce_saas_dev` 仅 upgrade 到 `204e6bcc8f59`，没有删除或重建；当时旧 `users`、`merchants`、`shops` 各 0 行，新三表也各 0 行。
当时的测试库 CLI 验证没有在开发库创建管理员，`seller` 库未触碰。当前开发库迁移状态及管理员状态见本文顶部第六阶段总结。

验收：`python -m compileall -q app alembic tests scripts` 通过；全套 Pytest 为 **32 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告**。
真实 MySQL 测试覆盖三个 API、租户范围、CLI身份互斥与重复执行、Merchant/warehouse 中途故障回滚、两独立 Session 并发争同申请人及争同申请、
独立审计提交/审计故障以及迁移往返。父代理还用 Uvicorn 完成了三账号注册、显式 CLI 初始化及重复安全报告、申请提交 201、伪造申请人过滤仍仅返回自己的列表、普通用户审核 403、平台审核批准 200、唯一仓库、旧 JWT 的 `/me` 实时返回 `MERCHANT_ADMIN` 和重复审核 409 的 HTTP 流程；审计独立提交了两条预期事件。Uvicorn 随后停止。

## 第三阶段历史工作总结（2026-10-16）

第三阶段实现 `POST /api/v1/auth/register`、`POST /api/v1/auth/login` 和受保护的
`GET /api/v1/auth/me`。注册只返回 `id`、`username`、`email`；登录返回 `access_token`、
`token_type` 和相同的公开用户对象；`me` 返回公开用户字段及数据库推导的 `role`、`merchant_id`、
`shop_id`。请求模型拒绝额外字段，用户名要求 3–100 个 ASCII 字母/数字及 `_.-` 字符并以字母/数字开头，
邮箱使用 EmailStr 并转为小写，密码要求 12–128 个字符。数据库现有唯一索引负责最终防并发重复，
MySQL duplicate-key 1062 映射为 409；其他完整性错误不会伪装成重复账号。

密码由 `pwdlib[argon2]` 的 Argon2id 推荐配置哈希。JWT 使用 PyJWT HS256，只含 `sub` 用户 ID、`iat`、
`exp`；通过 `AUTH_JWT_SECRET` 配置至少 32 字节密钥，访问期限由 `AUTH_JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
控制，默认 30 分钟。密钥可缺省以便导入应用及运行 health，但登录在签发密钥缺失或过短时返回受控 503，
认证验证失败时统一返回 401 和 `WWW-Authenticate: Bearer`。缺失、篡改、过期、非法 subject、非支持算法、
禁用或已删除用户均拒绝；每次认证都重新读取用户状态。JWT 中即使额外包含 role/tenant claim 也不会用于授权。

IdentityService 根据 `is_platform_admin`、真实 `Merchant.admin_user_id`、`Shop.owner_user_id` 关系推导
`PLATFORM_ADMIN`、`MERCHANT_ADMIN`、`SHOP_OWNER` 或 `NONE`。现无员工表，故不虚构 `SHOP_EMPLOYEE` 身份。
平台、商家、店铺三类关系互斥；发现任意组合冲突会拒绝授权。商家管理员/店主资格校验仅接受现存、启用且
当前为 `NONE` 的用户，留给未来审批服务复用；该检查不承诺并发审批安全，未来写审批时仍需事务序列化或约束。

统一授权策略位于 `app/core/permissions.py`，FastAPI 依赖包装位于 `app/api/deps.py`。平台操作只允许平台管理员；
平台管理员不会因此获得店铺/商家租户权限。商家管理员仅能管理自身商家，可读取其实际旗下店铺订单但不能创建、
完成或取消订单；店主仅可操作自己的实际店铺及其订单。店铺授权先从数据库解析 Shop→Merchant 关系，不能信任
调用方提供的 merchant ID；未来订单须先从数据库加载真实 shop_id。订单操作依赖将 action 固定在服务端，客户端
不能以 query 参数把取消等动作降成只读。没有员工权限表；未来员工需使用固定权限规则，不随自定义角色名改变。

### 认证 API 请求与响应

`POST /api/v1/auth/register` 接受：

```json
{"username":"alice-shop","email":"alice@example.com","password":"correct-horse-battery"}
```

成功返回 201：

```json
{"id":42,"username":"alice-shop","email":"alice@example.com"}
```

`POST /api/v1/auth/login` 接受用户名或邮箱：

```json
{"identifier":"alice-shop","password":"correct-horse-battery"}
```

成功返回 200：

```json
{"access_token":"<signed JWT>","token_type":"bearer","user":{"id":42,"username":"alice-shop","email":"alice@example.com"}}
```

`GET /api/v1/auth/me` 使用 `Authorization: Bearer <access_token>`，例如返回：

```json
{"id":42,"username":"alice-shop","email":"alice@example.com","role":"NONE","merchant_id":null,"shop_id":null}
```

认证缺失、错误、过期、禁用用户统一 401 并带 `WWW-Authenticate: Bearer`；身份冲突或权限不足为 403；
注册用户名/邮箱冲突为 409；请求格式、用户名、邮箱或密码不合规则为 422；JWT 签名密钥缺失或太短时，
登录返回 503。响应不会包含密码或密码哈希。

本阶段由 Luna 实现并经父代理独立复验。新增或修改文件：`app/core/config.py`、`app/core/permissions.py`、`app/api/deps.py`、
`app/modules/auth/router.py`、`app/modules/auth/schemas.py`、`app/modules/auth/service.py`、
`app/services/identity.py`、`.env.example`、`tests/conftest.py`、`tests/test_auth.py`、
`tests/test_health.py`、`tests/test_mysql_migrations.py`、本文档，以及本地忽略的 `backend/.env`（仅新增随机
`AUTH_JWT_SECRET` 和 30 分钟期限配置，密钥不记录在文档中）。未新增数据库表、迁移、依赖或订单/商品接口。
历史迁移 `2ba874a754ef` 未修改，SHA256 仍为
`5056a232471e6c64956dfe502f1db083e1cc39598381b2fce904563c4f38d55d`。Luna 的完整 Pytest 为
**21 passed、0 skipped、1 条既有 Starlette/httpx 弃用警告**，compileall 通过；父代理独立复验结果相同。
第三阶段验收时，父代理还在临时 MySQL 测试库上实测 Uvicorn 注册 201、登录 200、`me` 200、无令牌 401，以及禁用账户后旧令牌
401，并验证 `uv pip check` 的 47 个包兼容。该阶段专用测试库及 `.env.test` 已删除。彼时开发库 `ecommerce_saas_dev`
在 head `2ba874a754ef`、Alembic 无差异、三张业务表均为 0 行；既有 `seller` 库未修改。第四阶段迁移结果见本文件顶部。

JWT 不长期信任角色或租户声明，因为商家/店铺关系和账号 active 状态可能在令牌签发后改变；当前数据库读取让禁用、
删除或关系变化立即生效。第三阶段结束时尚未实现 refresh、找回密码、单 token 撤销、登录限速、审计、员工审批、业务端点或审批并发
保护；商家申请和审批的现状及边界见第四阶段总结。

## 第二阶段历史工作总结（2026-10-16）

Luna 完成了第二阶段实现，父代理随后独立复验。新增 `users`、`merchants`、`shops` 三张关系表，
使用真实 MySQL 9.7.2 在 `ecommerce_saas_dev` 上通过 Alembic autogenerate 生成迁移
`2ba874a754ef`。开发库升级且 `alembic check` 通过；专用 MySQL 测试库完成迁移 upgrade、downgrade
和 re-upgrade。真实 MySQL 集成测试及其余测试合计 **13 passed、0 skipped、1 条既有 warning**。

当时使用的临时测试库和 `.env.test` 已清理，开发库及本地 `.env` 保留。本阶段没有
开发业务 API，也没有增加其他业务表。MySQL 8.4 Compose 环境和 Python 3.12 尚未验收。本次只更新了
README，总结第二阶段结果，没有重跑测试；该说明仅对应第二阶段的文档汇总。

详细内容见[文件清单](#目录职责与文件清单)、[字段与设计](#核心身份关系第二阶段)、
[验证结果和风险](#验证与本次结果2026-10-16)及[当前阶段范围边界](#第十一阶段范围边界)。

## 本地开发（PowerShell，在 backend 目录执行）

```powershell
# 首次安装；已有 .venv 时跳过创建，不要覆盖已有环境。
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"

# 只有需要数据库连接时才准备 .env；已有文件时不要覆盖。
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

未安装 uv 时，可使用 `py -3.12 -m venv .venv` 和
`.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`。
本次直接复用现有 Python 3.14.2 虚拟环境，没有重建它。

- 存活检查：`GET http://127.0.0.1:8000/api/v1/health`，返回 `{"status":"ok"}`。
- OpenAPI 文档：`http://127.0.0.1:8000/docs`。
- 健康检查不访问数据库，表示进程可响应，不表示数据库就绪。

## 配置与数据库

配置由 `app/core/config.py` 读取：环境变量优先于 `backend/.env`，再使用代码中的非敏感默认值。
`.env` 路径相对工程目录固定，不依赖启动时的工作目录。配置会在进程内缓存，修改后需重启。
密码使用 `SecretStr` 避免在配置 repr 中明文显示；不要主动打印连接 URL 或提交 `.env`。
本地忽略的 `.env` 配有随机 `AUTH_JWT_SECRET` 和 30 分钟访问期限；`.env.example` 中签名密钥留空。
新环境要自行设置至少 32 字节的 `AUTH_JWT_SECRET` 才能登录签发令牌；应用导入和 health 检查不要求密钥。
数据库账号、密码均无源码默认值，应用启动时也不会创建连接。

需要连接时，在 `.env` 设置 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`。
指定的数据库和账号需已存在，并具有所需权限。执行只读连接检查：

```powershell
.\.venv\Scripts\python.exe -m scripts.check_db
```

该命令只读查询 `VERSION()`、`DATABASE()` 和常量 `1`，不会创建数据库或业务表。退出码：0 成功、1 连接失败、2 配置缺失。
凭据只用于验证实际 MySQL 连通性，不需要在聊天中提供，基础测试和存活检查也不依赖凭据。

开发容器配置：

```powershell
# 在 .env 中自行设置 DB_PASSWORD 和 MYSQL_ROOT_PASSWORD 后执行。
docker compose config --quiet
docker compose up -d mysql
docker compose ps
# 停止容器，保留数据卷。
docker compose down
```

Compose 使用 MySQL 8.4、utf8mb4、持久数据卷和认证 SQL 健康检查，仅发布到本机回环地址。
当前本机已有 MySQL 占用 3306，如需另启容器，将 `.env` 的 `DB_PORT` 改为 3307，`DB_HOST` 保持 127.0.0.1。
本阶段 API 在宿主机运行；Compose 只管理开发数据库。
MySQL 镜像的初始化环境变量只对空数据卷生效；修改 `.env` 不会修改已有卷中的账号密码。
不要使用 `docker compose down -v` 清理需要保留的数据。

## 目录职责与文件清单

以下是当前后端的主要结构。第一阶段建立 FastAPI、同步 SQLAlchemy、配置、会话和迁移骨架；本阶段的具体
新增和修改项另列在目录树之后。原有 `requirements.txt` 和 `.idea` 配置未修改；复用的 `.venv` 仅新增本
工程的可编辑安装信息。

```text
backend/
├── pyproject.toml                # 包元数据、运行依赖、开发依赖及 Pytest 配置
├── .env.example                  # 无真实密码的配置模板
├── .gitignore                    # 本地环境、凭据、IDE、测试/构建产物忽略规则
├── README.md                     # 本文：运行、架构、验收说明
├── compose.yaml                  # 开发 MySQL
├── alembic.ini                   # 迁移配置，不保存连接凭据
├── alembic/
│   ├── env.py                    # 加载配置、导入模型、执行在线/离线迁移
│   ├── script.py.mako            # 迁移文件模板
│   └── versions/
│       ├── 2ba874a754ef_core_identity_relationships.py  # 身份关系迁移
│       ├── 204e6bcc8f59_merchant_onboarding_and_audit.py # 第四阶段迁移
│       ├── 789fe3dda218_shop_applications.py             # 第五阶段迁移
│       ├── a6e73c92f014_shop_employees.py                # 第六阶段迁移
│       ├── 20261017_catalog.py                            # 第七阶段迁移
│       ├── 20261017_offshelf_applications.py               # 第八阶段迁移
│       ├── 20261017_warehouse_access.py                    # 第九阶段迁移
│       ├── 20261017_inventory.py                           # 第十阶段迁移
│       └── 20261017_orders.py                              # 第十一阶段迁移
├── app/
│   ├── __init__.py
│   ├── main.py                   # 应用工厂和 ASGI 入口
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py             # 应用、数据库和 JWT 配置
│   │   └── permissions.py        # 平台、商家、店铺及目录策略
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py               # 全局 DeclarativeBase 和约束命名
│   │   ├── session.py            # 同步 Engine、Session 工厂
│   │   └── models.py             # 业务模型的集中导入入口
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py             # 统一注册 APIRouter，挂载 /api/v1
│   │   ├── deps.py               # 请求级 Session、认证和权限依赖
│   │   └── health.py             # 健康接口和 Pydantic 响应结构
│   ├── services/
│   │   ├── __init__.py
│   │   ├── identity.py           # 根据有效用户和真实关系推导身份
│   │   ├── merchant_applications.py # 申请事务、锁顺序和身份复核
│   │   ├── shop_applications.py  # 店铺申请、审批和默认岗位创建
│   │   ├── employees.py          # 员工申请、审批、岗位和关系管理
│   │   ├── catalog.py            # 商家目录租户隔离、商品事务与审计
│   │   ├── offshelf_applications.py # 商品下架申请/审核事务
│   │   ├── warehouse_access.py   # 店铺仓库授权申请/审核、真实授权查询
│   │   ├── audit.py              # 业务提交后的独立审计事务
│   │   └── orders.py             # 订单幂等、快照、状态及共享库存事务
│   └── modules/
│       ├── __init__.py
│       ├── auth/                 # 注册、登录、JWT 和当前用户
│       ├── users/                # 用户
│       ├── merchants/            # 商家、申请模型/API/schema
│       ├── shops/                # 店铺
│       ├── employees/            # 员工和自定义角色
│       ├── catalog/              # 分类、SPU、SKU 和下架申请
│       │   ├── models.py
│       │   ├── schemas.py
│       │   ├── offshelf_schemas.py
│       │   └── router.py         # 十二个 /catalog API
│       ├── inventory/            # 仓库、仓库授权、共享库存与流水
│       │   ├── models.py
│       │   ├── schemas.py
│       │   └── router.py         # 仓库授权与库存 API
│       └── orders/               # 人工订单及历史商品快照
│           ├── models.py
│           ├── schemas.py
│           └── router.py         # 五个 /orders API
├── scripts/
│   ├── __init__.py
│   ├── check_db.py               # 只读 MySQL 连接验证
│   └── init_platform_admin.py    # 显式提升现有平台管理员
└── tests/
    ├── conftest.py               # 隔离本地 .env 与配置缓存
    ├── test_health.py            # 存活接口和 OpenAPI 边界
    ├── test_config.py            # 配置优先级、校验、密码特殊字符
    ├── test_session.py           # Session 共享/隔离、回滚、显式提交
    ├── test_alembic.py           # 模型注册、当前迁移头、离线 SQL
    ├── test_auth.py              # 认证、身份冲突及租户权限 MySQL 集成测试
    ├── test_merchant_applications.py # CLI/API/并发、回滚和审计 MySQL 集成测试
    ├── test_shop_applications.py # 店铺申请/审批、默认岗位和审计集成测试
    ├── test_employees.py         # 员工申请、权限、并发、审计和事务集成测试
    ├── test_catalog.py           # 分类、SPU、SKU、权限、价格和审计集成测试
    ├── test_offshelf_applications.py # 下架申请、审核、锁竞争和审计集成测试
    ├── test_warehouse_access.py # 授权、复合外键、并发、锁等待和审计集成测试
    └── test_mysql_migrations.py  # MySQL 约束、事务和迁移往返集成检查
```

业务模块包含 `__init__.py`、`router.py`、`models.py`。全部 ORM 映射由 `app.db.models` 注册；库存路由提供仓库授权及库存 API，订单由独立订单模块提供。

第二阶段实际新增或修改的文件：

- 修改原有占位文件 `app/modules/users/models.py`、`app/modules/merchants/models.py`、`app/modules/shops/models.py`，加入三张 ORM 映射。
- 新增 `alembic/versions/2ba874a754ef_core_identity_relationships.py`。
- 新增 `tests/test_mysql_migrations.py`；修改 `tests/test_alembic.py`。
- 修改 `scripts/check_db.py`，在安全输出中包含 MySQL 版本和数据库名。
- 修改本文档；本地新增并被忽略的 `backend/.env` 保存开发库连接配置。

## 核心身份关系（第二阶段）

本阶段只创建 `users`、`merchants`、`shops` 三张 InnoDB 表，使用 `utf8mb4` 和
`utf8mb4_unicode_ci`。主键和外键均为 `BIGINT`，主键自增。表字段和约束如下：

| 表 | 字段和约束 |
| --- | --- |
| `users` | `id` BIGINT 主键；`username` VARCHAR(100) 和 `email` VARCHAR(320) 非空且各自唯一；`password_hash` VARCHAR(255) 非空；`is_platform_admin` 非空，默认 false；`is_active` 非空，默认 true；`created_at`、`updated_at` 非空，数据库当前时间默认值，`updated_at` 行更新时自动刷新。 |
| `merchants` | `id` BIGINT 主键；`name` VARCHAR(255) 非空；`admin_user_id` BIGINT 非空、唯一，引用 `users.id`；`created_at`、`updated_at` 非空并使用数据库当前时间默认值，`updated_at` 行更新时自动刷新。 |
| `shops` | `id` BIGINT 主键；`merchant_id` BIGINT 非空，引用 `merchants.id`；`name` VARCHAR(255) 非空；`owner_user_id` BIGINT 非空、唯一，引用 `users.id`；`(merchant_id, name)` 唯一；`created_at`、`updated_at` 非空并使用数据库当前时间默认值，`updated_at` 行更新时自动刷新。 |

`password_hash` 不要求唯一。该排序规则让用户名、邮箱和同一商家内的店名比较不区分大小写及重音，
且尾随空格不影响唯一性；不同商家可以使用相同店名。店铺复合唯一索引也覆盖按 `merchant_id` 查询，
无需重复单列索引。

外键的更新和删除规则都是 `RESTRICT`。用户是否有资格管理商家、商家管理员/店铺负责人跨表身份互斥，
留给 Service 事务校验；本阶段不添加角色字段或数据库无法可靠表达的跨表约束。第三阶段在服务层推导身份并
拒绝冲突，没有修改表结构、迁移或增加其他业务表。

## 三层结构与事务约定

- Router：解析 Pydantic 请求/响应和依赖，调用 Service。
- Service：执行业务规则、租户隔离检查与跨表事务。
- ORM：映射表并通过共享 Session 操作数据库，不增加 Repository 层。

`get_engine()` 和 Session 工厂按进程缓存；Engine 连接池可以复用，但 Session 不跨请求共享。
未来同步数据库接口使用普通 `def`，由 FastAPI 在线程池调用，避免在事件循环内直接阻塞。
Router 使用 `DbSession`（或 `Depends(get_db)`）获得独立 Session。
同一次请求内，FastAPI 的依赖缓存使各依赖共享同一 Session，并显式把它传给下层 Service。

最上层业务 Service 拥有事务边界，通常在首次数据库操作之前进入 `with session.begin():`。
子服务只执行查询、更新或 `flush()`，不得擅自 `commit()`。
依赖退出时关闭 Session，未提交的事务会回滚；依赖本身不会自动提交。
不要先查询触发自动事务，再无条件嵌套 `session.begin()`；由上层统一组织查询和写入。

独立审计服务从 Session 工厂新建 Session，不能复用业务 Session；商家申请业务事务提交后写审计。失败被捕获并记录安全错误类型，
保持已提交的业务成功结果和 API 响应。该独立事务无法保证与业务事务原子，细节见第四阶段工作总结。

## Alembic

```powershell
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\alembic.exe history
# 需要完整数据库配置，但离线模式不会建立数据库连接。
.\.venv\Scripts\alembic.exe upgrade head --sql

# 为后续模型变更生成迁移并应用：
# .\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"
# .\.venv\Scripts\alembic.exe upgrade head
```

所有映射必须继承 `app.db.base.Base`。`alembic/env.py` 导入 `app.db.models`，该入口显式导入八个模块的
`models.py`，因此后续在这些文件中定义模型即可被 autogenerate 发现；如果拆分模型文件，需要在相应
`models` 包中导出全部映射。无须也不应在应用启动时使用 `create_all()`。
约束命名统一，`CheckConstraint` 需显式指定 name。
`Base.metadata` 当前注册 `users`、`merchants`、`shops`、`merchant_applications`、`warehouses`、`audit_events`、
`shop_applications`、`shop_employee_roles`、`shop_employees`、`employee_applications`、`categories`、`spus`、`skus`、
`product_offshelf_applications`、`warehouse_access_applications`、`warehouse_accesses`；第四阶段 migration head 为 `204e6bcc8f59`，第五阶段为 `789fe3dda218`，第六阶段为
`a6e73c92f014`，第七阶段为 `20261017_catalog`，第八阶段为 `20261017_offshelf`，第九阶段为 `20261017_warehouse_access`，第十阶段为 `20261017_inventory`，当前 head 为第十一阶段 `20261017_orders`。另注册 `orders`、`order_items` 两个映射。

## 依赖选择

运行依赖和版本范围沿用原有 `requirements.txt`：FastAPI/Uvicorn 提供 HTTP 服务，Pydantic/
pydantic-settings 提供结构校验和环境配置，SQLAlchemy/PyMySQL/Alembic 提供同步持久化与迁移。
cryptography 支持 MySQL 相应认证方式。
email-validator 校验注册邮箱；PyJWT 签发和验证短期访问令牌；pwdlib[argon2] 哈希和验证密码。
pytest/httpx/pytest-cov 归入 dev 可选依赖。
新增 setuptools 仅作为标准 Python 包构建工具，在隔离构建环境中使用，不是业务运行依赖。
没有增加其他第三方业务库。

后续以 `pyproject.toml` 作为依赖维护入口。原有 `requirements.txt` 按要求保留原样，当前范围与之相同，
它是历史安装入口；避免将来手动维护两份不同清单。本阶段未生成依赖锁文件或增加依赖。

## 第三阶段验收结果（历史，2026-10-16）

```powershell
.\.venv\Scripts\python.exe -m compileall -q app alembic tests scripts
.\.venv\Scripts\python.exe -m pytest
```

| 项目 | 实际结果 |
| --- | --- |
| Python 编译检查 | Luna 和父代理均运行通过 |
| Pytest | 第三阶段时 Luna 和父代理均为 21 passed、0 skipped、1 条既有弃用警告；包含真实 MySQL 认证/身份/权限和迁移集成测试 |
| HTTP/OpenAPI | 父代理启动 Uvicorn 并连接临时测试库，实测注册 201、登录 200、`me` 200、缺少令牌 401、禁用账户后旧令牌 401；测试覆盖权限 403 |
| 包兼容性 | 父代理运行 `uv pip check`，47 个包依赖兼容 |
| 可编辑包构建/安装 | 第一阶段历史结果：通过；使用现有环境和 `--no-deps`，未升级既有运行依赖 |
| 开发库迁移状态 | 父代理确认 `alembic current` 为 head `2ba874a754ef`、`alembic check` 无差异，`users`、`merchants`、`shops` 均为 0 行 |
| Alembic autogenerate | 第二阶段历史结果：在空开发数据库检测到并生成三表迁移；本阶段保持现有迁移，未修改迁移文件 |
| MySQL 集成测试 | 通过；临时专用库验证认证、身份/租户权限、唯一/FK 约束、事务回滚及迁移往返；父代理验收后删除测试库和 `.env.test` |
| Alembic 离线 upgrade head --sql | 通过；不建立数据库连接，直接编译已有迁移脚本的 SQL |
| Docker Compose 配置检查/容器启动 | 未执行；当前 PATH 没有 Docker 命令 |

第三阶段验收环境：Python 3.14.2、FastAPI 0.143.2、Starlette 1.8.0、Uvicorn 0.54.0、
Pydantic 2.14.1、pydantic-settings 2.16.0、SQLAlchemy 2.1.4、PyMySQL 1.2.3、
Alembic 1.20.0、Pytest 9.1.1、httpx 0.28.1。最低声明版本 Python 3.12 尚未单独验收。

测试存在一条来自现有 Starlette TestClient/httpx 组合的弃用警告，测试仍通过。
当前没有为消除警告引入新的 httpx2 依赖。后续升级测试客户端依赖时应复核兼容性。
Session 生命周期测试仅使用临时 SQLite 文件，不代表 MySQL 方言、隔离级别、锁或并发库存已验证。

集成测试只接受显式准备的专用 MySQL 测试库：在本地忽略的 `backend/.env.test` 或环境变量中提供连接参数，并设置
`MYSQL_TEST_DB_APPROVED=1`。库名需以 `test_` 开头或 `_test` 结尾；测试校验真实数据库名，并只接受空库、只含原有四张表且
位于 `2ba874a754ef` 的空数据库、位于 `204e6bcc8f59` 的空数据库、七张业务表加版本表且位于 `789fe3dda218` 的空数据库、十张业务表加版本表且位于 `a6e73c92f014` 的空数据库、十三张业务表加版本表且位于 `20261017_catalog` 的空数据库、十四张业务表加版本表且位于 `20261017_offshelf` 的空数据库、十六张业务表加版本表且位于 `20261017_warehouse_access` 的空数据库、十八张业务表加版本表且位于 `20261017_inventory` 的空数据库，或二十张业务表加版本表且位于 `20261017_orders` 的空数据库；其他表、视图、版本或业务数据明确 skip。
测试 fixture 不创建或删除数据库；共享 MySQL fixture 只接受通过名称、结构、版本和空数据检查的专用库，并按 FK 顺序清理测试行。迁移往返测试操作其已批准的测试库结构，最终留下 head。第十二阶段专用库及 `.env.test` 已在根代理独立验收后删除；第十一阶段及更早阶段专用库也已分别清理。没有测试库配置时，之后运行 MySQL 集成测试会按 fixture 规则 skip，这不代表本阶段没有实测。开发库 `ecommerce_saas_dev` 当前已升级到 `20261017_phase12`，本地 `.env` 保留。

本次 MySQL 集成测试确认了用户名和邮箱唯一、管理员用户外键及每用户至多一个商家、负责人至多一家店铺、
同商家店名唯一、商家/负责人/管理员全部非法外键、SQLAlchemy ORM 关系和默认字段，以及事务回滚。
唯一冲突断言 MySQL 错误码 1062，外键冲突断言 1452，避免被其他约束误触发而假通过。

风险与限制：真实数据库验收使用 MySQL 9.7.2；Compose 声明 MySQL 8.4，本次未启动 Compose 验证该版本。
时间戳精确到秒，取数据库服务器时区；部署时需统一时区并评估精度。`utf8mb4_unicode_ci` 不区分大小写、
重音及尾随空格，唯一性按此规则判断。开发配置使用本地 root 凭据；部署前应由环境管理员切换到满足应用
读写及迁移需要的最小权限账号，本阶段不加入额外授权流程。管理员/负责人跨表身份互斥由第三阶段 Service
推导时检查。

## 第十一阶段范围边界

本阶段交付人工订单创建、读取、完成和取消，以及共享库存扣减/回补；未实现支付、发货、退款、订单编辑、商品删除、业务 outbox/可靠审计投递、refresh token、密码找回或登录限速。前十阶段的总结是历史记录，最新行为和验收结果以本文第十一阶段为准。
