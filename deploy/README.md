# 部署说明（目标环境与验收边界）

> 本机与 WSL 无 Docker/Nginx 命令，以下命令为**目标流程**，容器启动与生产 Linux 链路**尚未验收**；MySQL 8.4 为目标配置，不是已通过。界面仅监听 `127.0.0.1:8080`。

## 1. 组成

`deploy/compose.yaml`：`mysql`（8.4）+ `migrate`（Alembic 一次性任务）+ `backend`（Python 3.12-slim，非 root）+ `nginx`（1.28-alpine，前端 Node 24 构建产物）。仅 `127.0.0.1:8080` 映射 nginx；db/backend 不对宿主开放；`mysql-data` 持久化卷。`mysql-init.sh` 仅在空卷首次初始化时创建 app 账号（仅 DML）。migration 账号为 schema 级 ALL（不授予 SUPER）；app 不可与 root/migration 同名。

## 2. 启动

```bash
# 在项目根目录；env 文件权限 600 且不提交
cd <项目根>
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
# 用本机安全编辑器填写空秘密；不要把真实值写入命令历史或 VITE_ 变量
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet  # 不要输出含 secret 的 config
docker compose --env-file deploy/.env -f deploy/compose.yaml up --build -d
```

依赖顺序：mysql healthy → migrate completed → backend healthy → nginx。**迁移失败必须停查 `logs`，不要重建 DB**。

## 3. 环境变量（`deploy/.env`，示例见 `.env.example`）

- `MYSQL_DATABASE`
- `MIGRATION_DB_USER` / `MIGRATION_DB_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `APP_DB_USER` / `APP_DB_PASSWORD`
- `AUTH_JWT_SECRET`（长随机，≥32）

时区：mysql 与 backend 均为 `Asia/Shanghai`，DB `server default +08:00`。

## 4. 更新与迁移

```bash
# 先备份（见第 8 节），进入维护窗口。先构建新镜像，再执行新版本迁移。
docker compose --env-file deploy/.env -f deploy/compose.yaml build
docker compose --env-file deploy/.env -f deploy/compose.yaml stop backend nginx
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm migrate
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d backend nginx
```

## 5. 权限

- app 用户仅 DML；migration 用户 schema ALL，无 SUPER。
- 应用无需 root 运行。
- 平台管理员：先**普通注册**得到账号，再执行：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml exec backend \
  python -m scripts.init_platform_admin --username <exact> --confirm-username <exact>
```

CLI 仅初始化一个已有、有效且无冲突身份的账号，不执行商家审批。没有公开注册平台管理员的 API；审核接口仍由 JWT 和平台权限保护，可用受控 API 客户端调用。

## 6. 时区与日志

- 本部署目标配置为 `Asia/Shanghai`，时间为 **naive 本地时间**。
- 查看日志：`docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f <service>`。
- 健康检查：`GET /api/v1/health` **仅表示进程存活，不代表 DB ready**。

## 7. 健康检查与 Nginx

`Frontend/nginx.conf` 做 history 回退，`/api` 原样反代；`proxy_next_upstream off`，**不自动重放写请求**。

## 8. 备份与恢复

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --routines --triggers --events "$MYSQL_DATABASE"' \
  > backup.sql
```

注意 shell 单引号；执行者须为受控 root（仅备份，非 app 身份）。恢复须**离线到新专用库演练**，不直接覆盖开发库，不执行 `down -v`。

## 9. 安全须知

- secret 文件权限 600，不提交版本库；`docker inspect` 可看到 env，管理员权限需受控。
- JWT 存于前端 sessionStorage，存在 XSS 风险；CSP 不能绝对防护，勿夸大。
- TLS 与 secret manager 外置为未来项。
- 后端依赖未全锁 transitive，可复现性有风险。

## 10. 已知限制

Docker/Nginx 未本机验收；MySQL 8.4 容器链路未验证；无生产 Linux 验收。**不称生产级**。

已有 volume 不会重跑 mysql-init.sh；修改 `.env` 不会自动修改库内账号密码。账号轮换需由受控 DBA 明确执行并验证，不能删除 volume 重新初始化。迁移账号仅用于维护阶段，不进入应用容器；应用账号没有 DDL、TRIGGER 或 SUPER 权限。不要在生产实例开启测试所需的宽松触发器设置。

当前验证只有 YAML 解析、依赖/路径/秘密隔离检查和 Ubuntu WSL `sh -n`；`docker compose config`、Docker build/up、`nginx -t`、备份恢复演练均未实际执行。发布前还需在独立主机验证 MySQL 8.4 全量测试、镜像构建、history 路由刷新、TLS 及迁移恢复。`/api/v1/health` 仅进程存活；应另行监控 DB 连接、迁移状态和错误日志。备份可能含业务数据，请在受保护目录保存并设置最小访问权限。


初始化脚本同时创建迁移账号与应用账号，不使用官方 MYSQL_USER 的隐式 schema 授权。脚本只读取 partial_revokes：关闭时转义数据库名下划线、开启时使用字面数据库名，以避免授权匹配相似库；不修改全局变量。依据 [MySQL 8.4 GRANT](https://dev.mysql.com/doc/refman/8.4/en/grant.html) 与 [partial_revokes](https://dev.mysql.com/doc/refman/8.4/en/partial-revokes.html)。此生成逻辑已做离线 shell 检查，实际 MySQL 8.4 初始化仍未验收。
