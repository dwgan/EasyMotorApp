# 上位机双模式架构

## 产品目标

EasyMotor 同时服务现场演示和工程调试：每次启动默认进入英文、CAN 接口的简洁演示模式；
可切换中文或备用 RS485，用户主动确认后才能进入工程师模式。两种界面只决定功能可见性，
不能绕过统一的安全策略、命令状态机和 MCU 保护。

## 模式边界

演示模式只提供设备连接、5/10/20 motor rpm、正转、反转和停止。默认运行 5 秒并自动停止；
“一直转”必须逐次主动勾选，通过 Keepalive 维持，停止、断线、故障或 MCU 看门狗均会结束
运行。演示操作自动执行 `start → 等待 RUN → speed → keep → stop`。

工程师模式保留手动 Iq/速度、持续运行、自动验收、实时遥测、波形、CAN、长稳和日志。
进入或退出工程师模式前必须停止电机；高级模式不会提高软件或固件限值。

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
已经台架验证的 Type 3/1/2/4 路径，RS485 继续复用现有 start/speed/keep/stop 控制链。

CAN 演示固定把位置、Kp、Kd 和力矩前馈编码为物理零，只允许 ±20 motor rpm，并按 250 ms
刷新 Type 1。Type 2/21 故障、反馈超时、显式停止、断开连接或关闭程序都会停止刷新并请求
Type 4；MCU 的速度限幅、编码器门控和 1 秒命令看门狗仍是最终保护。

当前固件每次上电让 CAN 收发器进入待机。默认 CAN 界面若在 1 秒内无法枚举，会指导工程师
通过备用 RS485 执行一次 `can stby 0`。把固件上电默认值改为 CAN 正常模式属于独立的硬件
行为变更，本阶段未擅自修改。

## 当前目录职责

```text
easymotor_app.py                           主启动入口和现有工程师控制器
robot_joint_app.py                         旧入口兼容层
easymotor/core/safety_policy.py           演示档位、时长与计划校验
easymotor/i18n.py                          中英文产品文本
easymotor/services/demo_service.py        演示准备/运行状态
easymotor/services/endurance_service.py   CAN 长稳状态机
easymotor/protocols/can_motor.py           CAN 电机协议编解码
easymotor/transports/usb_can.py            官方 USB-CAN 传输
easymotor/features/demo/view.py           默认演示页面
easymotor/features/can_tool/window.py     工程师 CAN 工具窗口
```

Python 文件名和通用类名不包含具体电机型号。设备兼容性和协议名称仍应在界面、配置和协议
文档中准确说明。

## 后续拆分顺序

当前阶段先建立双模式外壳并验证现场操作。完成界面人工验收后，再按独立提交依次从
`easymotor_app.py` 提取：

1. 串口连接与设备服务；
2. 电机启动、速度、Iq、Keepalive 和 Stop 服务；
3. 遥测解析与状态模型；
4. 自动验收功能；
5. 波形功能；
6. 工程日志和诊断页面。

每次拆分必须保持现有协议字节、命令顺序、限值和硬件行为不变，并先通过软件测试，再由
工程师进行真实台架验收。
