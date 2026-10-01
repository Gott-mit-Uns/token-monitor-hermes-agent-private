# Token Monitor Adapter for Windows

保持 Token Monitor 原界面和多设备用量查看，使用本机缓存减少热点下载。此工具不是 Token Monitor 采集器，需同时运行原客户端。

## 使用

从本仓库 `adapter-v*` Release 下载 `TokenMonitorAdapter.exe` 和 `SHA256SUMS.txt`。支持 Windows 10/11 x64，需要 Microsoft Edge WebView2 Runtime 及系统 .NET Framework 4.7.2 以上，不需要 Python。首次运行打开设置，填写 HTTPS Hub 地址与同步密钥。密钥留空可加密复用当前用户 Token Monitor 的已有密钥，保存后不回显。

默认压缩下载 10 分钟、客户端上报 30 分钟。勾选“接入 Token Monitor”会先备份配置，再将原客户端 Hub 地址设为本机地址；需要重启原客户端使设置生效。取消勾选时不会调整原客户端，因此显示的上报周期只是适配器保存的目标值。

关闭窗口隐藏到托盘。托盘可打开窗口、手动同步或退出整个适配器。重复运行 EXE 会打开已有窗口。开启登录启动后，EXE 被复制到 `%LOCALAPPDATA%\Programs\TokenMonitorAdapter`，以 `--background` 运行；关闭开关移除当前用户启动项。

配置与运行数据在 `%LOCALAPPDATA%\TokenMonitorHotspotAdapter`。升级时从托盘退出，替换 EXE 后重新运行；若使用登录启动，再保存设置以更新固定目录中的 EXE。移动原始下载文件不会破坏已设置的登录启动。

## 流量与可靠性

计量按接口累计记录请求正文与收到的响应正文；gzip 下载按压缩大小记录，不含 HTTP 头、TLS、重传和其他进程流量。失败次数是历史累计；“无待上报”不是每一条历史数据均已送达的证明。

失败上报写入本地 pending 文件，只保留最新快照。互斥锁防止旧成功清除新上报，正常退出与重启保留缓存和计量。网页轮询只读本地状态，本地 SSE 不订阅远端 SSE。普通读取在缓存到期时可能合并发起该端点应有的一次下载。

远端凭据与本地客户端认证分离。密钥使用当前 Windows 用户 DPAPI 加密，不能复制到其他账号后解密。仅监听 127.0.0.1。设置桥接仅在内嵌窗口提供，普通浏览器不能修改设置。程序不自动更新、不发送遥测。

## 构建与测试

在 Windows x64 Python 3.12 独立环境执行：

```powershell
python -m pip install -r requirements-build.txt
python -m unittest test_adapter test_settings
python -m PyInstaller --clean --noconfirm TokenMonitorAdapter.spec
.\dist\TokenMonitorAdapter.exe --self-test --root "$env:TEMP\adapter-smoke"
```

自检在指定目录产生 JSON 回执，必须包含 `ok=true`、`frozen=true`。`--root` 用于隔离测试；默认本地端口不自动改变，被占用时不会终止其他程序。

源码、合成测试与通用资源采用仓库 MIT 许可。依赖沿用各自许可。发布工作流仅打包明确列出的页面与图标，不纳入本机配置、凭据、缓存、测试回执、审计文件或个人截图。Windows EXE 暂未进行代码签名。

## 回退

迁移前保留旧适配器程序、启动项及数据目录备份。回退先退出 EXE，再恢复旧程序和原启动项，保留最新缓存与计量；必要时恢复 `backups` 中接入前的 Token Monitor 设置并重启原客户端。旧版读取客户端密钥，新版加密密钥不改变原凭据。不要在旧版与新版同时占用同一端口时启动。
