# RobotJointG5 / ICMU150 调试上位机

基于 Python Tkinter 和 pyserial 的 Stage-H/Stage-I 转矩与低速命令调试工具，
协议与当前固件 `motor_command_console.c` 对齐（UART5 2.5 Mbit/s 8N1 RS485）。

## 安装与启动

需要 Python 3.10 或更高版本。安装 Python 时请勾选 "Add python.exe to
PATH" 和 Tcl/Tk 组件。

```powershell
cd D:\Workspace\RobotJointG5\RobotJointApp_ICMU150
python -m pip install -r requirements.txt
python robot_joint_app.py
```

也可以直接双击 `run_app.bat`。Windows 官方 Python 通常自带 Tkinter；
如果启动时报缺 `tkinter`，请重新运行 Python 安装程序并启用 Tcl/Tk 组件。

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
- 不要把关机当作急停；功率测试必须保留独立断电手段。

## 与 RobotJointApp（通用版）的关系

`RobotJointApp` 与 `RobotJointApp_ICMU150` 曾是完全相同的副本；本目录
现在是按当前双 AS5047P + iC-MU150 共用命令面维护的版本。五套 IAR
配置（FreeRun / eRob80H50 / Bringup / ClosedLoop / Alignment）共用同一
UART5 控制台协议；Alignment 配置下 `start/iq/speed` 被禁用，仅
`align/stop/status/faultack`。
