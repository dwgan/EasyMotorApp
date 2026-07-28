# RobotJointG5 调试上位机

基于 Python Tkinter 和 pyserial 的 Stage-H/Stage-I 转矩与低速命令调试工具。

## 安装与启动

需要 Python 3.10 或更高版本。安装 Python 时请勾选“Add python.exe to
PATH”和 Tcl/Tk 组件。

```powershell
cd D:\Workspace\RobotJointG5\RobotJointApp
python -m pip install -r requirements.txt
python robot_joint_app.py
```

也可以直接双击 `run_app.bat`。

Windows 官方 Python 通常自带 Tkinter。如果启动时报缺少 `tkinter`，
请重新运行 Python 安装程序并启用 Tcl/Tk 组件。

## 推荐操作

1. 选择串口并连接；软件会自动发送一次 `status`。
2. 点击“启动并等待 RUN”。软件被动监听 MCU 的实时状态，进入 RUN 后才开放
   Iq 按钮；不会在对齐期间连续发送 `status` 干扰半双工总线。
3. 设置Iq和脉冲时长，点击“正向定时脉冲”或“反向定时脉冲”。
4. 定时脉冲期间软件自动每500 ms发送 `keep`，到时主动发送 `stop`。
5. 界面显示编码器位置增量、速度、电角度、CCR和PWM Clamp计数。
6. 完成低电流验证后，使用 `Iq=50`、`1000～2000 ms` 验证约0.5 A转矩。
7. 低速测试从 `5 motor rpm`、`3000 ms` 开始；确认方向和反馈正常后，再测试
   10、20 motor rpm。界面同时显示按9:1减速比估算的输出轴转速。
8. 速度模式下固件每100 ms输出`SPEED_TRACE`，上位机显示瞬时速度、整段平均
   速度、Iq、积分量和位置增量，用于后续PI整定。

## 安全边界

- GUI 将直接转矩输入限制为 `1..100 LSB`，当前约为 `0.01 A/LSB`。
- 速度命令单位是电机轴rpm，范围为 `-20..20 rpm`。固件按 `10 rpm/s`
  斜坡变化，并把速度环输出限制在约1.0 A。
- 固件具有独立的60 motor rpm超速停机和堵转停机监督。非零速度命令也需要
  定期发送 `keep`；GUI定时速度测试会自动刷新并停止。
- Iq 是转矩命令而不是速度命令；小电流可能不足以克服齿槽转矩、静摩擦和负载。
- 每次发送前等待 RS485 接收方向至少 5 ms 空闲，降低与 MCU 日志碰撞的概率。
- 定时脉冲范围为500～5000 ms，到时由上位机主动发送 `stop`；MCU自身的
  1000 ms命令看门狗仍独立生效。
- Stop最多以150 ms间隔发送三次，收到 `CMD stop accepted` 或观察到
  `mci=0` 后立即停止重发。若仍无确认，则停止Keepalive并依靠MCU看门狗。
- 成功的 `keep` 在固件侧不再逐条回复，以降低半双工RS485总线占用。
- MCU 中的电流限幅、命令超时、状态机和硬件过流保护仍是最终安全边界。
- 关闭窗口时软件会尝试发送 `stop`，但断线或上位机崩溃时仍由 MCU 的
  1000 ms 命令看门狗负责停机。
- 不要把串口停止按钮当作急停；功率测试必须保留独立断电手段。

## 支持的 MCU 命令

```text
start
iq -100..100
speed -20..20
keep
stop
status
faultack
help
```
