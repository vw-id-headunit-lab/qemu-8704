# 第三方声明

本项目包含 QEMU 衍生平台实现，根目录 `LICENSE` 保留 GNU GPL v2 文本；具体文件沿用其原有许可证和版权声明。QEMU 原始源码归档及修改源码随本项目提供。

图形转发参考 [iscle/qemu-mhi2](https://github.com/iscle/qemu-mhi2)，对应提交 `78b8cb2d34d7c08f34fc33c0cab1ab960edfc000`，许可证保留于 `src/emu/gl-forward/COPYING`。

ANGLE、QEMU 镜像工具及其 GLib／图形／压缩／网络／加密运行依赖由使用者执行 setup 后从锁定的官方 MSYS2 归档安装，不包含在公开运行包中。归档地址、摘要及文件摘要见 `config/dependencies.lock.json`；选定许可证文本保留在 `licenses/`。

原厂固件、原厂字体／JNI／Java 二进制、实车数据、账号凭据与运行快照不随项目分发。
