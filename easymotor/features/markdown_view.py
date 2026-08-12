"""Small, dependency-free Markdown renderer for trusted release-note text."""

from __future__ import annotations

import re
import tkinter as tk
import webbrowser


_INLINE = re.compile(
    r"(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_|\[[^]\n]+\]\(https://[^)\s]+\))"
)
_LINK = re.compile(r"\[([^]\n]+)\]\((https://[^)\s]+)\)")


def inline_runs(text: str) -> list[tuple[str, str | None, str | None]]:
    """Return display text, style and optional URL without exposing Markdown syntax."""
    runs: list[tuple[str, str | None, str | None]] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            runs.append((text[position : match.start()], None, None))
        token = match.group(0)
        link = _LINK.fullmatch(token)
        if link:
            runs.append((link.group(1), "link", link.group(2)))
        elif token.startswith(("**", "__")):
            runs.append((token[2:-2], "bold", None))
        elif token.startswith("`"):
            runs.append((token[1:-1], "code", None))
        else:
            runs.append((token[1:-1], "italic", None))
        position = match.end()
    if position < len(text):
        runs.append((text[position:], None, None))
    return runs


def render_markdown(widget: tk.Text, markdown: str) -> None:
    """Render the GitHub release-note subset used by EasyMotor Publisher."""
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.tag_configure("h1", font=("Microsoft YaHei UI", 15, "bold"), spacing1=8, spacing3=5)
    widget.tag_configure("h2", font=("Microsoft YaHei UI", 13, "bold"), spacing1=7, spacing3=4)
    widget.tag_configure("h3", font=("Microsoft YaHei UI", 11, "bold"), spacing1=5, spacing3=3)
    widget.tag_configure("bold", font=("Microsoft YaHei UI", 10, "bold"))
    widget.tag_configure("italic", font=("Microsoft YaHei UI", 10, "italic"))
    widget.tag_configure("code", font=("Consolas", 9), background="#E8EFF5")
    widget.tag_configure("link", foreground="#1E5A88", underline=True)
    widget.tag_configure("quote", foreground="#50708E", lmargin1=14, lmargin2=14)
    widget.tag_configure("bullet", lmargin1=12, lmargin2=28)

    link_index = 0
    for raw_line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = re.match(r"^(#{1,3})\s+(.*)$", raw_line)
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", raw_line)
        numbered = re.match(r"^\s*(\d+)\.\s+(.*)$", raw_line)
        quote = re.match(r"^>\s?(.*)$", raw_line)
        line_tag: str | None = None
        prefix = ""
        content = raw_line
        if heading:
            line_tag, content = f"h{len(heading.group(1))}", heading.group(2)
        elif bullet:
            line_tag, prefix, content = "bullet", "• ", bullet.group(1)
        elif numbered:
            line_tag, prefix, content = "bullet", f"{numbered.group(1)}. ", numbered.group(2)
        elif quote:
            line_tag, prefix, content = "quote", "│ ", quote.group(1)
        if prefix:
            widget.insert(tk.END, prefix, (line_tag,) if line_tag else ())
        for display, style, url in inline_runs(content):
            tags = tuple(tag for tag in (line_tag, style) if tag)
            if url is not None:
                link_index += 1
                unique_tag = f"release_link_{link_index}"
                tags += (unique_tag,)
                widget.tag_bind(unique_tag, "<Button-1>", lambda _event, target=url: webbrowser.open(target))
                widget.tag_bind(unique_tag, "<Enter>", lambda _event: widget.configure(cursor="hand2"))
                widget.tag_bind(unique_tag, "<Leave>", lambda _event: widget.configure(cursor=""))
            widget.insert(tk.END, display, tags)
        widget.insert(tk.END, "\n", (line_tag,) if line_tag else ())
    widget.configure(state=tk.DISABLED)
