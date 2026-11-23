# MEB EMU · Mac mini 交接

## 接收内容

版本：0.1.0-preview.1。项目目录：`~/code/MEB-EMU`。

公开工程包包含源码、QEMU 11.1.0 上游源码和平台覆盖代码、Windows 运行载体、依赖锁、测试与必要文档。原厂固件、原厂 JNI／字体／Java 文件、实车编码、账号和运行快照不在包内；后续仍使用本机私有输入目录 `var/inputs/`。

ZIP：`dist/MEB-EMU-0.1.0-preview.1.zip`。

SHA256：`3d7249e2cf4f7dba4e15c43d86129c0bf9d9b357c1cbcc3ef7ca79025202d12f`。

## 已确认结果

- Windows 工程预览已通过 17 项单元测试、新路径独立安装、32 个依赖文件和 388 个 ZIP 成员的完整性校验。
- 原生载体创建 virt 机器并通过顺序 QMP 查询／退出；镜像工具 create／info／compare 通过。
- 构建路径常量和非装载调试信息已脱敏；版本输出在基线、修改和回滚中一致，回滚摘要恢复一致。
- 中控开发环境已显示原厂欢迎、主页、Radio 等页面。持续交互及动态 >10 FPS 未验收；Android 仍有音频策略超时及 framework 重启；IC／HUD 尚无有效画面。
- 本次 SSH 已确认接收端为 macOS arm64。

## Mac 端接续顺序

1. 核对本文件与 `dist/` 中 SHA256；阅读 `README.md`、`发布状态.md`、`docs/开发.md`。
2. 先确认本机 Python、原生编译器与图形栈。现有启动器为 PowerShell、运行载体为 Windows PE，不能作为 macOS 原生载体使用。
3. 从 `native/upstream/` 与 `native/overrides/` 重新构建 Darwin 载体；检查 C 代码中的 Windows API。补充 Mac 启动入口、图形宿主及输入窗口，保留 Windows 入口。
4. 验证独立入口、源码摘要、纯 Python 检查和最小 QMP 机器启动；再准备使用者自己的原厂输入。
5. 功能工作优先中控：1920×1080、可操作、可交互；FPS 放左上角，只统计运行中变化画面，目标 >10 FPS。不要先扩展 IC／HUD，也不要把加载或停住画面的数值作为动态 FPS 验收。

本次操作范围是传输与交接，不自动恢复原先暂停的运行优化目标。源码校验、Windows 验证和 Mac 原生运行分别记录；未在本次交接中完成 Mac 原生编译或原厂系统启动。统一运行边界见项目根目录 `运行边界.md` 最后一段。
