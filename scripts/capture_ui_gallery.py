"""Capture the 11vatedTech Intelligence UI at desktop and compact widths.

Run with the desktop extra installed and a native Qt platform plugin:

    python scripts/capture_ui_gallery.py --output tests/screenshots

This script performs construction, live inventory and telemetry waits, page
navigation and real screenshot grabs. It deliberately does not start any
inference request, approve a workforce plan or mutate user settings.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ollama_workstation.ui.main_window import MainWindow
from ollama_workstation.ui.theme import apply_theme

PAGE_ORDER = (
    "command",
    "chat",
    "models",
    "workforce",
    "operations",
    "integrations",
    "laboratory",
    "history",
    "settings",
)

MATRIX_SIZES = (
    (1280, 800),
    (1024, 768),
    (900, 700),
)

MATRIX_PAGES = ("command", "chat", "models", "workforce")


class GalleryCapture:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.window = MainWindow()
        self.window.resize(1480, 940)
        self.window.show()
        self.original_page = self.window.settings.last_page
        self.index = 0
        self.narrow_index = 0
        self.matrix_size_index = 0
        self.matrix_page_index = 0

    def start(self) -> None:
        QTimer.singleShot(7000, self._begin)

    def _begin(self) -> None:
        context = self.window.context
        print(
            f"online={context.ollama_online} models={len(context.models)} "
            f"profiles={len(context.profiles)} telemetry={bool(context.telemetry)}"
        )
        self._prepare_demo_pages()
        QTimer.singleShot(350, self._capture_next)

    def _prepare_demo_pages(self) -> None:
        """Put read-only, genuine data on otherwise quiet pages for the gallery."""
        model_page = self.window.pages["models"]
        def evidence_score(model: object) -> int:
            profile = self.window.context.profile_for(getattr(model, "name", ""))
            if profile is None:
                return -1
            return sum(
                1 for item in profile.capabilities
                if item.status in {"passed", "partial", "failed"}
            )

        profiled = max(
            self.window.context.models,
            key=evidence_score,
            default=None,
        )
        if profiled is not None:
            model_page._select_name(profiled.name)

        workforce = self.window.pages["workforce"]
        if workforce.plan is None:
            workforce.objective.setPlainText(
                "Audit the local workspace for documentation gaps, identify the highest-risk "
                "missing evidence, and verify each finding against the repository."
            )
            workforce.review_plan()

    def _capture_next(self) -> None:
        if self.index >= len(PAGE_ORDER):
            self._resize_compact()
            return
        key = PAGE_ORDER[self.index]
        self.window.navigate(key, persist=False)
        QTimer.singleShot(520, lambda: self._save_page(key))

    def _save_page(self, key: str) -> None:
        path = self.output / f"windows-{key}.png"
        image = self.window.grab()
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"Could not save {path}")
        print(f"captured {key} -> {path.name} ({image.width()}x{image.height()})")
        self.index += 1
        QTimer.singleShot(120, self._capture_next)

    def _resize_compact(self) -> None:
        self.window.resize(1120, 720)
        QTimer.singleShot(650, lambda: self._capture_compact("command"))

    def _capture_compact(self, key: str) -> None:
        self.window.navigate(key, persist=False)
        QTimer.singleShot(520, lambda: self._save_compact(key))

    def _save_compact(self, key: str) -> None:
        path = self.output / f"windows-narrow-{key}.png"
        image = self.window.grab()
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"Could not save {path}")
        print(f"captured compact {key} -> {path.name} ({image.width()}x{image.height()})")
        if self.narrow_index == 0:
            self.narrow_index = 1
            QTimer.singleShot(120, lambda: self._capture_compact("chat"))
            return
        if self.narrow_index == 1:
            self.narrow_index = 2
            QTimer.singleShot(120, lambda: self._capture_compact("workforce"))
            return
        self._capture_matrix_size()

    def _capture_matrix_size(self) -> None:
        if self.matrix_size_index >= len(MATRIX_SIZES):
            self._finish()
            return
        width, height = MATRIX_SIZES[self.matrix_size_index]
        self.matrix_page_index = 0
        self.window.resize(width, height)
        QTimer.singleShot(650, self._capture_matrix_page)

    def _capture_matrix_page(self) -> None:
        width, height = MATRIX_SIZES[self.matrix_size_index]
        key = MATRIX_PAGES[self.matrix_page_index]
        self.window.navigate(key, persist=False)
        QTimer.singleShot(520, lambda: self._save_matrix_page(width, height, key))

    def _save_matrix_page(self, width: int, height: int, key: str) -> None:
        path = self.output / f"windows-{width}x{height}-{key}.png"
        image = self.window.grab()
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"Could not save {path}")
        print(f"captured matrix {width}x{height} {key} -> {path.name} ({image.width()}x{image.height()})")
        self.matrix_page_index += 1
        if self.matrix_page_index < len(MATRIX_PAGES):
            QTimer.singleShot(120, self._capture_matrix_page)
            return
        self.matrix_size_index += 1
        QTimer.singleShot(120, self._capture_matrix_size)

    def _finish(self) -> None:
        page = self.window.pages["command"]
        print(
            "gauges="
            f"{page.gpu_gauge._value},{page.vram_gauge._value},{page.ram_gauge._value},"
            f"{page.cpu_gauge._value},{page.disk_gauge._value}"
        )
        # Preserve the user's last workspace rather than the gallery's final page.
        self.window._last_page = self.original_page
        self.window._shutdown()
        self.window.close()
        QApplication.instance().quit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/screenshots"),
        help="Directory for PNG captures (default: tests/screenshots).",
    )
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("11vatedTech Intelligence Gallery")
    apply_theme(app)
    gallery = GalleryCapture(args.output)
    gallery.start()
    app.exec()
    print(
        f"gallery complete: {len(PAGE_ORDER)} desktop, 3 compact, "
        f"{len(MATRIX_SIZES) * len(MATRIX_PAGES)} responsive"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
