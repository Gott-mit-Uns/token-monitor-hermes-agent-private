# 项目范围与发布

本仓库 `Gott-mit-Uns/token-monitor-nas` 只维护 NAS Docker Hermes Agent。Windows EXE Adapter 已迁出到 `Gott-mit-Uns/token-monitor-adapter`；Adapter 代码、测试、工作流与 `adapter-v*` Release 必须在新仓库维护和发布，不得重新混入本仓库。

更新前阅读 `README.md`、`UPSTREAM.md`、`VERSIONING.md` 与 `SECURITY.md`。NAS 版本遵守官方基础版本加两位 NAS 修订号的规则。保持现有只读部署、设备 ID、持久化目录及超时与去重边界。发布前执行版本校验、NAS 测试与公开仓库安全检查；发布不表示已部署到 NAS。

不要提交真实凭据、`.env`、Hermes 数据、运行状态或日志。代码和 Docker 发布只能包含仓库源码与合成测试。
