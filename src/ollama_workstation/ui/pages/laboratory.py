"""System Laboratory: telemetry, diagnostics, benchmarks, recovery and local logs."""

from __future__ import annotations

import html
import json
import platform
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.recovery import RecoveryStatus, load_recovery_candidates
from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    RingGauge,
    SectionHeader,
    button,
    divider,
    label,
)
from ollama_workstation.ui.pages.common import (
    format_bytes,
    format_timestamp,
    prepare_list,
    short_path,
    two_line_item,
)
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS
from ollama_workstation.ui.workers import FunctionWorker


class LaboratoryPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._diagnostics: dict[str, Any] = {}
        self._build()
        self._connect()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(14)
        actions = QWidget()
        action_row = QHBoxLayout(actions)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(7)
        action_row.addWidget(button("Run diagnostics", on_click=self.run_diagnostics))
        action_row.addWidget(button("Refresh", variant="Secondary", on_click=self.refresh))
        root.addWidget(PageHeader(
            "SYSTEM / LABORATORY",
            "System Laboratory",
            "Measure the machine, inspect local evidence and export a diagnostic record.",
            actions=[actions],
        ))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._telemetry_tab(), "Telemetry")
        self.tabs.addTab(self._diagnostics_tab(), "Diagnostics")
        self.tabs.addTab(self._benchmarks_tab(), "Benchmarks")
        self.tabs.addTab(self._recovery_tab(), "Recovery")
        self.tabs.addTab(self._logs_tab(), "Logs")
        root.addWidget(self.tabs, 1)

    def _telemetry_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        gauges = QHBoxLayout()
        gauges.setSpacing(12)
        self.gpu = RingGauge("GPU LOAD", tone="cyan")
        self.vram = RingGauge("VRAM", tone="violet")
        self.memory = RingGauge("SYSTEM RAM", tone="blue")
        self.cpu = RingGauge("CPU", tone="green")
        self.disk = RingGauge("DISK", tone="amber")
        for widget in (self.gpu, self.vram, self.memory, self.cpu, self.disk):
            card = Card(margins=(12, 10, 12, 9))
            card.body.addWidget(widget)
            gauges.addWidget(card)
        layout.addLayout(gauges)
        details = Card(margins=(18, 15, 18, 14))
        details.body.addWidget(SectionHeader(
            "Host envelope", "Actual local readings; no simulated telemetry."
        ))
        details.body.addWidget(divider())
        self.host_text = QTextBrowser()
        self.host_text.setMinimumHeight(180)
        details.body.addWidget(self.host_text)
        layout.addWidget(details)
        layout.addStretch(1)
        return page

    def _diagnostics_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)
        controls = QHBoxLayout()
        self.export_button = button("Export diagnostic report", variant="Secondary",
                                    on_click=self.export_diagnostics)
        self.export_button.setEnabled(False)
        controls.addWidget(self.export_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.diagnostics = QTextBrowser()
        self.diagnostics.setOpenExternalLinks(True)
        layout.addWidget(self.diagnostics, 1)
        return page

    def _benchmarks_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)
        self.benchmark_summary = label(
            "Saved local core reports. Deterministic scoring only.", "Muted", wrap=True
        )
        layout.addWidget(self.benchmark_summary)
        self.benchmark_table = QTableWidget()
        self.benchmark_table.setColumnCount(6)
        self.benchmark_table.setHorizontalHeaderLabels(
            ["Model", "Score", "Passed", "Suite", "Completed", "Report"]
        )
        self.benchmark_table.horizontalHeader().setStretchLastSection(True)
        self.benchmark_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.benchmark_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.benchmark_table, 1)
        return page

    def _recovery_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)
        self.recovery_meta = label("Scanning checkpoint store…", "Muted", wrap=True)
        layout.addWidget(self.recovery_meta)
        self.recovery_list = QListWidget()
        prepare_list(self.recovery_list, row_height=50)
        layout.addWidget(self.recovery_list, 1)
        layout.addWidget(button(
            "Open recovery controls", variant="Secondary",
            on_click=lambda: self.context.navigate("history"),
        ))
        return page

    def _logs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(8)
        self.log_meta = label("Local JSONL log", "Muted", wrap=True)
        layout.addWidget(self.log_meta)
        self.logs = QTextBrowser()
        self.logs.setStyleSheet(
            f"QTextBrowser{{font-family:Consolas;font-size:11px;color:{COLORS['text_secondary']};}}"
        )
        layout.addWidget(self.logs, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Open log folder", variant="Ghost", on_click=self.open_log_folder))
        layout.addLayout(row)
        return page

    def _connect(self) -> None:
        self.context.events.telemetry.connect(self.apply_telemetry)
        self.context.events.data_changed.connect(self._data_changed)
        self.context.events.theme_changed.connect(self.refresh)

    def _data_changed(self, kind: str) -> None:
        if kind in {"runs", "models", "profiles", "settings", "workforce"}:
            self.refresh()

    def refresh(self) -> None:
        if self.context.telemetry:
            self.apply_telemetry(self.context.telemetry)
        self._refresh_benchmarks()
        self._refresh_recovery()
        self._refresh_logs()

    def apply_telemetry(self, payload: dict[str, Any]) -> None:
        gpus = payload.get("nvidia_gpus") or []
        gpu = gpus[0] if gpus else {}
        self.gpu.set_value(gpu.get("utilization_percent"), detail=str(gpu.get("name") or "GPU unavailable"))
        total = gpu.get("memory_total_mib")
        used = gpu.get("memory_used_mib")
        self.vram.set_value(
            used / total * 100 if total and used is not None else None,
            detail=(f"{used / 1024:.1f} / {total / 1024:.1f} GB"
                    if total and used is not None else "unavailable"),
        )
        memory = payload.get("system_memory") or {}
        ram_total = memory.get("total_bytes")
        ram_used = memory.get("used_bytes")
        self.memory.set_value(
            ram_used / ram_total * 100 if ram_total and ram_used is not None else None,
            detail=f"{format_bytes(ram_used)} / {format_bytes(ram_total)}" if ram_total else "",
        )
        self.cpu.set_value(payload.get("cpu_percent"), detail=f"{payload.get('logical_cpu_count', '—')} threads")
        disk = payload.get("disk") or {}
        self.disk.set_value(disk.get("percent"), detail=f"{format_bytes(disk.get('free_bytes'))} free")
        host_html = (
            f"<div style='color:{COLORS['text_secondary']};font-size:12px;'>"
            f"<p><b style='color:{COLORS['ice']};'>{html.escape(str(payload.get('os') or 'Unknown OS'))}</b><br>"
            f"{html.escape(str(payload.get('processor') or 'Unknown processor'))}</p>"
            f"<p>Memory: {format_bytes(ram_total)} · Logical CPUs: {payload.get('logical_cpu_count')}<br>"
            f"GPU: {html.escape(str(gpu.get('name') or 'No NVIDIA telemetry'))} · "
            f"{gpu.get('temperature_c', '—')}°C<br>"
            f"Snapshot: {format_timestamp(payload.get('captured_at'))}</p>"
            f"<p>Interface: 11vatedTech Intelligence · Qt {html.escape(qVersion())} · "
            f"Python {html.escape(platform.python_version())}<br>"
            f"Workspace: {html.escape(short_path(self.context.workspace_path(), 64))}</p>"
            f"<p style='color:{COLORS['faint']};'>Telemetry is local only and is not uploaded.</p></div>"
        )
        self.host_text.setHtml(host_html)

    def run_diagnostics(self) -> None:
        self.export_button.setEnabled(False)
        self.diagnostics.setHtml("Running local integrity and policy checks…")
        worker = FunctionWorker(self._diagnostic_task)
        worker.signals.result.connect(self._diagnostics_ready)
        worker.signals.error.connect(self._diagnostics_failed)
        self.context.pool.start(worker)

    def _diagnostic_task(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        try:
            with sqlite3.connect(self.context.paths.database_file) as db:
                row = db.execute("PRAGMA integrity_check").fetchone()
            integrity = str(row[0]) if row else "unknown"
        except Exception as exc:
            integrity = f"{type(exc).__name__}: {exc}"
        checks.append({
            "name": "SQLite integrity",
            "status": "pass" if integrity == "ok" else "fail",
            "detail": integrity,
        })
        checks.append({
            "name": "Checkpoint key",
            "status": "pass" if self.context.repository.checkpoint_key_path.exists() else "warn",
            "detail": str(self.context.repository.checkpoint_key_path),
        })
        checks.append({
            "name": "Ollama runtime",
            "status": "pass" if self.context.ollama_online else "warn",
            "detail": f"{self.context.settings.ollama_base_url} · {self.context.ollama_version or 'unknown'}",
        })
        checks.append({
            "name": "Command execution",
            "status": "pass" if not self.context.settings.allow_shell_tools else "warn",
            "detail": "disabled in desktop policy" if not self.context.settings.allow_shell_tools else "enabled",
        })
        checks.append({
            "name": "MCP servers",
            "status": "pass" if not self.context.settings.mcp_servers else "info",
            "detail": f"{len(self.context.settings.mcp_servers)} configured; exact grants only",
        })
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "checks": checks,
            "host": self.context.telemetry,
            "models": [item.name for item in self.context.models],
            "profiled_models": len(self.context.profiles),
            "runs": len(self.context.repository.list_runs(10)),
            "workforce_runs": len(self.context.repository.list_workforce_runs(10)),
            "logs": str(self.context.paths.logs_dir),
        }

    def _diagnostics_ready(self, payload: dict[str, Any]) -> None:
        self._diagnostics = payload
        self.export_button.setEnabled(True)
        checks = payload.get("checks") or []
        rows = "".join(
            f"<tr><td><span style='color:"
            f"{COLORS['green'] if item['status'] == 'pass' else COLORS['amber'] if item['status'] in {'warn', 'info'} else COLORS['red']};'>"
            f"{html.escape(str(item['status']).upper())}</span></td>"
            f"<td><b>{html.escape(str(item['name']))}</b></td>"
            f"<td style='font-family:Consolas;font-size:10px;'>{html.escape(str(item['detail']))}</td></tr>"
            for item in checks
        )
        self.diagnostics.setHtml(
            f"<div style='color:{COLORS['text_secondary']};font-size:12px;'>"
            f"<p style='color:{COLORS['faint']};'>Generated {format_timestamp(payload.get('generated_at'))}</p>"
            f"<table cellspacing='7'>{rows}</table>"
            f"<p><b style='color:{COLORS['ice']};'>Model evidence</b><br>"
            f"{len(payload.get('models') or [])} installed · {payload.get('profiled_models')} profiled · "
            f"{payload.get('runs')} recent agent runs · {payload.get('workforce_runs')} workforce runs.</p>"
            f"<p><b style='color:{COLORS['ice']};'>Routing surface</b><br>"
            + "".join(
                f"<span style='color:{COLORS['cyan']};'>{html.escape(name)}</span> · "
                for name in (payload.get("models") or [])[:8]
            )
            + "</p></div>"
        )
        self.context.notify("Local diagnostics completed.")
        self.context.set_state("complete", "Diagnostics complete")

    def _diagnostics_failed(self, message: str) -> None:
        self.diagnostics.setHtml(
            f"<p style='color:{COLORS['red']};'>{html.escape(message)}</p>"
        )
        self.context.notify("Diagnostics failed.")

    def export_diagnostics(self) -> None:
        if not self._diagnostics:
            return
        destination = (
            self.context.paths.profiles_dir / "diagnostics"
            / f"diagnostic-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self._diagnostics, indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        self.context.notify(f"Diagnostic report written to {destination}")
        QMessageBox.information(self, "Diagnostic report", f"Written locally:\n{destination}")

    def _refresh_benchmarks(self) -> None:
        records: list[dict[str, Any]] = []
        for path in sorted((self.context.paths.profiles_dir / "benchmarks").glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                data["_path"] = str(path)
                records.append(data)
        records.sort(key=lambda item: str(item.get("completed_at") or ""), reverse=True)
        self.benchmark_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = [
                str(record.get("model_name") or "—"),
                f"{float(record.get('score') or 0):.0f}/100",
                f"{record.get('total_passed', '—')}/{record.get('total_attempts', '—')}",
                f"{record.get('suite_id', '—')}@{record.get('suite_version', '—')}",
                format_timestamp(record.get("completed_at")),
                Path(str(record.get("_path"))).name,
            ]
            for column, value in enumerate(values):
                self.benchmark_table.setItem(row, column, QTableWidgetItem(value))
        self.benchmark_table.resizeColumnsToContents()
        self.benchmark_summary.setText(
            f"{len(records)} saved report(s). Deterministic local scoring; not a public leaderboard."
        )

    def _refresh_recovery(self) -> None:
        try:
            candidates = load_recovery_candidates(self.context.repository)
        except Exception as exc:
            self.recovery_meta.setText(f"Recovery scan failed: {exc}")
            return
        self.recovery_list.clear()
        for candidate in candidates:
            item = two_line_item(
                f"{candidate.status.value.upper()} · {candidate.agent_title or 'Agent'} · "
                f"step {candidate.step}/{candidate.step_budget or '?'}\n"
                f"{format_timestamp(candidate.updated_at)} · {candidate.reason}"
            )
            if candidate.status is RecoveryStatus.RESUMABLE:
                item.setForeground(Qt.GlobalColor.cyan)
            elif candidate.status is not RecoveryStatus.SUPERSEDED:
                item.setForeground(Qt.GlobalColor.yellow)
            self.recovery_list.addItem(item)
        if not candidates:
            self.recovery_list.addItem("No checkpoint requires recovery attention.")
        self.recovery_meta.setText(f"{len(candidates)} integrity-checked checkpoint(s) in the local store.")

    def _refresh_logs(self) -> None:
        log_path = self.context.paths.logs_dir / "workstation.jsonl"
        self.log_meta.setText(str(log_path))
        if not log_path.exists():
            self.logs.setPlainText("No structured log has been written yet.")
            return
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
        except OSError as exc:
            self.logs.setPlainText(f"Unable to read log: {exc}")
            return
        rendered = []
        for line in lines:
            try:
                payload = json.loads(line)
            except ValueError:
                rendered.append(html.escape(line))
                continue
            level = html.escape(str(payload.get("level") or "INFO"))
            message = html.escape(str(payload.get("message") or ""))
            stamp = html.escape(str(payload.get("timestamp") or ""))
            rendered.append(
                f"<span style='color:{COLORS['faint']};'>{stamp}</span> "
                f"<span style='color:{COLORS['cyan']};'>{level}</span> {message}"
            )
        self.logs.setHtml(
            f"<pre style='font-family:Consolas;font-size:10px;white-space:pre-wrap;'>"
            f"{'<br>'.join(rendered)}</pre>"
        )

    def open_log_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.paths.logs_dir)))
