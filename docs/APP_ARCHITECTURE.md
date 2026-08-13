# 上位机双模式架构

## 产品目标

EasyMotor 同时服务现场演示和工程调试：每次启动默认进入英文、CAN 接口的简洁演示模式；
可切换中文，用户主动确认后才能进入工程师模式。两种界面只决定功能可见性，
不能绕过统一的安全策略、命令状态机和 MCU 保护。

## 模式边界

演示模式只提供设备连接、5/10/20 motor rpm、正转、反转和停止。默认运行 5 秒并自动停止；
“一直转”必须逐次主动勾选，通过周期 Type 1 维持，停止、断线、故障或 MCU 看门狗均会结束
运行。演示操作固定通过 CAN 自动执行 `Type 3 → Type 1 → Type 4`。

工程师模式允许 CAN 运动连接与只读 UART5/RS485 Debug 同时在线：前者负责使能、运动和停止，
后者负责状态、波形和日志。两条链路拥有独立的端口和连接生命周期。高级模式也提供同一套
5/10/20 rpm CAN 安全控制，便于电机运行时同时观察波形。所有由 UART5 发出的状态、帮助、
波形命令均在界面中明确标为 RS485 Debug。`help`、`can status` 和 `can codec` 只保留为固件
终端诊断命令，不进入正式 GUI。参数、拒绝测试和只读长稳直接复用主 CAN transport，其中
参数面板与 Validation Tools 均默认折叠；运动期间参数操作锁定，长稳期间运动控制锁定。
未验证的 Iq、位置等高级
运动项不提供，所有运动都走同一条受限 CAN 服务。
进入或退出工程师模式前必须停止电机；高级模式不会提高软件或固件限值。

高级页面按接口职责分区，避免在同一页混合控制链与调试链：

```text
Overview     只汇总两路接口与电机安全状态
CAN Control  主 CAN 连接、受限运动控制、Type 2 反馈、折叠参数面板
RS485 Debug  UART5 状态、遥测与波形
Logs         All / CAN / RS485 Debug / Application 四路视图
```

RS485 Debug 波特率由连接区选择，默认与 Engineering Debug 固件一致为
4 Mbps，同时允许常用预设和自定义整数。波形传输支持三相分频原始帧、三相
min/max 包络帧，以及一路 128 点分块全采样帧；单路模式在当前 50 kHz FOC 下
覆盖每一个控制周期采样，不改变 PWM、FOC 或 ADC 触发频率。显示层提供
30 ms 至 5 s 时间窗，并按画布像素聚合每列最小值/最大值，限制 Tk 绘图负载的
同时保留噪声尖峰；原始接收与 CSV 路径不做显示抽取。

波形接收线程以有界批次队列向 UI 交付数据，界面短时阻塞时会丢弃最旧批次而不是无限增长
内存，并分别统计 UART 序号缺口、固件环形缓冲丢弃、主机批次丢弃和解码校验错误。二进制
解码缓存也设置上限；校验失败只丢弃候选帧头并重新同步，避免噪声伪帧吞掉后续有效帧。

## 统一控制链

```text
演示界面 / 工程师界面
          ↓
     安全策略与服务
          ↓
      电机控制接口
          ↓
       串口 / CAN
          ↓
         MCU 保护
```

演示页面不直接编码或发送串口命令。`DemoService` 只产生受限运行计划；CAN 适配器仅开放
已经台架验证的 Type 3/1/2/4 路径。RS485 仅连接 Engineering Debug 固件，不提供运动控制。

CAN 演示固定把位置、Kp、Kd 和力矩前馈编码为物理零，只允许 ±20 motor rpm，并按 250 ms
刷新 Type 1。Type 2/21 故障、反馈超时、显式停止、断开连接或关闭程序都会停止刷新并请求
Type 4；MCU 的速度限幅、编码器门控和 1 秒命令看门狗仍是最终保护。

正式固件完成 FDCAN 初始化后自动进入 CAN 正常模式，但电机保持锁止，直到合法 Type 3/Type 1。
默认 CAN 界面若无法枚举，会指导用户检查供电、1 Mbps、接线、终端电阻和节点 ID。

## 当前目录职责

```text
easymotor_app.py                           唯一启动入口和应用控制器
easymotor/controllers/waveform.py         有界波形缓存、显示包络和丢帧统计
easymotor/core/safety_policy.py           演示档位、时长与计划校验
easymotor/i18n.py                          中英文产品文本
easymotor/models/telemetry.py             CPU/编码器强类型遥测模型
easymotor/services/demo_service.py        演示准备/运行状态
easymotor/services/endurance_service.py   CAN 长稳状态机
easymotor/protocols/can_motor.py           CAN 电机协议编解码
easymotor/protocols/waveform.py            UART5 二进制波形流解码
easymotor/transports/usb_can.py            官方 USB-CAN 传输
easymotor/features/demo/view.py           默认演示页面
easymotor/features/can_parameters/panel.py 共享主 transport 的 CAN 参数面板
```

CAN 控制事件与 RS485 调试文本使用独立队列。CAN 队列优先处理；RS485 文本队列有固定上限，
过载时丢弃最旧的调试事件，避免日志洪流阻塞 CAN 状态处理。波形二进制帧继续使用独立的有界
批队列，三路原始样本使用预分配的 16 位定长环形缓存，避免长时间采集时产生大量 Python
对象和不可控内存增长。CAN Type 1 刷新仍由独立工作线程执行，不依赖 Tk 界面刷新速度。

Python 文件名和通用类名不包含具体电机型号。设备兼容性和协议名称仍应在界面、配置和协议
文档中准确说明。

## 已移除的历史功能

主分支不再包含不可达的旧 Control 页、RS485 运动 fallback、手动 Iq 脉冲、任意速度输入、
固定占空比 hold 或 Stage-I 自动验收状态机。RS485 运动关键字仍会被显式拒绝，这是接口安全
防御，不是可用运动功能。旧 `robot_joint_app.py` 兼容入口也已删除。

## 后续拆分顺序

本轮已经从 `easymotor_app.py` 提取工程遥测模型、波形协议解码和有界波形缓存。CAN 启动
状态仍保持现有台架验证过的单一状态链，没有并行引入第二套会话状态源。主窗口暂时仍负责
Tk 控件编排、串口线程和安全动作调度，以避免一次重构改变已验证的命令时序。后续可按独立
提交继续提取：

1. 串口连接与设备服务；
2. CAN 电机启动、速度命令刷新和 Stop 服务；
3. 波形窗口视图；
4. 工程日志和诊断页面。

每次拆分必须保持现有协议字节、命令顺序、限值和硬件行为不变，并先通过软件测试，再由
工程师进行真实台架验收。
