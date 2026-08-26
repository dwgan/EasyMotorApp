# EasyMotor v1.0.3

## Highlights

EasyMotor v1.0.3 improves customer demonstrations with a dedicated speed PI
mode and 5/30/100 motor-rpm presets. It also adds rotor-alignment maintenance
for compatible EasyMotor firmware while retaining the OpenArmX/RS04 MIT bench
workflow introduced in v1.0.2.

Motor commands continue to use CAN exclusively. RS485 remains a read-only
engineering and diagnostic interface.

## New Features

- Added an explicit speed PI customer-demo workflow.
  - Selects firmware `run_mode=2` before enabling the motor.
  - Sends the live speed reference through parameter `0x700A`.
  - Refreshes the active speed reference every 250 ms.
  - Restores the safe `run_mode=0` MIT default after the motor returns to IDLE.
- Updated the customer-demo speed presets to **5/30/100 motor rpm**.
  - Supports forward and reverse rotation.
  - Retains the fixed 5-second timed demonstration.
  - Retains the explicit continuous-run option until **STOP** is pressed.
- Added rotor-alignment maintenance parameter support:
  - `0x7033 rotor_alignment_valid`
  - EasyMotorApp may clear this status by writing `0`, allowing compatible
    firmware to perform alignment again.
  - Writing `1` is intentionally rejected; only firmware may validate an
    alignment result.

## Improvements

- Separated customer-demo speed control from MIT Type 1 control. Demo rotation
  now uses the firmware's dedicated speed PI loop instead of encoding a
  velocity-only MIT command.
- Added failure-safe cleanup when demo enable or RUN-state confirmation fails.
  The application requests a stop instead of leaving a partially started demo.
- Prevented the demo workflow from taking over a motor that was already left in
  RUN by another control path.
- Improved mode-transition logging, including speed-mode selection and MIT-mode
  restoration.
- Preserved OpenArmX startup compatibility without requiring a Type 6 zero
  command before every enable.
- Retained the v1.0.2 MIT bench workflow, dedicated operation log, one-click log
  clearing, torque-calibration parameters, and measured-Iq display.
- Aligned Type 1 / Type 2 protocol scaling with the current official RS04
  ranges used by EasyMotor firmware:
  - Position: `-4π..4π rad`
  - Velocity: `-15..15 rad/s`
  - Kp: `0..5000`
  - Kd: `0..100`
  - Torque: `-120..120 Nm`

## Compatibility

- Operating system: Windows 10/11 x64.
- Motor-control interface: USB-CAN.
- USB-CAN serial baud rate: 921600 baud.
- CAN bitrate: 1 Mbps.
- CAN frame format: 29-bit extended frame.
- Engineering debug interface: RS485.
- RS485 debug baud rate: configurable, up to 4 Mbps.
- MIT bench mode requires compatible EasyMotor firmware using the current RS04
  private-protocol ranges.
- The new speed PI demo requires firmware support for `run_mode=2` at `0x7005`
  and the live speed reference at `0x700A`.
- Rotor-alignment maintenance requires firmware support for parameter `0x7033`.
- Parameter configuration should continue to be performed from EasyMotorApp.

## Safety Notes

- Perform the first MIT or speed-demo test without mechanical load.
- Keep emergency-stop and independent power-disconnection hardware available.
- The 100 rpm preset retains a 10 motor-rpm/s firmware ramp. A timed 5-second
  demo will stop before reaching 100 rpm; select continuous run when the full
  preset speed must be demonstrated.
- Stop the motor before changing direction, speed, interface, or control mode.
- Clicking **STOP**, disconnecting CAN, detecting a fault, or the firmware
  command watchdog ends the active demo.
- `tau_ff` remains locked until measured torque calibration is completed and
  saved.
- Firmware and hardware safety mechanisms remain the final authority.
- Temperature values are diagnostic estimates and must not replace hardware
  overtemperature protection.

## Upgrade Instructions

Existing users can select **Check for updates** inside EasyMotor.

Alternatively, download and run:

`EasyMotor_v1.0.3_win-x64.exe`

Use the matching current EasyMotor firmware when using the speed PI demo or
rotor-alignment maintenance features.

## Known Issues

- Linux release packaging is prepared but requires Docker Desktop with Buildx.
- Motor temperature accuracy depends on the installed NTC specification.
- MIT operation may require tuning for different motors and mechanical loads.
- The 100 rpm timed demo cannot reach its final speed within the default
  5-second duration because the firmware deliberately limits acceleration.
- Torque feed-forward remains unavailable until a measured torque calibration
  is saved.
- Official motor_toolV13 parameter-mode compatibility remains intentionally
  limited to the supported MIT motion-control and documented maintenance paths.
