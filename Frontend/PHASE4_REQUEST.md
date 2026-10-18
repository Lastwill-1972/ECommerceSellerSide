# Vue3 前端第四阶段：身份申请、店铺负责人工作台与员工管理

你负责继续开发多商家多店铺 SaaS 电商管理平台的 Vue3 + TypeScript 前端。

此前已完成：

- 第一阶段：Vue3 基础工程、JWT 认证、身份工作台。
- 第二阶段：商家资料、店铺审核、仓库授权审批和订单只读。
- 第三阶段：商家商品管理、SKU、上下架申请、共享库存与人工库存调整。

FastAPI 后端已完成 20 张业务表、46 个业务 API，Alembic head 为 `20261017_phase12`。

本阶段开发普通用户身份申请与店铺负责人管理功能，并补齐正常及强制负责人交接的前端操作。

不新增后端 API，不修改数据库和历史迁移。

## 一、开发前检查

请阅读：

- Frontend/README.md
- Frontend/PHASE3_HANDOFF.md（如果存在）
- Frontend/src/app/router.ts
- Frontend/src/modules/auth/
- Frontend/src/modules/merchant/
- Frontend/src/shared/
- backend/README.md
- backend/app/modules/merchants/
- backend/app/modules/shops/
- backend/app/modules/employees/
- backend/app/modules/inventory/
- backend/app/services/owner_transfers.py
- FastAPI 实际 OpenAPI

必须以真实源码核对 API 请求、响应、角色权限和状态码。

复用已有认证、Pinia、Axios、BIGINT、精确数值、请求 scope 和路由守卫，不得重建工程。

## 二、开发批次 4A：普通用户申请中心

为 NONE 身份的普通用户提供申请入口。

包括：

1. 商家入驻申请。
2. 商家目录与开店申请。
3. 员工入职申请。
4. 个人申请记录与状态。

核对并使用：

- GET /api/v1/merchants
- POST /api/v1/merchants/applications
- GET /api/v1/merchants/applications
- POST /api/v1/shops/applications
- GET /api/v1/shops/applications
- POST /api/v1/employees/applications
- GET /api/v1/employees/applications

必须区分不同申请类型的字段和可用状态筛选，以实际 schema 为准。

业务要求：

- 普通用户不能直接创建 Merchant 或 Shop。
- 创建商家需要申请及平台审批。
- 创建店铺需要申请及所属商家管理员审批。
- 加入店铺需要提交员工申请，并由目标店铺负责人审核。
- 员工申请所需 shop_id 可以由店主提供。
- 后端没有公开店铺搜索目录，不得虚构。
- 不得通过普通用户界面授予自己管理员或负责人身份。
- 申请通过后必须重新调用 /auth/me 获取身份。
- 同一时间能提交哪些申请，以现有后端检查为准。
- 拒绝、身份冲突或重复申请应显示真实 409/403 等错误。
- 不实现平台超级管理员前端。

申请提交成功不能直接显示“已经获得身份”，只能显示申请当前真实状态。

## 三、开发批次 4B：店铺负责人管理后台

为 SHOP_OWNER 新增 `/shop` 管理区域。

建议页面：

- /shop：店铺概览
- /shop/employees：员工列表
- /shop/applications：员工入职审批
- /shop/roles：店铺岗位管理
- /shop/warehouse：仓库授权申请与状态
- /shop/catalog：本商家商品只读
- /shop/inventory：授权库存只读

对应真实 API：

- GET /api/v1/shops
- GET /api/v1/employees/applications
- POST /api/v1/employees/applications/{id}/review
- GET /api/v1/shops/{shop_id}/employees
- DELETE /api/v1/shops/{shop_id}/employees/{user_id}
- GET /api/v1/shops/{shop_id}/employee-roles
- POST /api/v1/shops/{shop_id}/employee-roles
- PATCH /api/v1/shops/{shop_id}/employees/{user_id}/role
- POST /api/v1/inventory/access-applications
- GET /api/v1/inventory/access-applications
- GET /api/v1/inventory/warehouse
- GET /api/v1/inventory/stocks
- GET /api/v1/catalog/categories
- GET /api/v1/catalog/spus
- GET /api/v1/catalog/spus/{id}

店铺负责人：

- 只能查看和管理本店员工。
- 可以审批本店员工入职申请。
- 可以移除本店员工。
- 可以创建本店岗位名称。
- 可以为本店员工分配现有岗位。
- 不得把其他店铺岗位分配给本店员工。
- 不提供不存在的岗位编辑或删除 API。
- 不能人工修改商家共享库存。
- 不能编辑商家 SPU 或 SKU。
- 只有获得仓库授权后才能查看相应共享库存。

员工移除 API 成功返回 204，不能要求它一定返回 JSON。

岗位名称只是展示分类，不代表管理员权限。不要根据岗位名称修改路由或菜单授权。

### 仓库授权页面

只有当前店主能提交本店仓库授权申请。

仓库授权申请不允许客户端指定其他商家的 warehouse_id 或 shop_id。

未获仓库使用权时，应展示申请入口、状态或无权限说明。

注意：库存读取接口在未授权状态下可能返回 403。不能把所有这类业务权限不足都无条件解释为“用户不再具有 SHOP_OWNER 身份”；应区分当前身份验证结果和具体资源的访问资格。

不得在申请批准前虚构库存数据。

## 四、开发批次 4C：店铺负责人交接

沿用已有后端唯一接口：

PUT /api/v1/shops/{shop_id}/owner

不能新增 GET proposal 接口。

### A. 店主主动提议

只有当前 SHOP_OWNER 能提交：

{"action":"PROPOSE","target_user_id":123}

成功响应包括 proposal_id。

要求：

- 显示目标接任人 ID。
- 明确说明提交提议不代表立即交接完成。
- 展示 proposal_id。
- 支持复制 proposal_id。
- 提醒用户将该编号交给所属商家管理员。
- 不虚构后端不存在的待交接申请列表。

当前没有通用用户搜索接口。若目标用户不是现有本店员工，则由用户手动提供准确的 ID，不得凭用户名猜测或查询不存在的接口。

### B. 商家管理员确认

在已有 `/merchant` 区域增加负责人交接管理入口。

使用：

{"action":"CONFIRM","proposal_id":"<UUID>"}

要求：

- 只有当前商家管理员可确认本商家的店铺交接。
- 管理员需要填写准确的 proposal_id。
- 不允许管理员代替旧店主提交 PROPOSE。
- 409 说明提议失效、重复确认或目标资格已变化。
- 确认成功后刷新店铺列表与当前身份。

### C. 异常强制交接

仅本商家管理员可以使用：

{"action":"FORCE","target_user_id":123,"reason":"明确原因"}

要求：

- 强制交接和正常交接必须在界面上明显区分。
- 原因必填、去首尾空白，最大长度以真实 schema 为准。
- 必须二次确认操作的安全后果。
- 不得把 FORCE 描述成旧负责人已同意。
- 不得向普通用户、员工或其他商家的管理员展示该操作。

交接之后：

- 新负责人必须重新获得真实 SHOP_OWNER 身份。
- 旧负责人使用原 JWT 时，需要重新读取 /auth/me。
- 员工晋升、正常卸任岗位恢复等以真实服务端身份为准。
- 不能在前端自行写入或预测员工身份关系。
- 原有店铺订单和仓库授权不能被前端误认为已经消失。

## 五、路由与权限

- NONE：申请中心。
- MERCHANT_ADMIN：保留原商家管理模块，并增加负责人确认和强制交接。
- SHOP_OWNER：本店管理模块。
- SHOP_EMPLOYEE：本阶段可保留适当的身份工作台，完整订单工作台属于下一阶段。
- PLATFORM_ADMIN：不新增管理界面。

延续已有商家 scope 隔离思想，在店铺模块加入用户、商家、店铺、token 和身份 epoch 检查。

任何角色或店铺变化，都必须立即清除旧角色页面数据、列表、弹窗和过期请求展示。

所有写入权限最终由后端校验，前端路由守卫不构成数据访问安全边界。

## 六、交互质量

每个页面至少处理：

- Loading
- Empty
- 401
- 403
- 404
- 409
- 422
- 网络错误
- 业务成功反馈
- 弹窗重复提交
- 异步响应过期
- 表格分页

不得让失败审批后页面仍假装处于成功状态。

删除员工操作应二次确认。

岗位调整成功后重新读取真实员工关系。

仓库授权申请成功后重新加载申请状态。

## 七、数据类型

继续复用第三阶段已经完成的精确 JSON 读写工具。

- 所有用户、商家、店铺、岗位和申请 ID 使用无精度损失的十进制字符串。
- 后端要求严格整数的 ID，请使用经测试的精确 JSON 数字序列化。
- 不得把超大 ID 转成 Number。
- 日期时间沿用数据库本地 naive DATETIME 展示语义。
- 不把角色、tenant_id 或 owner_user_id 从请求体当作当前授权来源。

## 八、自动化测试

至少覆盖：

1. NONE 提交商家申请。
2. NONE 提交开店申请。
3. NONE 提交员工入职申请。
4. 申请通过后刷新身份。
5. 未获审批前不能访问业务区域。
6. 店主只能看本店申请和员工。
7. 店主批准和驳回员工。
8. 员工移除 204 正常处理。
9. 员工岗位创建和更换。
10. 不允许跨店岗位使用。
11. 未授权仓库时的页面反馈。
12. 仓库授权申请。
13. 授权后库存只读。
14. 店主不能人工调整库存。
15. 正常负责人交接提议与 UUID 展示。
16. 管理员确认正确 UUID。
17. 失效、重放 UUID 返回 409。
18. FORCE 必须有明确原因。
19. 跨商家负责人交接被拒绝。
20. 交接后旧身份数据立即清空。
21. BIGINT 大 ID 请求与响应精度。
22. 前三阶段所有测试不回归。

## 九、真实浏览器及 MySQL 验收

使用隔离测试库完成：

- 从普通用户注册到提交身份申请。
- 商家审批后身份变化。
- 店铺负责人批准员工入职。
- 员工岗位管理及移除。
- 店主申请仓库授权，商家管理员批准。
- 授权之后能够查看同商家的共享库存。
- 正常负责人提议、确认及原岗位恢复。
- 强制负责人交接以及旧权限撤销。
- 两个商家之间不能管理对方店铺。
- 身份变化后旧页面不会显示其他角色数据。
- 401/403/409/422 等失败处理正确。
- 浏览器无 Vue error/warn。

需要平台管理员参与审批的测试，可以通过隔离环境中的真实后端 API 或受控测试脚本执行，不必增加平台前端。

验收结束后清理测试账号、专用数据库和临时服务，不能污染开发业务库。

## 十、构建与交付

执行：

- npm run lint
- npm run typecheck
- npm test
- npm run build

确认商家目录和库存页面仍然通过回归测试。

检查首屏 bundle、公共 chunk 和 CSS，不得为了消除构建提示单纯提高阈值。

更新 Frontend/README.md，说明：

1. 新增页面和路由。
2. 真实 API 契约。
3. 身份申请与审批流。
4. 员工和岗位管理边界。
5. 仓库授权申请与只读库存。
6. 正常交接与强制交接区别。
7. BIGINT、日期和权限隔离。
8. Vitest、构建和浏览器验收结果。
9. 未完成内容和已知风险。

完成本阶段后停止，不自动开发第五阶段订单工作台，也不新增任何后端业务 API。