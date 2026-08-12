# EasyMotor development rules

- This repository is the standalone EasyMotor desktop application. Do not add
  filesystem or import dependencies on sibling RobotJoint or Publisher projects.
- Runtime dependencies are declared in `requirements.txt`; build dependencies
  are declared in `requirements-build.txt`.
- Run `python -m unittest discover -s tests -v` before committing application,
  protocol, updater, packaging, or UI changes.
- Build the supported Windows artifact with
  `build_easymotor_release.ps1`; the result must remain a single x64 EXE.
- `easymotor/version.py` is the only product-version source.
- Do not connect to or operate real hardware during automated validation.
- Never commit tokens, credentials, customer data, logs, generated wave data,
  `build/`, or `release/` artifacts.
