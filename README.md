# EasyMotor 电机演示与工程工具

EasyMotor 是基于 Python Tkinter 和 pyserial 的电机演示与工程调试工具。软件每次启动
默认进入英文、CAN 接口的简洁演示模式；可切换中文和备用 RS485 接口。工程师可主动进入
完整调试界面。两个模式共用相同的限值、停止重试和 MCU 安全保护。

## 安装与启动

需要 Python 3.10 或更高版本。安装 Python 时请勾选 "Add python.exe to
PATH" 和 Tcl/Tk 组件。

```powershell
cd <EasyMotor repository>
python -m pip install -r requirements.txt
python easymotor_app.py
```

也可以直接双击 `run_app.bat`。Windows 官方 Python 通常自带 Tkinter；
如果启动时报缺 `tkinter`，请重新运行 Python 安装程序并启用 Tcl/Tk 组件。

## 构建单文件 EXE

双击 `build_easymotor_release.bat`，或在 PowerShell 中执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_easymotor_release.ps1 -Clean
```

脚本默认安装 `requirements.txt` 和 `requirements-build.txt` 中的依赖、运行软件测试，并使用
PyInstaller 的 `--onefile --windowed` 模式生成一个无需 Python 环境的 Windows GUI 可执行文件：

```text
release\EasyMotor_v1.0.0_win-x64.exe
```

版本号统一读取 `easymotor/version.py`，同时写入软件标题、EXE 文件名和 Windows 文件属性中的
FileVersion、ProductVersion、ProductName、FileDescription、CompanyName 等字段。正式发布时应先修改
`VERSION`；打包脚本不接受独立版本覆盖，以保证源码、界面和 EXE 元数据始终一致。
EXE 元数据一致。常用选项：

```powershell
# 生成指定版本，并额外输出 SHA-256 文件
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_easymotor_release.ps1 -Clean -WriteChecksum

# 已自行安装依赖时跳过 pip
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_easymotor_release.ps1 -Clean -NoInstallDependencies

# 仅在紧急诊断构建中跳过测试，不建议用于正式发布
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_easymotor_release.ps1 -Clean -SkipTests
```

`build/` 仅保存 PyInstaller 中间文件；可交付物位于 `release/`。默认不会生成 onedir 文件夹、ZIP
或校验和旁文件，因此每个版本的默认交付物只有一个 EXE。

## GitHub 手动更新

演示模式和高级模式底部都提供“检查更新 / Check for updates”。EasyMotor 只在用户点击时访问公开的
`dwgan/EasyMotorApp` GitHub Releases，不会在启动时联网。源码运行时可以查看 Release 页面；只有打包后的
EXE 才允许原位更新。

正式稳定版本使用 `vX.Y.Z` 标签，并同时包含：

```text
EasyMotor_vX.Y.Z_win-x64.exe
easymotor-update.json
```

客户端会同时校验清单、GitHub asset digest、文件长度、SHA-256、PE x64 架构和 Windows 版本资源。
安装前必须停止电机并断开通信；替换程序在 EasyMotor 退出后运行，需要时申请 UAC，并在新版本未能
健康启动时恢复旧 EXE。发布端由独立的 EasyMotor Publisher 工程管理；两个工程不共享 Git 仓库、
Python 包或构建目录，客户软件中不包含 GitHub Token。

## 两种使用模式

### 演示模式（默认）

- 默认英文界面，可在右上角切换 English/中文；
- 默认官方 USB-CAN（串口 921600 baud，CAN 1 Mbps），RS485 为备用；
- 启动后只显示接口、设备连接、状态、5/10/20 motor rpm、正转、停止和反转；
- 默认每次运行 5 秒，到点自动停止；
- 勾选“一直转”后持续刷新速度命令，直到点击停止、连接断开、出现故障或 MCU 看门狗介入；
- CAN 自动执行 `Type 3 → 等待 Type 2 MOTOR → Type 1 → Type 4`；RS485 自动执行
  `start → 等待 RUN → speed → keep → stop`，用户不需要理解对齐和 MCI；
- 每次启动都回到演示模式，“一直转”默认不勾选。

当前固件上电后 CAN 收发器默认待机。若 CAN 枚举超时，EasyMotor 会提示工程师先在高级模式
通过 RS485 执行一次“CAN 正常模式”；这不是 CAN 报文能够自行解除的状态。

### 工程师模式

电机停止后可从右上角进入。工程师模式保留手动 Iq/速度、持续运行、Stage-I 验收、
实时遥测、波形、CAN 参数、长稳和原始日志等功能。切换模式不会放宽任何软件或固件
安全限制。

主界面的 English/中文选择是全局语言设置：演示页、工程师页、CAN 参数工具、波形窗口、日志窗口，以及运行时状态和弹窗会同步切换。协议标识、CAN 原始帧和固件原始输出保持原样，便于工程诊断和协议比对。

## 工程结构

`easymotor_app.py` 是主启动入口，`robot_joint_app.py` 仅保留为旧快捷方式兼容层；通用功能
放在 `easymotor/` 包中：

- `core/safety_policy.py`：演示档位、时长和安全计划；
- `i18n.py`：产品外壳的中英文文本；
- `services/demo_service.py`：演示准备与运行状态机；
- `services/endurance_service.py`：CAN 长稳调度与统计；
- `protocols/can_motor.py`：CAN 电机协议编解码；
- `transports/usb_can.py`：官方 USB-CAN 串口传输；
- `features/demo/view.py`：默认演示页面；
- `features/can_tool/window.py`：工程师 CAN 工具窗口。

通用 Python 文件名不使用具体电机型号；实际兼容的 RS04 协议仍在界面和协议说明中明确标注。
完整的模式边界、统一控制链和后续拆分顺序见 `docs/APP_ARCHITECTURE.md`。

## 当前协议（固件 v2_free_run，ClosedLoop/Stage-H 命令面）

串口：UART5，`2500000` baud，8N1，RS485 半双工（PB3 硬件 DE）。
命令为小写 ASCII，CR/LF 结尾；MCU 也接受 UART IDLE 作为命令结束符。

| 命令 | 范围/说明 |
|---|---|
| `start` | OFFSET_CALIB → ALIGNMENT → START → RUN（自动对齐） |
| `iq N` | 转矩模式，N = -100..100 LSB（约 ±1.0 A），100 ms 斜坡 |
| `speed N` | 速度模式，N = -20..20 motor rpm，10 rpm/s 斜坡 |
| `keep` | 刷新命令看门狗；成功时不回显 |
| `stop` | 100 ms 斜坡归零后停止 PWM |
| `status` | 打印 TORQUE_CMD / SPEED / MOTION / UART_RX 诊断 |
| `faultack` | 确认未激活的锁存故障 |
| `help` | 打印命令帮助 |

非零 `iq`/`speed` 命令超过 1000 ms 未收到 `keep` 时，MCU 自动减流并停机。

遥测行：

- `RT ... mci= pwm= enc= slk= dmiss= out= ...`：1 s 实时运行时行；
- `SPEED_TRACE`：速度模式运行期间每 100 ms 一行轨迹；
- `MOTION`：1 s 运动快照（位置增量、速度、电角度、CCR、Clamp）；
- `ENC_ERR`：转子 AS5047P 健康计数（spi/spit/spif/snapu/fast_stale/stable/usable）；
- `EANG_RIPPLE` + 12 行 `EANG_BIN`：`stop` 后输出的电角度纹波统计。

## 推荐操作

1. 选择串口并连接；软件会自动发送一次 `status`。
2. 点击“启动并等待 RUN”。软件被动监听 RT 状态，进入 RUN 后才开放
   Iq/速度按钮；不会在对齐期间连续发 `status` 干扰半双工总线。
3. 低速测试：
   - 设置速度（1..20 motor rpm）和测试时长（500..5000 ms）；
   - 点击“正向低速测试”或“反向低速测试”；
   - 测试期间软件**每 500 ms 自动发送 `keep`**，到点自动 `stop`；
   - 查看 `SPEED_TRACE`（目标/实测/平均/误差）与停止后的 `EANG_BIN`。
4. “Stage-I 双向探测”一键完成文档首测流程：
   `start → +rpm（自动 keep）→ stop → start → -rpm（自动 keep）→ stop`，
   每个方向之间按文档重新 `start`（自动再对齐），每阶段都有超时保护，
   任何异常自动停止并中止序列。
5. “Stage-I 完整验收”一键跑完整 4 档验收：默认
   `start → +10 rpm（3 s，自动 keep）→ stop → start → -10 rpm → stop →
   start → +20 rpm → stop → start → -20 rpm → stop`（档位可在
   “验收档位 rpm”输入框改成逗号分隔列表，如 `10,20,5`，每档自动执行
   正反向）。每档结束后自动汇总该档 avg/min/max 速度、是否看门狗停机、
   EANG 样本与 PASS/FAIL 判定（判定标准：无看门狗停机且平均速度与目标
   偏差 ≤30%），全部结束后输出通过档数、失败档与总耗时。
6. 持续运行：勾选“持续运行自动 Keepalive（500ms）”后发送非零
   `iq`/`speed`，软件会持续刷新；点击“停止”或“Iq/速度归零”结束。
7. 界面显示编码器位置增量、速度、电角度、CCR、PWM Clamp、RT/ENC
   健康计数和电角度纹波摘要。

## RS04 USB-CAN 参数验收

主界面“CAN 调试”区域中的“USB-CAN 参数验收”会打开独立串口窗口，
不会占用原来的 COM4/RS485 调试通道。该窗口按灵足时代 RS04 官方私有
协议工作：CAN 2.0、1 Mbps、29 位扩展帧，官方 USB-CAN 模块串口为
`921600` baud、8N1，串口帧头/尾为 `41 54` / `0D 0A`。

推荐的阶段 3 操作顺序：

1. 保持主界面固件串口连接，确认 `mci=0`，然后点击“正常模式”；
2. 打开“USB-CAN 参数验收”，选择官方模块所在的另一个 COM 口并连接；
3. 保持电机 ID `127`、主机 ID `253`，点击“检测设备”，日志应显示 MCU UID；
4. 选择参数并点击“读取”。读取使用通信类型 17，整数和浮点数均按官方
   小端格式解释；`run_mode`、`mechPos`、`EPScan_time` 和 `cantimeout`
   也提供独立的“常用验收”快捷读取按钮；
5. 只有 `run_mode=0`、`EPScan_time=1..200`、`cantimeout=0` 或
   `20..100000` 可以写入。写入使用通信类型 18，随后自动用类型 17
   回读。类型 18 的官方响应是普通类型 2 状态帧，不能单独表示写入成功；
   只有回读数值一致，并且 COM4 固件日志出现 `CAN_PARAM: write accepted`
   才算通过（若原值本来相同，只有 COM4 日志能区分接受与拒绝）。

窗口有意不提供电机使能、运控、机械置零、修改 CAN ID 或参数保存功能。
官方参数表中其他标为 W/R 的项目，在当前固件阶段仍按只读处理。

“阶段 3 固件拒绝路径”提供三个固定诊断报文：写只读 `mechPos`、写
`EPScan_time=0`、写 `cantimeout=19`。三项均不包含使能或运动指令，预期
固件拒绝并保持原值；必须同时在 COM4 确认 `CAN_PARAM: write rejected`，
最后通过 `can status` 确认 `param_write_rejected` 计数增加 3。

### 阶段 4：无动力 CAN 长稳

CAN 参数窗口提供独立的长稳区，只以 100 ms 周期轮询 Type 17 参数
`run_mode`、`mechPos`、`mechVel`、`EPScan_time`、`cantimeout`，单次响应超时
为 500 ms。长稳期间会阻止枚举、手动读写和拒绝测试，并且不会发送使能、运控、
写参数、置零或修改 CAN ID 报文。

正式验收前保持功率级关闭、`mci=0`，在 COM4 点击“查询状态”保存开始基线；
时长保持 60 分钟，完成后导出 CSV，再次查询状态。通过条件为上位机超时、拒绝和
发送失败均为 0，`TX=response`，固件 BusOff/收发错误/FIFO 丢失不增长，且
`param_read_ok` 增量等于上位机响应数。测试期间不要执行其他 CAN 操作。

## 安全边界

- GUI 将转矩输入限制为 1..100 LSB（约 ±1.0 A）；速度限制为 -20..20
  motor rpm；定时测试时长 500..5000 ms。
- 速度环输出限制 ±100 LSB，固件还有 150 LSB 软件限流和独立 TIM1
  硬件过流保护（±15 A 窗口）。
- 非零命令必须定期 `keep`；定时测试和双向探测会自动刷新并到点停止，
  MCU 自身的 1000 ms 命令看门狗仍独立生效。
- `stop` 最多以 150 ms 间隔发送三次，收到 `CMD stop accepted` 或观察
  到 `mci=0` 后立即停止重发；仍无确认则停止 Keepalive 依靠 MCU 看门狗。
- 成功 `keep` 在固件侧不回显，降低 RS485 半双工总线占用。
- USB-CAN 参数窗口与 RS485 控制窗口使用两个独立串口；不要选择同一个
  COM 口。参数写入前必须保持电机处于 IDLE/RESET。
- 不要把关机当作急停；功率测试必须保留独立断电手段。

## 独立工程说明

本目录是完整、可单独克隆和构建的 EasyMotor Git 仓库，不依赖父目录或
其他 RobotJointApp 工作区。工程按当前双 AS5047P + iC-MU150 共用命令面
维护。五套 IAR
配置（FreeRun / eRob80H50 / Bringup / ClosedLoop / Alignment）共用同一
UART5 控制台协议；Alignment 配置下 `start/iq/speed` 被禁用，仅
`align/stop/status/faultack`。
