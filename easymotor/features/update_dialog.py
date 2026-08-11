"""Manual, bilingual EasyMotor update dialog."""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import ttk
import tkinter as tk
from typing import Callable

from easymotor.branding import apply_window_icon
from easymotor.i18n import tr
from easymotor.updates import GitHubReleaseClient, UpdateCancelled, UpdateRelease
from easymotor.updates.installer import update_root
from easymotor.updates.pe import validate_easymotor_executable


class UpdateDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        *,
        language_getter: Callable[[], str],
        current_version: str,
        on_install_ready: Callable[[Path, UpdateRelease], None],
    ) -> None:
        super().__init__(master)
        apply_window_icon(self)
        self._language_getter = language_getter
        self.current_version = current_version
        self._on_install_ready = on_install_ready
        self.client = GitHubReleaseClient()
        self.release: UpdateRelease | None = None
        self.cancel_event = threading.Event()
        self.worker_active = False
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.resizable(False, False)
        self.transient(master)

        outer = ttk.Frame(self, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)
        self.heading = ttk.Label(outer, font=("Microsoft YaHei UI", 15, "bold"))
        self.heading.pack(anchor="w")
        self.status_var = tk.StringVar()
        ttk.Label(outer, textvariable=self.status_var, wraplength=520).pack(
            fill=tk.X, pady=(10, 8), anchor="w"
        )
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 10))
        self.notes = tk.Text(outer, width=72, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.notes.pack(fill=tk.BOTH, expand=True)
        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(12, 0))
        self.primary_button = ttk.Button(buttons, command=self._primary_action)
        self.primary_button.pack(side=tk.RIGHT)
        self.cancel_button = ttk.Button(buttons, command=self.close)
        self.cancel_button.pack(side=tk.RIGHT, padx=(0, 8))
        self._render_language()
        self.after(50, self._start_check)

    @property
    def language(self) -> str:
        return self._language_getter()

    def _render_language(self) -> None:
        language = self.language
        self.title(tr(language, "update_title"))
        self.heading.configure(text=tr(language, "update_heading"))
        self.cancel_button.configure(text=tr(language, "cancel"))
        if self.release is None:
            self.primary_button.configure(text=tr(language, "check_again"), state=tk.DISABLED)

    def refresh_language(self) -> None:
        self._render_language()
        if self.release is not None:
            self._show_release(self.release)

    def _start_check(self) -> None:
        if self.worker_active:
            return
        self.worker_active = True
        self.status_var.set(tr(self.language, "checking_updates"))
        self.primary_button.configure(state=tk.DISABLED)

        def worker() -> None:
            try:
                release = self.client.fetch_latest()
            except Exception as exc:
                self.after(0, self._check_failed, exc)
                return
            self.after(0, self._check_complete, release)

        threading.Thread(target=worker, daemon=True).start()

    def _check_complete(self, release: UpdateRelease) -> None:
        self.worker_active = False
        self.release = release
        self._show_release(release)

    def _show_release(self, release: UpdateRelease) -> None:
        self._set_notes(release.release_notes or tr(self.language, "no_release_notes"))
        if release.is_newer_than(self.current_version):
            size_mib = release.manifest.asset_size / (1024 * 1024)
            self.status_var.set(
                tr(
                    self.language,
                    "update_available",
                    current=self.current_version,
                    latest=release.version,
                    size=f"{size_mib:.1f}",
                )
            )
            action_key = "download_install" if getattr(sys, "frozen", False) else "open_release_page"
            self.primary_button.configure(text=tr(self.language, action_key), state=tk.NORMAL)
        else:
            self.status_var.set(tr(self.language, "already_latest", version=self.current_version))
            self.primary_button.configure(text=tr(self.language, "check_again"), state=tk.NORMAL)

    def _check_failed(self, exc: Exception) -> None:
        self.worker_active = False
        self.status_var.set(tr(self.language, "update_check_failed", error=str(exc)))
        self.primary_button.configure(text=tr(self.language, "check_again"), state=tk.NORMAL)

    def _set_notes(self, text: str) -> None:
        self.notes.configure(state=tk.NORMAL)
        self.notes.delete("1.0", tk.END)
        self.notes.insert("1.0", text)
        self.notes.configure(state=tk.DISABLED)

    def _primary_action(self) -> None:
        if self.release is None or not self.release.is_newer_than(self.current_version):
            self._start_check()
            return
        if not getattr(sys, "frozen", False):
            webbrowser.open(self.release.release_url)
            return
        self._start_download()

    def _start_download(self) -> None:
        if self.worker_active or self.release is None:
            return
        release = self.release
        self.worker_active = True
        self.cancel_event.clear()
        self.progress.configure(value=0)
        self.status_var.set(tr(self.language, "downloading_update", version=release.version))
        self.primary_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(text=tr(self.language, "cancel_download"))
        destination = update_root() / release.version / release.manifest.asset_name

        def progress(done: int, total: int) -> None:
            percent = 0 if total <= 0 else max(0, min(100, int(done * 100 / total)))
            self.after(0, lambda: self.progress.configure(value=percent))

        def worker() -> None:
            try:
                path = self.client.download(
                    release, destination, progress=progress, cancel=self.cancel_event
                )
                validate_easymotor_executable(path, release.version)
            except Exception as exc:
                self.after(0, self._download_failed, exc)
                return
            self.after(0, self._download_complete, path, release)

        threading.Thread(target=worker, daemon=True).start()

    def _download_complete(self, path: Path, release: UpdateRelease) -> None:
        self.worker_active = False
        self.progress.configure(value=100)
        self.status_var.set(tr(self.language, "update_verified"))
        self.cancel_button.configure(text=tr(self.language, "cancel"))
        self._on_install_ready(path, release)

    def _download_failed(self, exc: Exception) -> None:
        self.worker_active = False
        self.cancel_button.configure(text=tr(self.language, "cancel"))
        if isinstance(exc, UpdateCancelled):
            self.status_var.set(tr(self.language, "download_cancelled"))
        else:
            self.status_var.set(tr(self.language, "update_download_failed", error=str(exc)))
        self.primary_button.configure(text=tr(self.language, "retry_download"), state=tk.NORMAL)

    def close(self) -> None:
        if self.worker_active:
            self.cancel_event.set()
            self.status_var.set(tr(self.language, "cancelling_download"))
            return
        self.destroy()
