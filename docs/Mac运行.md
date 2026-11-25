# macOS 运行指南

主路线：Apple Silicon macOS + QEMU HVF；统一入口 `MEB-EMU.sh`。本仓库并不表示原厂三域和全部业务已经验收。

## 准备与启动

先按照根目录 README 安装依赖，并准备自己有权使用的 8704 原始输入。首次运行按顺序执行：

```sh
./MEB-EMU.sh setup
.venv/bin/python -B tools/configure_macos.py
./MEB-EMU.sh doctor
./MEB-EMU.sh smoke
./MEB-EMU.sh start
.venv/bin/python -B tools/bench_power.py on
./MEB-EMU.sh serve
./MEB-EMU.sh status
```

以实际命令输出的网址为准，不复用研究记录中的端口或进程号。`bench_power.py on` 是本地模拟的驻车点火信号，不是实车控制。停止时执行 `./MEB-EMU.sh stop`，等待旧 worker 退出后再启动。

## 输入与地图

基础输入、许可边界和导入位置以 README 为准。当前导航配置由 `config/hvf-8704.json` 指定：

- `firmware_navigation`：`var/inputs/seeds/linux/ufs-vh1-data-session.qcow2`，只读导航系统分区来源。
- `navigation_map`：`var/inputs/derived/navigation/nanjing-1911-complete.ext4`，补齐 WORLD/1100 依赖的完整南京 1911 地图。
- `navigation_data`：`var/hvf/navigation-data.ext4`，会话可写缓存，不能与只读地图混用。

`configure_macos.py` 绑定基础输入路径，不自动保证可选导航配置和业务冷启动验收。换机器后需检查这些字段及实际输入存在性。

## qcow2 保留规则

不能按扩展名批量删除 qcow2。下列 backing 链仍是启动依赖：

```text
seeds/linux/ufs-vh1-data-session.qcow2
  -> hmi/install-overlay/vm3-vw-install-lun0.qcow2
  -> support/vw-verity-native/overlay-verity/vm3-vw-lun0.qcow2
  -> support/abox-dsp-native/virtual-lun0-dsp/ufs-lun0.raw
```

这些路径均相对于 `var/inputs/`。QNX 种子和原生 Android 种子也依赖原始 UFS；仍被运行进程、当前指针、种子元数据或 backing 链使用的盘必须保留。历史会话副本与原始输入分开管理，见 [目录结构与清理规则](目录结构.md)。

## 验证范围

`doctor`、路径检查、QEMU 存活或域 RUN 不能替代业务画面与交互验证。已出现过原 HMI、导航地图及独立原生画面证据，但不据此宣称 Android 完整 framework、完整 IC/HUD、全部导航交互或长期稳定性通过。详情见 [运行边界](运行边界.md)。

逐轮研究过程、旧 PID、临时探针、原始输出及恢复材料不作为用户操作指南；本机证据保留在被忽略的 `.local/mac-port/` 和项目外清理记录中。
