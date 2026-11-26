# qemu-8704

当前主路线为 Apple Silicon macOS + QEMU HVF，启动入口保留 `MEB-EMU.sh`。

## 8704 资源下载

- [8704 资源（百度网盘）](https://pan.baidu.com/s/1W6a6TEcS3oD1tUDozOScfw)
- 提取码：`8704`
- 本地打包版本：`qemu-8704-resources-20261125.zip`，16,724,786,584 字节（约 15.6 GiB）。
- 内容：8704 原始资源与运行输入、完整南京 1911 地图、必要 qcow2 种子及 backing 链、HMI 编译依赖和私有触控载体；不含历史会话盘/RAM 副本和个人车辆适配配置。

下载后请核对本地打包版本的 SHA256：

```text
829d843d950c89e3b0c5f29abdf34cfe199f5651d2b38e6d8eb46a01a9c25127
```

使用者须自行确认原厂软件和地图的合法使用权限；分享不构成授权，也不代表新机器已通过全部业务验收。

## 首次运行

Git 仓库本身不包含 8704 更新包、原厂镜像、原厂 JAR、个人车辆编码或运行快照。8704 运行输入和地图可从上方独立网盘分享获取，也可自行准备有权使用的资源；原始更新包不能直接作为下列镜像使用。

克隆后，在项目根目录执行：

```sh
./MEB-EMU.sh setup
```

使用网盘 ZIP 时，请先只取出恢复脚本和说明，不要直接完整解压：UFS 稀疏镜像的逻辑大小约 115.6 GiB，普通解压可能占满磁盘。在**新克隆且未启动会话**的项目根目录执行（将 `ZIP` 替换为下载文件的绝对路径）：

```sh
ZIP="/absolute/path/qemu-8704-resources-20261125.zip"
shasum -a 256 "$ZIP"
HELPER_DIR="$(mktemp -d)"
unzip -j "$ZIP" restore_resources.py RESOURCE_README.txt -d "$HELPER_DIR"
python3 "$HELPER_DIR/restore_resources.py" "$ZIP" "$PWD"
```

恢复脚本逐文件校验 SHA256、稀疏展开并重定位必要 qcow2 backing 路径，拒绝覆盖已有目标文件。已有输入或正在运行的工作目录不要直接套用。可选地图配置及迁移边界见包内 `RESOURCE_README.txt` 和 [Mac 运行指南](docs/Mac运行.md)。

若不使用网盘 ZIP，请将自行准备的 8704 文件放到以下项目相对位置：

```text
var/inputs/analysis/cpu/ivi/icas3cn-gp-image-euto-v9.ext4
var/inputs/hmi/vw-restored/vw-hmi.ext4
var/inputs/support/vw-verity-native/overlay-verity/lg-custom.ext4
var/inputs/support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw
```

四项分别供 Linux 根文件系统、原 HMI、custom 分区和 Android 原始分区读取使用。更新包是否足够取决于实际包含的分区；缺失原始输入不能由脚本伪造。初始化时会创建明确标为模拟的离线车型资源选择（默认 21 位资源键，可用 `--vehicle-type` 指定），不包含原车诊断记录或身份。个人车辆适配配置如有需要，由使用者自行准备在被忽略的 `src/hvf_bench/local/`，不随仓库分发。

在新克隆且未启动会话的目录执行：

```sh
.venv/bin/python -B tools/configure_macos.py
./MEB-EMU.sh doctor
./MEB-EMU.sh smoke
./MEB-EMU.sh start
.venv/bin/python -B tools/bench_power.py on
./MEB-EMU.sh serve
./MEB-EMU.sh status
```

初始化脚本仅绑定本机路径、校验模块并创建空白 Android misc 运行盘；缺少输入时退出 2 且不写配置，不下载、不解包、不修改原厂输入。`bench_power.py on` 只设置客体内模拟的驻车点火与电源信号，服务尚未就绪时需稍后重试；不是实车控制。首次 HMI 启动可能等待约 90 秒后选择默认皮肤。`doctor` 或路径检查成功不代表完整业务启动通过；页面地址以 `start` / `serve` 的实际输出为准。使用 `./MEB-EMU.sh stop` 停止当前副本，等旧 worker 退出后再启动。

已有本机验证覆盖原 HMI 页面、中文设置及 Linux/Android 指令通道。Android 完整 framework、QNX/IC/HUD、长期稳定性及持续动态帧率未全部验收。不能将“本机已有镜像可运行”表述为“任意机器仅克隆即可运行”；新机器仍需自备输入、macOS 依赖和实际冷启动验证。历史文档中的本机绝对路径不是新机器的部署路径。

仓库包含当前启动所需的载体、内核和 Mesa 运行归档；可再生的解包缓存不纳入 Git。重新构建 QEMU 时，源码准备脚本会从 QEMU 官方获取固定版本，并强制校验锁定的 SHA256；这不是 8704 资源下载。Git 克隆和当前预编译载体运行不再依赖 Git LFS。

macOS 日常操作、源码职责和验收边界见 [Mac 运行指南](docs/Mac运行.md)。

第三方许可见 [第三方声明](NOTICE.md)，功能验收范围见 [运行边界](docs/运行边界.md)。

公开包保留 `src/`、`tools/`、`native/`、`runtime/`、`config/`、`tests/`、`licenses/` 与必要文档；本机输入和输出分别位于 `var/inputs/`、`var/sessions/`。`var/`、`.local/`、`.venv/` 不进入发布包。

## 目录约定

- 根目录仅保留入口、依赖声明、README、许可证和工程配置；运行、开发、发布及边界说明统一在 `docs/`，见 [目录结构与清理规则](docs/目录结构.md)。
- 私有固件与实车资料放在 `var/inputs/`；根目录的实车导出包及说明已原字节归档到 `var/inputs/packages/car-export-20261124/`，不进入 Git 或发布包。
- 历史截图放在 `var/screenshots/`，不是当前实时画面；会话产物放在 `var/hvf/`、`var/sessions/`，不进入 Git。
- 本机已有 `8704/` 是私有更新包提取目录，维持路径不搬动；新输入仍按首次运行约定放入 `var/inputs/`。

## 交流群

微信扫码加入「ID 折腾俱乐部」。图中二维码在 12 月 3 日前有效，过期后需更新。

<img src="https://github.com/user-attachments/assets/cc0acdb7-dab7-4c20-95be-d6cfead08a4c" alt="ID 折腾俱乐部微信群二维码" width="400" />
