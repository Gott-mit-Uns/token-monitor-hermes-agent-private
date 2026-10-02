# 官方源码基线

- 仓库：<https://github.com/Javis603/token-monitor>
- 官方 tag：`v0.65.0`
- 官方提交：`db325fdf46ea7f7328feeb47a4f2005fe339909f`
- NAS 发布号：`v0.65.0-01`
- 源码范围：`app/src/shared`、官方 `app/src/agent/agent.js`、`runtime.js`、`seedClients.js`、相关构建脚本和依赖清单；NAS 专用代码在 Agent 包装、Docker、Compose 和发布检查中。

迁移重点：官方新版将 `session-usage-archive.json` 迁至 `session-usage-archive.sqlite`，成功后会移除旧 JSON。部署前须备份整个状态目录；回退到旧镜像时也必须恢复迁移前的状态副本，不能只切换镜像标签。

本次 v0.65.0 升级不改变归档存储格式；保留 NAS 上传中止信号、去重和健康检查。Tokscale 继续使用 npm 官方二进制，未切换为桌面项目的 fork 覆盖包。

保留 NAS 的 `verify-vendored-tokscale.js` 验证脚本：官方新增 Muse/FX 与 fork 分组契约不属于本容器的 Hermes 采集范围，避免对 npm 二进制误用 fork 专用断言。
