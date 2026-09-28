# NAS 版本规则

NAS 镜像和 GitHub Release 使用同一个版本号：`v<官方版本>-<两位 NAS 修订号>`。

- 官方基础版本取自 `app/package.json`，必须是完整的三段数字版本，例如 `0.63.1`。
- 每次引入一个新的官方版本，NAS 修订号从 `01` 开始：官方 `v0.63.1` → NAS `v0.63.1-01`；以后官方 `v0.64.1` → NAS `v0.64.1-01`。
- 官方基础不变、仅修改 NAS 封装或补丁时，修订号依次增加：`v0.63.1-02`、`v0.63.1-03`。不可覆盖已发布的固定镜像标签或 Release。
- `nas-version.txt` 是 NAS 发布号的唯一输入；`app/package.json` 保留官方源码版本。Dockerfile 默认构建号必须与 `nas-version.txt` 一致。
- Docker 固定镜像标签、OCI 版本标签、GitHub Release 标签和 Hub 上报的 NAS Agent 版本必须完全一致。`latest` 只指向最近通过双架构测试的镜像。
- 每次更新同时记录官方 tag 与提交 SHA、NAS 补丁和迁移说明，运行 `node scripts/check-nas-version.js` 与 NAS 测试，再发布。
- 早期 `0.54.1` 等旧标签是历史版本，保留回退用途，不沿用其编号规则。

源码升级时保留 NAS 专用 Docker、健康检查、超时、去重、权限和持久化边界；先在状态副本上验证归档迁移。GitHub 发布不会自动更新 NAS 容器。
