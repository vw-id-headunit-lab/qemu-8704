# Mac 运行结论：原 8704 + HVF

项目 `/Users/diwen/code/MEB-EMU`，主入口 `/Users/diwen/code/MEB-EMU/MEB-EMU.sh`，统一页面 [打开中控与双指令台](http://127.0.0.1:61509/)。实时地址读取 `/Users/diwen/code/MEB-EMU/var/hvf/dashboard.json`。

## 运行

```sh
cd /Users/diwen/code/MEB-EMU
./MEB-EMU.sh start
./MEB-EMU.sh serve
./MEB-EMU.sh status
./MEB-EMU.sh stop
```

`start` 复用已运行域并准备同页服务，`serve` 返回统一页面，`stop` 正常退出两域及页面服务。中控冷启动后会经历原应用约 90 秒的语言／皮肤初始化。使用页面“启动车辆”进入主界面；首次上电的用户切换遮挡可通过页面熄火后再启动清除。

当前中控 `/Users/diwen/code/MEB-EMU/var/hvf/hmi-20261123T152634Z`，Android `/Users/diwen/code/MEB-EMU/var/hvf/android-20261123T145538Z`。两个域使用同一个 `/Users/diwen/code/MEB-EMU/runtime/darwin/qemu-system-aarch64`：`virt,gic-version=3`、HVF、host CPU、4 核、2 GiB。中控运行原 8704 Linux 用户空间、Weston、Java HMI，Android 运行原 8704 Android11 init／APEX／框架用户空间。配置 `/Users/diwen/code/MEB-EMU/config/hvf-8704.json`。

## 已看到的结果

- 原 HMI 默认简体中文，已打开“通用设置”“连接设置”“热点设置”等原厂页面。完整逻辑画布 1920×1080，网页等比适配并显示完整底栏，输入坐标按同一画布转换。
- ID.3 原车型显示已恢复：中央黑底红叉被原内饰与车身模型替代，实际车型 ID 为 `020301006300000002000`。实拍 `/Users/diwen/code/MEB-EMU/var/hvf/hmi-20261123T150545Z/id3-interior.jpg` 和 `/Users/diwen/code/MEB-EMU/var/hvf/hmi-20261123T150545Z/id3-exterior.jpg`。
- “连接→WLAN 和移动热点→设置车辆作为移动热点”已接入原界面。原 UI 开关实测关→开→关→开，原 RSI `wifi/accessPointManagers` 的 `state` 同步变化，HTTP200；截图 `/Users/diwen/code/MEB-EMU/var/hvf/hmi-20261123T150545Z/wifi-hotspot-verified.jpg`。SSID 为 `MEB-ID3-Bench`。
- 两路原用户空间指令台分别实际返回 Linux UID65534、Android UID2000 与 release11，随机标记 `ID3_L_84ac`、`ID3_A_18df` 均退出0。
- Android 原 `/system/bin/init second_stage`、apexd、installd、zygote、SurfaceFlinger、system_server 已运行；原 keystore 的独立工作副本采用自身 SOFTWARE 后端，`android.security.keystore` Binder 已实际注册。UID2000 所需 passwd 绑定已在控制服务冷启动中复现。
- 原更新管理程序 `/lge/app_ro/update/swup/swup` 已实际启动于独立 PID 命名空间。服务模式原调用链已定位为 `RemModus.start → DownloadApi.requests().setRemStart(true)`；REM 脚本使用原 SWUP→MCU 消息。接续输入定位到 `/data/sharedmemory/shm_swup_*` 及已有 Linux 完成态数据种子。
- 导航虚拟定位已按用户要求保存为南京，WGS84 `32.0603,118.7969`，配置 `/Users/diwen/code/MEB-EMU/var/hvf/navigation-location.json`。

## ID.3 输入与修复点

用户之前 Ric_Laptop 的原捕获为 `D:/code/MEB-MKII/scratch/car-startup-inputs-20261120T063551Z/diagval.bin`，本机 `/Users/diwen/code/MEB-EMU/var/inputs/support/ric-id3/diagval.bin`，2135 字节，SHA256 `918cbd3b6bb548068b7e4adcc9de9a928a2087de62e6eb006c61c7a2875b7dbf`。原 Ric 配置 `prepared/original-8704-vm/offline_id3_config.py` 的 KEY、MASK、VALUE 已核对。

修复在外部 `/Users/diwen/code/MEB-EMU/src/hvf_bench/platform/BenchPlatform.java`：向原 `DSIPersistence.readBuffer` 的 namespace `33554432`、key `1536` 提供捕获的 25 字节编码。原 HMI 自行推导 21 位车型 ID；接受结果使用原资源路径。此前参考启动器继承的 16 位 override 已在此输入路径移除。原 Java 命令行实际只带 `-Dbench.diagnosis.coding=/tmp/bench-ric-id3-coding.bin`。

原 HMI JAR SHA256 保持 `c23959ed50dce25d33aa0e8c503533b4a16336a07d26a61270d357d66d5f6c00`；原 ID.3 `car.mcf` SHA256 为 `db1b358065b9e5487300c77d1888a2b2e2885da29a1e3669046b6b5666ae8ae8`。工作配置 `/Users/diwen/code/MEB-EMU/src/hvf_bench/local/vehicle-profile.json`；模型验收 `/Users/diwen/code/MEB-EMU/.local/mac-port/transaction/ric-id3-model-verified.json`。

WiFi 输入由 `/Users/diwen/code/MEB-EMU/src/hvf_bench/src/hmi_bench/wifi_fixture.py` 提供。原 WiFiController 收到 RSI0.4.2 管理对象后自行发布 `FEAT_IS_WLAN_HOTSPOT=true`，随后注册原菜单 CIO。验收 `/Users/diwen/code/MEB-EMU/.local/mac-port/transaction/wifi-update-verified.json`。

## 验证与回滚

持续沿用四角色：

- MODIFIED_FILE：`/Users/diwen/code/MEB-EMU/tools/emu_macos.py`
- DIFF_FILE：`/Users/diwen/code/MEB-EMU/.local/mac-port/transaction/DIFF.patch`
- VERIFICATION：`/Users/diwen/code/MEB-EMU/.local/mac-port/transaction/VERIFICATION.txt`
- ROLLBACK：`/Users/diwen/code/MEB-EMU/.local/mac-port/transaction/ROLLBACK.sh`

入口同输入 `doctor` 的 BASELINE／MODIFIED／ROLLBACK 为退出2／0／2；差异可精确重建修改文件，独立回滚副本恢复原摘要。补充源补丁与摘要在 `SUPPLEMENTAL.patch`、`supplemental.json` 中持续扩展。`--hvf-wifi-copy DIR` 恢复 WiFi 接入前的输入源和服务表，`--hvf-car-profile-copy DIR` 恢复车型输入接入前的启动器／归档器，`--hvf-car-persistence-copy DIR` 恢复外部 Java 适配源与先前 JAR，`--hvf-android-init-copy DIR` 恢复原 init 集成前的源副本，`--hvf-console-parent-copy DIR` 恢复终端持有机制修改前的源副本。WiFi 同输入源行为为无 WiFi 资源→四项 WiFi 资源→恢复无 WiFi 资源，均退出0。

根输入、原固件与已接受证据保持原位；已结束且 QEMU 退出0、PID消失的六个会话生成 initramfs 已清除，共 83373503 字节。调试终端现在由父进程持有一次 fd3，后台任务存活时的第二次连接已实际通过。活跃会话与回滚材料保留。

本项目当前仍在推进原 REM／恢复系统切换与 FBL 画面、南京虚拟定位对原导航的实际注入、Android `sys.boot_completed=1`、QNX 和 IC/HUD 原像素以及菜单语言热切换；整体目标保持进行中。车辆信号、WiFi 等外部服务包含台架输入，热点菜单和虚拟开关不代表 Mac 已发射无线 AP 或手机可接入；当前导航仍可能显示正在启用。原镜像只读保留，通用内核和外部平台服务已适配；未宣称实车硬件、原厂安全启动、持续动态帧率或长期稳定性已验证，也未改写实车身份、提交或发布项目。
