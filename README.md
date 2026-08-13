# EasyMotor 电机演示与工程工具

EasyMotor 是基于 Python Tkinter 和 pyserial 的电机演示与工程调试工具。软件每次启动
默认进入英文、CAN 接口的简洁演示模式；可切换中文。工程师可主动进入只读 RS485 诊断
界面。所有真实运动统一走 CAN，并共用相同的限值、停止重试和 MCU 安全保护。

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
- 固定使用官方 USB-CAN（串口 921600 baud，CAN 1 Mbps）；
- 启动后只显示接口、设备连接、状态、5/10/20 motor rpm、正转、停止和反转；
- 默认每次运行 5 秒，到点自动停止；
- 勾选“一直转”后持续刷新速度命令，直到点击停止、连接断开、出现故障或 MCU 看门狗介入；
- CAN 自动执行 `Type 3 → 等待 Type 2 MOTOR → Type 1 → Type 4`，用户不需要理解对齐和 MCI；
- 每次启动都回到演示模式，“一直转”默认不勾选。

当前正式固件在初始化完成后自动进入 CAN 正常模式。若 CAN 枚举超时，EasyMotor 会提示检查
电机供电、1 Mbps 设置、CAN-H/CAN-L 接线、终端电阻和节点 ID。
USB-CAN 可以先于电机上电连接：软件会保持适配器连接并每秒自动枚举，电机随后上电即可自动
进入 Ready，无需断开重连。等待超过 10 秒只显示检查建议，不停止后台枚举。

### 工程师模式

可从右上角进入高级模式；为观察运行中的三相电流，进入高级模式不要求停止或断开 CAN。
CAN 运动连接与 UART5/RS485 Debug 使用两个独立串口，可以同时在线。工程师模式的
UART5/RS485 仅用于 Debug 固件的状态、遥测和 ADC 波形。`help`、`can status` 和
`can codec` 仍是固件串口终端命令，但不再占用正式 GUI。CAN 参数直接复用 CAN Control
已经连接并枚举的 USB-CAN；参数拒绝和只读长稳位于默认折叠的 Validation Tools。高级 Iq、
位置等尚未验证的 CAN 功能保持隐藏，切换模式不会放宽任何软件或固件安全限制。

主界面的 English/中文选择是全局语言设置：演示页、工程师页、CAN 参数工具、波形窗口、日志窗口，以及运行时状态和弹窗会同步切换。协议标识、CAN 原始帧和固件原始输出保持原样，便于工程诊断和协议比对。

Logs 页按来源提供 `All / CAN / RS485 Debug / Application` 四个视图。每条记录带来源标签；
CAN 帧、RS485 串口遥测和软件生命周期事件不再混在单一列表中，All 页仍保留跨接口时序。

## 工程结构

`easymotor_app.py` 是唯一启动入口；通用功能放在 `easymotor/` 包中：

- `core/safety_policy.py`：演示档位、时长和安全计划；
- `i18n.py`：产品外壳的中英文文本；
- `services/demo_service.py`：演示准备与运行状态机；
- `services/endurance_service.py`：CAN 长稳调度与统计；
- `protocols/can_motor.py`：CAN 电机协议编解码；
- `transports/usb_can.py`：官方 USB-CAN 串口传输；
- `features/demo/view.py`：默认演示页面；
- `features/can_parameters/panel.py`：复用主 CAN transport 的嵌入式参数面板。

通用 Python 文件名不使用具体电机型号；实际兼容的 RS04 协议仍在界面和协议说明中明确标注。
完整的模式边界、统一控制链和后续拆分顺序见 `docs/APP_ARCHITECTURE.md`。

## UART5 Engineering Debug 命令面

串口：UART5，Engineering Debug 固件默认 `4000000` baud，8N1，RS485 半双工
（PB3 硬件 DE）。上位机提供 921600/1M/2M/2.5M/3M/4M 常用选项，也可输入
自定义整数波特率；所选值必须与固件一致，且 USB-RS485 适配器必须支持该速率。
命令为小写 ASCII，CR/LF 结尾；MCU 也接受 UART IDLE 作为命令结束符。

| 命令 | 范围/说明 |
|---|---|
| `status` | 打印 TORQUE_CMD / SPEED / MOTION / UART_RX 诊断 |
| `help` | 打印命令帮助 |
| `wave on [1..100]` | 三相电流原始分频流 |
| `wave stats on [10..500]` | 三相电流最小/最大包络流 |
| `wave single on [u/v/w]` | 指定一路相电流按每次 FOC 采样分块发送 |
| `wave off` | 停止波形流 |
| `can status` | 打印 CAN 传输状态和 Bus-Off 恢复统计 |
| `can codec` | 执行不访问真实总线的协议编解码自检 |

Product 固件不初始化 UART5。Engineering Debug 固件拒绝所有使能、停止、故障确认、Iq、速度、
Keepalive、CAN STBY 和主动上报控制命令；真实运动和状态改变必须使用 CAN。

遥测行：

- `RT ... mci= pwm= enc= slk= dmiss= out= ...`：1 s 实时运行时行；
- `SPEED_TRACE`：速度模式运行期间每 100 ms 一行轨迹；
- `MOTION`：1 s 运动快照（位置增量、速度、电角度、CCR、Clamp）；
- `ENC_ERR`：转子 AS5047P 健康计数（spi/spit/spif/snapu/fast_stale/stable/usable）；
- `EANG_RIPPLE` + 12 行 `EANG_BIN`：`stop` 后输出的电角度纹波统计。

## 推荐操作

1. 只连接电机供电和 CAN，选择官方 USB-CAN 模块的串口并点击连接。
2. 等待节点枚举成功；此时 CAN 已上线，但电机仍保持停止。
3. 选择 5/10/20 motor rpm，点击正转或反转；默认 5 秒后自动发送 Type 4 停止。
4. 只有明确勾选“一直转”才持续刷新 Type 1，点击停止、断线、故障或 MCU 看门狗都会结束。
5. 需要波形时保持 CAN 在线并进入高级模式，在“RS485 Debug”页选择另一 COM 口和与固件一致的波特率；连接后打开波形窗口，再切换到“CAN Control”页使用同样受限的 5/10/20 rpm 控制。波形独立窗口会继续显示。UART5 遥测必须使用 Engineering Debug 固件。普通模式观察三相波形，包络模式保留每个统计窗口的三相峰值；“单路全采样”使用 128 点二进制分块，在当前 50 kHz FOC 配置下连续传输选定 U/V/W 一路的全部控制周期采样。横轴时间窗可选择 30 ms、200 ms、1 s、2 s 或 5 s；长时间窗按屏幕像素保留每列 min/max 后绘制，因此扩大周期范围不会隐藏窄尖峰，CSV 仍保存完整接收样本。

## RS04 USB-CAN 参数验收

高级模式 `CAN Control` 页中的参数区域直接复用当前主 CAN 连接，不会再次打开串口，
也不需要第二只 USB-CAN 适配器。参数区域默认折叠，电机必须已枚举并保持 IDLE；启动、
运动、停止和长稳期间参数操作与运动按钮会互锁。该功能按灵足时代 RS04 官方私有
协议工作：CAN 2.0、1 Mbps、29 位扩展帧，官方 USB-CAN 模块串口为
`921600` baud、8N1，串口帧头/尾为 `41 54` / `0D 0A`。

推荐的阶段 3 操作顺序：

1. 在 `CAN Control` 连接并等待设备枚举成功，确认电机处于 IDLE；
2. 展开“CAN 参数”；
3. 选择参数并点击“读取”。读取使用通信类型 17，整数和浮点数均按官方
   小端格式解释；`run_mode`、`mechPos`、`EPScan_time` 和 `cantimeout`
   也提供独立的“常用验收”快捷读取按钮；
4. 只有 `run_mode=0`、`EPScan_time=1..200`、`cantimeout=0` 或
   `20..100000` 可以写入。写入使用通信类型 18，随后自动用类型 17
   回读。类型 18 的官方响应是普通类型 2 状态帧，不能单独表示写入成功；
   只有回读数值一致才算通过；Engineering Debug 构建还可从 UART5 日志确认接受或拒绝。

参数面板有意不提供机械置零、修改 CAN ID 或参数保存功能。
官方参数表中其他标为 W/R 的项目，在当前固件阶段仍按只读处理。

参数拒绝测试和只读长稳默认隐藏；勾选“显示 Validation Tools”后才会加入
对应二级页，避免普通参数读取界面被验收细节占满。

“阶段 3 固件拒绝路径”提供三个固定诊断报文：写只读 `mechPos`、写
`EPScan_time=0`、写 `cantimeout=19`。三项均不包含使能或运动指令，预期
固件拒绝并保持原值；Engineering Debug 构建可通过 `can status` 确认
`param_write_rejected` 计数增加 3。

### 阶段 4：无动力 CAN 长稳

CAN 参数面板提供折叠的长稳区，只以 100 ms 周期轮询 Type 17 参数
`run_mode`、`mechPos`、`mechVel`、`EPScan_time`、`cantimeout`，单次响应超时
为 500 ms。长稳期间会阻止枚举、手动读写和拒绝测试，并且不会发送使能、运控、
写参数、置零或修改 CAN ID 报文。

正式验收前保持电机停止；Engineering Debug 构建可从 UART5 保存开始基线；
时长保持 60 分钟，完成后导出 CSV，再次查询状态。通过条件为上位机超时、拒绝和
发送失败均为 0，`TX=response`，固件 BusOff/收发错误/FIFO 丢失不增长，且
`param_read_ok` 增量等于上位机响应数。测试期间不要执行其他 CAN 操作。

## 安全边界

- 演示速度固定为 5/10/20 motor rpm，定时运行默认 5 秒；Type 1 的位置、Kp、Kd 和力矩前馈固定为零。
- 速度环输出限制 ±100 LSB，固件还有 150 LSB 软件限流和独立 TIM1
  硬件过流保护（±15 A 窗口）。
- EasyMotor 以 250 ms 刷新 Type 1；停止会发送 Type 4，MCU 自身的 1000 ms 命令看门狗仍独立生效。
- RS485 Debug 不发送运动或状态改变命令。参数写入前必须保持电机处于 IDLE/RESET。
- 不要把关机当作急停；功率测试必须保留独立断电手段。

## 独立工程说明

本目录是完整、可单独克隆、测试和打包的 EasyMotorApp Git 仓库，不依赖固件或 Publisher
工作区。固件与应用通过稳定的 RS04 29 位 CAN 协议边界协作。
