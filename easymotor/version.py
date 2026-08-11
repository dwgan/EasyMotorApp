"""Single source of truth for the EasyMotor product version."""

from __future__ import annotations

import os
import re
from typing import Final


VERSION: Final = (1, 0, 0)
SOURCE_VERSION: Final = ".".join(str(part) for part in VERSION)
_frozen_version = os.environ.get("EASYMOTOR_BUILD_VERSION", "").strip()
__version__: Final = (
    _frozen_version
    if re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", _frozen_version)
    else SOURCE_VERSION
)


def window_title(product_name: str = "EasyMotor") -> str:
    return f"{product_name} v{__version__}"
