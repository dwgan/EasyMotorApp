"""Single source of truth for the EasyMotor product version."""

from __future__ import annotations

from typing import Final


VERSION: Final = (1, 0, 1)
__version__: Final = ".".join(str(part) for part in VERSION)


def window_title(product_name: str = "EasyMotor") -> str:
    return f"{product_name} v{__version__}"
