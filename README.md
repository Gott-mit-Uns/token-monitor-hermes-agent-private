# Token Monitor Hermes：NAS 用量采集 Agent

基于 [Javis603/token-monitor](https://github.com/Javis603/token-monitor) 的 Hermes 采集器，为 NAS 提供 Docker 部署。采集 Hermes 的用量统计并发送到你配置的桌面 Token Monitor Hub。

## 镜像与更新

```yaml
image: ghcr.io/gott-mit-uns/token-monitor-hermes:latest
```

支持 `linux/amd64`（DXP4800）和 `linux/arm64`（DH4300plus）。`latest` 仅在发布前安全检查、构建及两种架构的 Agent 测试成功后更新。固定版本为 `0.54.0-nas.2`，每次构建还保留 `sha-<完整提交 SHA>` 标签。

仓库当前仍为私有，新建镜像按 GHCR 默认规则为私有；请在 GitHub 的 Packages 设置核对镜像可见性。NAS 拉取私有镜像需要预先安全配置只读包访问授权。将源码仓库改为公开不会自动把已有镜像改为公开；镜像可见性需要单独设置为公开，才能免登录拉取。不要把拉取凭据写入 Compose 或提交到 Git。

在原 Compose 所在目录运行：

```sh
docker compose pull
docker compose up -d token-monitor-hermes-agent
```

已有部署迁移时，将 Compose 的 `image` 替换为上述地址，并删除 `build` 配置。保留原来的 `.env`、`state` 挂载、Hermes 只读挂载、设备 ID 和服务名称。镜像更名不要求移动 NAS 目录或改变容器名称。

## 自定义桌面 Hub 中显示的设备名称

当前桌面 Hub 使用 `deviceId` 作为显示名称。直接编辑 Compose 中的环境变量即可，例如：

```yaml
environment:
  TOKEN_MONITOR_DEVICE_ID: DXP4800
```

然后重新创建容器：

```sh
docker compose up -d --force-recreate token-monitor-hermes-agent
```

首次成功上传后，Hub 会显示 `DXP4800`。若 Hub 暂时离线，会在恢复连接后的成功上传中显示；默认每五分钟采集，文件事件也可能提前触发。

**改设备 ID 会创建新的 Hub 记录，旧 `Hermes-NAS-4800` 记录会保留，并不是对原记录原地改名。** 程序不会自动删除旧记录、迁移 Hub 历史或合并设备。不要为普通镜像升级改设备 ID；不同 NAS 使用不同 ID。单纯 `docker restart` 或 `docker compose restart` 不会加载修改后的 Compose 环境变量。

## 部署模板

- `docker-compose.yaml`：DXP4800，默认使用 `/volume1/docker` 和 `Hermes-NAS-4800`。
- `docker-compose.4300.yaml`：DH4300plus，默认使用 `/volume4/docker` 和 `Hermes-NAS-4300`。

DH4300plus 上将模板保存为原项目路径下的 `docker-compose.yaml`，以保持绿联项目管理入口不变。`.env`、`state` 目录和挂载应继续使用原位置。

首次部署，将 `.env.example` 复制为 NAS 本地 `.env`，填写桌面 Hub 地址与真实共享密钥。不要提交 `.env`。

## 数据与发布边界

GitHub 构建只使用提交到仓库的源码与依赖，不连接你的 NAS、不挂载 Hermes 数据库，也不读取 NAS 本地 `.env` 或 `state`。Docker 构建上下文只允许 `app` 中指定的源码、依赖清单和构建脚本；最终镜像只复制 Agent、共享代码和安装后的依赖。

Agent 在 NAS 正常运行时会读取只读挂载的 Hermes 数据库，并将用量记录发送到 `TOKEN_MONITOR_HUB_URL` 指定的 Hub。**这是向你配置的 Hub 同步，与向 GitHub 提交代码或发布镜像是两条独立流程。** 不应将生产 Agent 放在 GitHub 构建中运行；测试只使用仓库里的测试数据。

`.gitignore` 排除 `.env`、数据库、状态目录、日志、备份和依赖目录。发布前脚本检查可达 Git 历史中的运行数据文件与常见密钥格式，并且只输出路径和问题类型，不输出命中的值。此检查不能识别所有秘密，也不能阻止手工强制添加数据文件，应结合人工审阅。

本仓库包含 NAS 型号、示例目录结构及历史部署验证说明；公开仓库会公开这些说明和 Git 历史。详见 `SECURITY.md`。

## 运行保护与健康检查

- 为读取 NAS ACL 保护的目录，使用 root 身份，但移除全部能力，仅恢复 `DAC_READ_SEARCH`。
- 根文件系统只读，Hermes 目录只读，写入状态放在单独的 `state` 挂载中。
- 五分钟采集兜底，文件事件延迟 30 秒触发。
- 分别记录成功采集与上传时间，不在健康状态中保存有效载荷或凭据。

健康检查命令：

```sh
docker exec token-monitor-hermes-agent node src/agent/nasHealth.js
```

输出分别报告采集和上传为 `ok` 或 `stale`。默认过期阈值为 15 分钟，或三个更长的采集间隔。采集停滞、数据库不可读或进程缺失会导致 Docker 健康检查失败；只有上传过期不会使其失败，以免桌面 Hub 休眠引发误判。Docker 不会仅因容器不健康而自动重启。

## 升级与回退

升级前短暂停止 Agent，备份本地配置与状态，并保留旧镜像。回退时恢复匹配的配置和状态，以及固定版本镜像；不要使用 `docker compose down -v`。

从原本地构建迁移后，旧镜像 `token-monitor-hermes-agent:0.54.0-nas.1` 仍可作为首次迁移的回退选择。不要改变绿联项目原有 Compose 文件路径。Hub 建议使用稳定的局域网地址。

## 构建来源

上游 v0.54.0，提交 `fce070c789ae8b1ca59be3ce7c09fd8301d6f631`。NAS 镜像版本 `0.54.0-nas.2` 调整镜像发布方式，Agent 上游应用版本仍为 0.54.0。

Dockerfile 使用摘要固定的 Node 22 Bookworm slim，构建时执行官方 `ensure:tokscale` 步骤。桌面更新器与 Discord 依赖会被移除，共享原生依赖保留。启动时不安装依赖。

许可证：[MIT](LICENSE)。上游更新仍需合并并验证，再发布新的 NAS 镜像；`latest` 不会自动拉入上游代码。
