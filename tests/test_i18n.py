import ast
import tkinter as tk
import unittest
from pathlib import Path

from easymotor.i18n import LocalizedStringVar, has_han, localize_legacy, tr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_TEXT_SOURCES = (
    PROJECT_ROOT / "easymotor_app.py",
    *(PROJECT_ROOT / "easymotor").rglob("*.py"),
)


class TranslationTests(unittest.TestCase):
    def test_product_shell_has_both_languages(self):
        self.assertEqual(tr("en", "app_title"), "EasyMotor")
        self.assertEqual(
            tr("en", "copyright"),
            "Copyright © 2026 STMicroelectronics Shenzhen R&D Co., Ltd.",
        )
        self.assertEqual(
            tr("zh_CN", "copyright"),
            "版权所有 © 2026 意法半导体研发（深圳）有限公司",
        )
        self.assertEqual(tr("en", "engineer_mode"), "Advanced")
        self.assertEqual(tr("zh_CN", "engineer_mode"), "高级模式")

    def test_engineering_statuses_translate_without_chinese(self):
        examples = (
            "MCI: 未知",
            "位置: 等待固件 MOTION 遥测",
            "电角度纹波: 等待停机后 EANG_RIPPLE",
            "长稳进度: 2/10，超时 0，拒绝 0",
            "参数 0x7019 mechPos = 0.6894817",
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertFalse(has_han(localize_legacy(source, "en")))
                self.assertEqual(localize_legacy(source, "zh_CN"), source)

    def test_localized_string_var_rerenders_from_original_source(self):
        try:
            interpreter = tk.Tcl()
        except tk.TclError as exc:
            self.skipTest(f"Tcl runtime is unavailable: {exc}")
        language = {"value": "en"}
        value = LocalizedStringVar(
            interpreter,
            lambda: language["value"],
            "CAN: 未初始化",
        )
        self.assertEqual(value.get(), "CAN: Not initialized")
        language["value"] = "zh_CN"
        value.refresh_language()
        self.assertEqual(value.get(), "CAN: 未初始化")

    def test_all_user_facing_source_strings_have_an_english_rendering(self):
        failures: list[str] = []
        for path in USER_TEXT_SOURCES:
            if path.name == "i18n.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and has_han(node.value)
                ):
                    rendered = localize_legacy(node.value, "en")
                    if has_han(rendered):
                        failures.append(f"{path.name}:{node.lineno}: {node.value!r}")
                elif isinstance(node, ast.JoinedStr):
                    source = "".join(
                        part.value
                        if isinstance(part, ast.Constant)
                        and isinstance(part.value, str)
                        else "VALUE"
                        for part in node.values
                    )
                    if has_han(source) and has_han(localize_legacy(source, "en")):
                        failures.append(f"{path.name}:{node.lineno}: {source!r}")
        self.assertEqual(failures, [], "Missing English UI text:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
