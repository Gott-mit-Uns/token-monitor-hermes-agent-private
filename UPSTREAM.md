# 官方源码基线

- 仓库：<https://github.com/Javis603/token-monitor>
- 官方 tag：`v0.63.1`
- 官方提交：`e38f60a94ce6310341f8ad2de6888f1ec51dd755`
- NAS 发布号：`v0.63.1-01`
- 源码范围：`app/src/shared`、官方 `app/src/agent/agent.js`、`runtime.js`、`seedClients.js`、相关构建脚本和依赖清单；NAS 专用代码在 Agent 包装、Docker、Compose 和发布检查中。

迁移重点：官方新版将 `session-usage-archive.json` 迁至 `session-usage-archive.sqlite`，成功后会移除旧 JSON。部署前须备份整个状态目录；回退到旧镜像时也必须恢复迁移前的状态副本，不能只切换镜像标签。
