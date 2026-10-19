# 综合验收矩阵（2026-10-19）

状态以本阶段实际执行记录为准；第五阶段报告作为历史资料保留。没有新增业务表或业务 API，仍为 20 张表、46 个业务 API 加 health，Alembic head `20261017_phase12`。

| 范围 | 方法与结果 | 证据 |
| --- | --- | --- |
| 前端质量 | lint 0 错误/0 警告；类型检查与构建成功；28 文件、179 Vitest 通过，0 失败/0 跳过；无 Vue/Router 非预期警告 | [四项最终输出](../Frontend/PHASE6_FRONTEND.json) |
| 后端完整回归 | Python 3.12.14 Windows + 真实 MySQL 9.7.2，110 通过、0 失败/0 跳过；1 项 Starlette 弃用警告 | [完整 Pytest](../Frontend/PHASE6_BACKEND_FINAL.json) |
| 特殊测试权限 | 首次受限账号全量 106 通过、3 项 MySQL 1419 失败；最终受控管理连接仅新建临时库，无应用 SUPER、无全局设置变更 | [初始记录](../Frontend/PHASE6_BACKEND_INITIAL.json)、[最终记录](../Frontend/PHASE6_BACKEND_FINAL.json) |
| 最小应用权限 | 仅 SELECT/INSERT/UPDATE/DELETE，真实登录、身份、审计、库存、下单及取消成功；建表被 1142 拒绝 | [最小权限实测](../Frontend/PHASE6_MINIMUM_PRIVILEGES.json) |
| 组织与授权完整闭环 | 注册、商家申请、开店、默认岗位、员工入职、仓库授权、商品上架与零库存初始化，随后库存和订单流程 | [隔离种子脚本](../Frontend/qa/phase6_env.py)、[16 组 HTTP](../Frontend/PHASE6_ACCEPTANCE.json) |
| 五角色边界 | NONE、员工、店主、商家、平台的真实 API 拒绝；跨商家商品、审计、跨店订单/人员隔离；岗位名称不扩大权限 | [HTTP 回归](../Frontend/PHASE6_ACCEPTANCE.json)、[浏览器边界](../Frontend/PHASE6_BROWSER_EXTRA.json) |
| 事务与库存 | 两店抢一件返回 201/409；多 SKU 缺货整体回滚；完成不二次扣减、取消仅一次回补；改价下架不改历史快照 | [HTTP 回归](../Frontend/PHASE6_ACCEPTANCE.json)、[后端完整测试](../Frontend/PHASE6_BACKEND_FINAL.json) |
| 结果未知与重试 | 后端实际提交后代理返回 503；库存/订单用户显式重试原键，参数 SHA 相同且数据库仅一笔；本地记录丢失刷新不自动 POST | [代理重试](../Frontend/PHASE6_CLEANUP.json)、[数据库证据](../Frontend/PHASE6_DATABASE_PROOF.json)、[记录丢失](../Frontend/PHASE6_BROWSER_EXTRA.json) |
| 身份即时变化 | 移除员工后原 JWT 失权，导航到 403；释放迟到的真实订单响应不会恢复旧数据；负责人交接后旧 owner 失权 | [真实浏览器](../Frontend/PHASE6_IDENTITY_BROWSER.json)、[HTTP 回归](../Frontend/PHASE6_ACCEPTANCE.json) |
| Chromium 响应式 | 1440×1000 与 390×844 共 48 角色页面视图，整页横向溢出为零、Vue 错误/警告为零；登录注册另检查 | [视图清单](../Frontend/PHASE6_BROWSER.json) |
| 移动弹窗与长数据 | 审批弹窗静止动画后宽约 358.8px，实际批准仓库授权及驳回店铺申请；长商品/SKU、最大目录单价、BIGINT ID 显示 | [移动弹窗](../Frontend/PHASE6_MODAL_VERIFICATION.json)、[长数据检查](../Frontend/PHASE6_BROWSER_EXTRA.json) |
| 迁移与模型 | 全量测试覆盖空库顺序升级、回退/重新升级及历史数据兼容测试；当前真实库 ORM 差异为空 | [全量结果](../Frontend/PHASE6_BACKEND_FINAL.json)、[结构与时区](../Frontend/PHASE6_SCHEMA.json) |
| 列表性能 | 100 隔离订单数据，limit 1/20/50 查询数 3/22/52 → 3/3/3，不含鉴权；50 行本机单次约 21.22 → 3.37ms；非压测结论 | [优化前](../Frontend/PHASE6_QUERY_BEFORE.json)、[优化后](../Frontend/PHASE6_QUERY_AFTER.json) |
| 静态资源 | 主 JS 152.90KB，初始 JS 含 preload 428.99KB；主 CSS 149.11KB（原 390.02KB）；审计页懒加载，未提高告警阈值 | [全部资源清单](../Frontend/PHASE6_BUNDLE.json) |
| 数据与资源清理 | 仅本次专用库和临时账号；专用库不存在、已启动端口关闭；无开发库/旧项目业务数据或 volume 删除 | [清理核验](../Frontend/PHASE6_CLEANUP.json)、[弹窗环境状态](../Frontend/PHASE6_MODAL_FIXTURE.json) |
| MySQL 8.4 | 未验收；仅目标 Compose 配置，实测服务器为 9.7.2 | [部署程度](../Frontend/PHASE6_DEPLOYMENT_STATIC.json) |
| Ubuntu/Linux 完整运行 | 未验收；Ubuntu WSL 只运行 shell 语法检查，完整应用实测在 Windows | [部署程度](../Frontend/PHASE6_DEPLOYMENT_STATIC.json) |
| Docker/Nginx | 配置 YAML、路径、依赖及 shell 静态检查通过；compose config/build/up、nginx -t、生产 dist 反代及容器启动未执行 | [静态检查](../Frontend/PHASE6_DEPLOYMENT_STATIC.json)、[部署说明](../deploy/README.md) |
| 其他浏览器/生产规模 | Firefox/WebKit、全部移动设备矩阵、生产压力、备份恢复、TLS 未验收 | [阶段报告](../Frontend/README.md) |

SQL 查询数来自实际数据库执行事件，耗时只是本机单次观测。浏览器视图检查使用 Vite 和真实 FastAPI，不能替代 Nginx 生产构建验收。sessionStorage 的未知请求记录可能丢失；独立审计可能缺失；二者均不具备不可篡改或法定完整历史保证。Docker 管理员可读取容器环境变量，正式发布前还需限制运维权限、配置 TLS 并评估秘密管理与依赖锁定。

当前适合本机作品集流程演示，目标生产环境仍有上述待验收项。阶段六结束，不自动扩展支付、物流或 AI 功能。
