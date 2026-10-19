# 前端第六阶段交付：综合验收与部署文件（2026-10-19）

文档日期：2026-10-19。本报告只陈述有证据的结论，严格区分「已实现」「已实测」「未验收」。未列出的能力不视为完成。

## 1. 范围与证据

本阶段新增审计查询前端模块，并完成前端质量、权限端到端、部署草案与清理。证据文件：`PHASE6_FRONTEND.json`、`PHASE6_BACKEND_FINAL`、`PHASE6_ACCEPTANCE`、`PHASE6_BROWSER`、`PHASE6_BROWSER_EXTRA`、`PHASE6_IDENTITY_BROWSER`、`PHASE6_SCHEMA`、`PHASE6_BUNDLE`、`PHASE6_QUERY_BEFORE/AFTER`、`PHASE6_MINIMUM_PRIVILEGES`、`PHASE6_CLEANUP`、`PHASE6_DEPLOYMENT_STATIC`、`PHASE6_HTTP_RESULTS`、`PHASE6_DATABASE_PROOF`。完整文件清单以 `PHASE6_FILES.json` 为准。证据工具日志为 DeepSeek runs（frontend `a0202647c20c48a58a71eade2281b4ef`，格式修正后的最终检查见 PHASE6_FRONTEND.json，初 backend `bac4d8fba2e14f54a7acd637c0a642c4`），主代理核验修正，未使用 Luna。

## 2. 修改文件分组

- 审计前端（新增）：`Frontend/src/modules/audit/{api.ts,AuditPage.vue,api.spec.ts,AuditPage.spec.ts}`。
- 前端基础设施（修改）：路由、`AppShell` 移动端 `select` 导航、`main.ts`、`styles/main.css`、新增 `styles/element-plus.ts`。
- 可访问性与错误：`OrderCreatePage` 表格横向容器与 checkbox/qty aria；`errors.ts` 503 通用服务提示。
- 前端测试修复：`EmployeeRead`、workspace、`MerchantOrders`、`AppShell`、`roleGuard` 修正真实路由警告，新增审计守卫测试，不屏蔽 `warn`。
- 后端（修改）：仅 `services/orders.py` 列表批量取明细，`test_orders.py` 增加性能快照用例；未改写事务锁。
- 交付文件（新增）：根 `README`、`deploy/README.md`、`deploy/{compose.yaml,mysql-init.sh,.env.example,.gitignore}`、backend/Frontend Dockerfile 与 `.dockerignore`、`Frontend/nginx.conf`、`.gitattributes`；`.gitignore` 排除临时 `.venv312`。
- QA 脚本与截图：`qa/phase6_env.py`、`qa/phase6_checks.py`、`qa/phase6_browser.py`、`qa/phase6_browser_extra.py`、`qa/phase6_identity_browser.py`、`qa/phase6_modal_env.py`、`qa/phase6_modal_browser.py` 与 `PHASE6*.json`、`docs/phase6` 截图。

## 3. 审计 API 契约（已实现，已实测）

- 路由：merchant 仅 `/merchant/audit`，owner 仅 `/shop/audit`。
- 复用 `GET /api/v1/audit/events`，参数 `limit,offset,event_type,target_type,start_at,end_at`。
- 返回 `items,limit,offset,total`；item 字段 `id,actor_user_id,event_type,target_type,target_id,outcome,event_data,created_at`。
- IDs 按字符串展示，naive 时间按原文展示。
- `event_data` 仅以文本渲染，不使用 `v-html`；不从 `target_id` 推断归属；页面提示独立审计可能丢失记录。

## 4. 完整测试表

| 项 | 结果 | 状态 |
|---|---|---|
| npm 4 项（lint/typecheck/test/build） | 均 exit 0 | 已实测 |
| ESLint | 0 errors 0 warnings | 已实测 |
| TypeScript typecheck | pass | 已实测 |
| Vitest | 28 files，179 pass，0 fail，0 skip | 已实测 |
| Vue/Router warn | 无 | 已实测 |
| 后端首次受限 MySQL | 106 pass，3 fail，0 skip（1419 创建故障触发器权限） | 已实测 |
| Python 3.12.14 Windows 独立 venv 临时库全量 | 110 pass，0 fail，0 skip；1 Starlette DeprecationWarning 保留 | 已实测 |
| HTTP 端到端 16 组 | 全通过 | 已实测 |
| Chromium 1440x1000 / 390x844 | 48 角色页面视图，无 document 横向溢出、Vue error/warn 0 | 已实测 |

前端首轮新增测试存在 mock/fixture 选择器与 naive datetime 测试错误，已修复后达到上述最终结果；初跑失败如实保留，不掩盖。

## 5. 权限端到端（已实测）

16 组 HTTP 覆盖：注册审批完整种子链、5 角色后端边界、跨商家商品/审计、跨店订单与员工隔离、自定义超管岗位不扩大权限、同商家 2 店共享库存抢 1 件（201/409）、多 SKU 回滚、complete 不二扣、cancel 只回补 1、改价下架快照不变、员工移除旧 JWT、店主交接失权。

浏览器侧：真实延迟订单 GET + 移除员工 + 移动导航，403 后释放旧 response 且不恢复数据；invalid JWT 真实 401 → expired 登录；仓库未授权 403 保留页面身份；审批 approve/reject 弹窗必填原因。

## 6. 性能指标（单次本机，非压测）

- 100 隔离订单 fixture，limit 1/20/50 的 SQL 数由 3/22/52 降为 3/3/3（不含鉴权）。
- 50 行 21.22ms → 3.37ms 仅参考。
- main JS 152.90kB（gzip 51.81）；初始 preload JS 428.99kB（上阶段 427.73）；主 CSS 149.11kB（gzip 23.58，原 390.02/gzip 55.15）。
- AuditPage 6.83kB 懒加载；create 21.35（gzip 7.27）；detail 11.35（gzip 4.13）；未上调阈值。

数据库：MySQL 实际 9.7.2，20 tables，head `20261017_phase12`，ORM diff `[]`，OpenAPI 仍 46 业务 + health，历史迁移未改。

## 7. 部署程度（未验收）

- 已提供 Compose/Nginx/Dockerfile 等交付文件；Yaml 解析、path+dependency+env 静态检查、Ubuntu `sh -n` 通过。
- 未执行：`docker compose config/build/up`、`nginx -t`、MySQL 8.4、容器全链路、备份恢复。**不能称部署成功**。
- 目标设计：Compose 中 nginx、history、api 不重放；backend 非 root；mysql 8.4 volume；迁移先行；app 仅 DML、schema DDL 分离；默认只绑定 127.0.0.1:8080，用于本机演示，生产 TLS 外置未配。
- 浏览器 48 视图来自本地 Vite，非 Nginx 生产 dist；生产 dist 经 Nginx 未验收。

## 8. 风险

- sessionStorage 存在 XSS 风险；独立审计不保证完整，浏览器记录丢失需人工核对（实测代理两次同 SHA，backend 200/200→browser 503/200、201/201→503/201，DB 每 key 仅 1 单/流水）。
- backend 依赖范围未完整锁定。
- 非真实支付物流，非高并发承诺。
- Firefox/WebKit 未验收；非完整设备矩阵。
- MySQL 8.4 未实测；Python 3.12 已实测 Windows，Ubuntu 仅 WSL shell syntax，未跑完整应用；Node 24.16/npm 12 符合声明。
- 未给 app SUPER、未改全局；另用仅 DML 新临时账号完成真实 HTTP 登录、身份、审计、库存、下单、取消；CREATE TABLE 被 1142 拒绝。

清理：本阶段专用数据库及临时账号已删除并验证不存在，临时账号删除，6179/8018/8019/8020 端口关闭，browser contexts 关闭；开发库未修改，未删 volume。

## 9. 演示结论

可作本机作品集演示；已具备部署交付文件，目标容器环境待验收。根 `README` 与 `deploy/README` 已说明不把生产草案当作已部署。

**阶段 6 完成，停止。**
## 10. 验收材料与精确边界

- [前端最终四项检查](PHASE6_FRONTEND.json)、[后端最终全量结果](PHASE6_BACKEND_FINAL.json)、[16 组 HTTP 回归](PHASE6_ACCEPTANCE.json)。
- [桌面/移动 48 视图](PHASE6_BROWSER.json)、[额外权限、弹窗、存储丢失检查](PHASE6_BROWSER_EXTRA.json)、[真实身份变更与 401](PHASE6_IDENTITY_BROWSER.json)。
- [静止动画后重新截图并实际批准/驳回的移动弹窗](PHASE6_MODAL_VERIFICATION.json)：390px 视口内真实对话框宽约 358.8px；首次截图捕获了过渡动画，已补验并替换。
- [最小权限应用实测](PHASE6_MINIMUM_PRIVILEGES.json)、[迁移结构与时区](PHASE6_SCHEMA.json)、[构建全部 chunk 清单](PHASE6_BUNDLE.json)。
- [查询优化前](PHASE6_QUERY_BEFORE.json)、[查询优化后](PHASE6_QUERY_AFTER.json)、[幂等数据库证据](PHASE6_DATABASE_PROOF.json)、[清理和真实代理重试证据](PHASE6_CLEANUP.json)。
- [部署静态检查](PHASE6_DEPLOYMENT_STATIC.json)、[具体修改文件清单](PHASE6_FILES.json)、[项目总览](../README.md)、[部署方法](../deploy/README.md)。

同一业务的结果未知时保留请求键与原参数，仅用户明确操作后原键重试，不自动换键或自动重发；已确认的业务失败显示服务端错误。完成/取消结果未知先 GET 核对状态。库存调整与订单创建的真实响应丢失、原键重试均已验证；故障注入清除本地订单记录后，刷新没有额外 POST，页面提示人工核对。这不等于在浏览器记录永不丢失或所有业务之间具有 exactly-once 保证。

本次未添加依赖到前后端项目声明。另建 Python 3.12 验收虚拟环境，并仅在其中安装项目声明的开发依赖和用于 YAML 静态解析的 PyYAML；浏览器沿用本机 MCP 的 Playwright 运行时。部署文件未执行安装或容器启动。QA 脚本是本机受控验收辅助工具，依赖已有 MCP 临时库工具与环境变量，不是普通用户的生产业务入口。

截图：[商家审计桌面](docs/phase6/merchant-audit-1440.png)、[商家审计移动端](docs/phase6/merchant-audit-390.png)、[库存原键重试](docs/phase6/inventory-retry-mobile.png)、[订单原键重试](docs/phase6/order-retry-desktop.png)、[移动审批弹窗](docs/phase6/warehouse-approve-dialog-mobile.png)、[移除员工后的旧响应隔离](docs/phase6/identity-change-stale-response.png)。第五阶段订单完成截图仍作为历史演示材料保留，不能替代第六阶段的实测报告。

