# Token Monitor NAS

基于 [Token Monitor 官方项目](https://github.com/Javis603/token-monitor) 的 NAS Docker Agent，采集 Hermes 用量并同步到自己配置的桌面 Hub。当前官方源码基线是 `v0.63.1`，NAS 发布版本是 `v0.63.1-01`。官方提交及源码范围见 [UPSTREAM.md](UPSTREAM.md)。

Windows EXE Adapter 已迁移到独立公开仓库 [token-monitor-adapter](https://github.com/Gott-mit-Uns/token-monitor-adapter)，包括源码、Windows 构建工作流与历史 `adapter-v*` Release。本仓库只维护 NAS Docker Agent；Adapter 更新与下载请使用新仓库。

## 镜像、版本与更新

镜像继续使用现有公开的 GHCR 包名，以免已有 NAS 部署的拉取地址失效：

```yaml
image: ghcr.io/gott-mit-uns/token-monitor-hermes:latest
```

`latest` 指向最近一次通过双架构构建和测试的版本；也可以固定到 `ghcr.io/gott-mit-uns/token-monitor-hermes:v0.63.1-01`。支持 `linux/amd64` 与 `linux/arm64`。完整版本规则写在 [VERSIONING.md](VERSIONING.md)：官方版本作为前缀，每次引入新官方版本时 NAS 修订号从 `-01` 开始；同一官方版本上的 NAS 修改递增为 `-02`、`-03`。发布检查会校验版本号、Dockerfile 与官方 `app/package.json` 一致。固定镜像标签不覆盖，GitHub Release 与 Agent 上报使用相同版本。

拉取并重新创建当前服务：

```sh
docker compose pull
docker compose up -d token-monitor-nas
```

GitHub 上发布代码和镜像不会自动替 NAS 拉取镜像。旧的固定标签与提交 SHA 标签保留作回退用途。

## Compose 与设备名称

仓库提供 [DXP4800 模板](docker-compose.yaml) 和 [DH4300plus 模板](docker-compose.4300.yaml)。Compose 项目、服务与容器均名为 `token-monitor-nas`；两个模板继续使用各自原有的状态目录、Hermes 只读挂载和设备 ID。按实际路径调整后，将模板放到 NAS 上的项目目录，`.env` 留在该目录。首次部署可参考 [.env.example](.env.example)，不要提交实际 `.env`。

桌面 Hub 显示的名称由 `TOKEN_MONITOR_DEVICE_ID` 决定。DXP4800 模板为 `DXP4800`，DH4300plus 模板为 `DH4300Plus`。改动设备 ID 后须 `docker compose up -d --force-recreate token-monitor-nas`；Hub 会将新 ID 当成另一台设备，旧记录不会自动合并。普通版本升级不要改这个值。

默认每 5 分钟采集一次，也监听 Hermes 文件变化；文件事件防抖 60 秒。Compose 为 `Asia/Shanghai` 时区，保留 512 MiB 内存上限、只读根文件系统、Hermes 只读挂载、最小能力以及独立状态目录。额度和项目统计默认关闭，历史和会话归档开启。Agent 对未变化的记录去重，并保留心跳；上传总时限默认 30 秒。

健康检查：

```sh
docker exec token-monitor-nas node src/agent/nasHealth.js
```

输出会分别报告采集和上传状态。Hub 暂时离线时上传状态可能过期；采集停滞才使 Docker 健康检查失败。Docker 不会因健康检查失败自行重启。

## 状态迁移与回退

从 `0.54.1` 升级到本版时，官方会将 `session-usage-archive.json` 迁移到 SQLite，迁移成功后删除旧 JSON。**升级前停止 Agent，备份整个状态目录和 Compose、`.env`，并保留旧镜像。** 回退旧镜像时必须一并恢复迁移前的状态目录，单纯切换镜像标签不足以恢复旧版归档。不要执行 `docker compose down -v`。镜像升级不需要变更现有设备 ID 或移动状态目录。

## 数据与发布边界

GitHub Actions 只构建仓库源码和测试数据，不连接 NAS、不读取本地 `.env`、Hermes 数据库或状态目录。实际运行的 Agent 会读取 NAS 上的 Hermes 只读挂载，并向 `TOKEN_MONITOR_HUB_URL` 指定的 Hub 上传统计。发布前脚本检查常见密钥和运行数据路径；详情见 [SECURITY.md](SECURITY.md)。不要提交凭据和个人运行数据。

许可证：[MIT](LICENSE)。
