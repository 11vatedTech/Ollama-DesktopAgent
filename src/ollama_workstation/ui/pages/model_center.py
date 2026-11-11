"""Model Intelligence Center: inventory, profiles, evidence and comparisons."""

from __future__ import annotations

import html
import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.benchmarking.runner import BenchmarkRunner
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.profiling.research import HuggingFaceResearcher, attach_model_card_evidence
from ollama_workstation.ui.components import (
    Card,
    MetricTile,
    PageHeader,
    Pill,
    SectionHeader,
    Sparkline,
    button,
    divider,
    label,
)
from ollama_workstation.ui.pages.common import (
    empty_panel,
    format_bytes,
    format_timestamp,
    prepare_list,
    two_line_item,
)
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS
from ollama_workstation.ui.workers import FunctionWorker


class CapabilityBar(QWidget):
    def __init__(self, name: str, profile: ModelProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        capability = next(
            (item for item in profile.capabilities if item.capability == name),
            None,
        )
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(2)
        name_label = label(name.replace("_", " ").title(), "Body")
        layout.addWidget(name_label, 0, 0)
        status = capability.status if capability else "not_assessed"
        score = capability.score if capability else None
        value_text = f"{score:.0f}" if score is not None else status.replace("_", " ")
        tone = (
            COLORS["green"] if status == "passed" else
            COLORS["amber"] if status in {"partial", "not_assessed"} else
            COLORS["red"] if status in {"failed", "unsupported"} else COLORS["faint"]
        )
        value = QLabel(value_text)
        value.setStyleSheet(f"color:{tone};font-size:11px;font-weight:600;")
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(value, 0, 1)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(score) if score is not None else 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        bar.setStyleSheet(
            f"QProgressBar{{background:{COLORS['border_soft']};border:0;border-radius:2px;}}"
            f"QProgressBar::chunk{{background:{tone};border-radius:2px;}}"
        )
        layout.addWidget(bar, 1, 0, 1, 2)
        if capability and capability.attempts:
            detail = label(
                f"{capability.passed}/{capability.attempts} trials · confidence "
                f"{capability.confidence:.2f}",
                "Faint",
            )
            layout.addWidget(detail, 2, 0, 1, 2)


class ModelCenterPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._all_models: list[str] = []
        self._selected = ""
        self._profile_worker: FunctionWorker | None = None
        self._benchmark_cancel: Any = None
        self._last_benchmark: Any = None
        self._build()
        self._connect()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 0)
        root.setSpacing(16)
        self.refresh_button = button("Refresh inventory", on_click=self.refresh_all)
        self.profile_button = button("Profile", variant="Primary", on_click=self.profile_selected)
        self.benchmark_button = button("Benchmark", on_click=self.benchmark_selected)
        self.cancel_button = button("Stop", variant="Danger", on_click=self.cancel_benchmark)
        self.cancel_button.setEnabled(False)
        self.compare_button = button("Compare models", variant="Ghost", on_click=self.open_comparison)
        root.addWidget(PageHeader(
            "INTELLIGENCE / EVIDENCE",
            "Model Intelligence",
            "Understand the models installed on this machine before you trust them with work.",
            actions=[self.refresh_button, self.profile_button, self.benchmark_button,
                     self.cancel_button, self.compare_button],
        ))

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._inventory_panel())
        split.addWidget(self._detail_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([315, 900])
        root.addWidget(split, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        root.addWidget(self.progress)

    def _inventory_panel(self) -> QWidget:
        panel = Card(margins=(14, 14, 14, 12))
        panel.setMinimumWidth(275)
        panel.setMaximumWidth(350)
        panel.body.addWidget(label("LOCAL ARTIFACTS", "Eyebrow"))
        panel.body.addWidget(label("Installed models", "SectionTitle"))
        from PySide6.QtWidgets import QLineEdit

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search model name or family…")
        self.search.setObjectName("Search")
        self.search.textChanged.connect(self._filter)
        panel.body.addWidget(self.search)
        from PySide6.QtWidgets import QComboBox

        self.family_filter = QComboBox()
        self.family_filter.addItem("All families", "")
        self.family_filter.currentIndexChanged.connect(self._filter)
        panel.body.addWidget(self.family_filter)
        self.model_list = QListWidget()
        self.model_list.currentItemChanged.connect(self._selection_changed)
        prepare_list(self.model_list, spacing=1, row_height=50)
        panel.body.addWidget(self.model_list, 1)
        self.inventory_meta = label("Waiting for Ollama inventory…", "Faint")
        panel.body.addWidget(self.inventory_meta)
        return panel

    def _detail_panel(self) -> QWidget:
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_content = QWidget()
        self.detail_content.setObjectName("PageBody")
        self.detail_layout = QVBoxLayout(self.detail_content)
        self.detail_layout.setContentsMargins(6, 0, 8, 26)
        self.detail_layout.setSpacing(13)
        self.detail_scroll.setWidget(self.detail_content)
        return self.detail_scroll

    def _connect(self) -> None:
        self.context.events.data_changed.connect(self._data_changed)
        self.context.events.theme_changed.connect(self._apply_motion)
        self._apply_motion()

    def _data_changed(self, kind: str) -> None:
        if kind in {"models", "profiles", "settings"}:
            self.refresh()

    def refresh_all(self) -> None:
        self.context.events.refresh_inventory.emit()
        self.refresh()

    def refresh(self) -> None:
        installed = self.context.models
        self._all_models = [item.name for item in installed]
        families = sorted({
            str((item.details or {}).get("family") or "unknown") for item in installed
        })
        current = self.family_filter.currentData() if hasattr(self, "family_filter") else ""
        self.family_filter.blockSignals(True)
        self.family_filter.clear()
        self.family_filter.addItem("All families", "")
        for family in families:
            if family and family != "unknown":
                self.family_filter.addItem(family, family)
        index = self.family_filter.findData(current)
        self.family_filter.setCurrentIndex(max(0, index))
        self.family_filter.blockSignals(False)
        self.inventory_meta.setText(
            f"{len(installed)} installed · {len(self.context.profiles)} profiled"
        )
        self._filter()
        if not installed:
            self.model_list.clear()
            self.model_list.addItem("No models discovered. Start Ollama and refresh.")
            self._render_empty(
                "No local models available",
                "Start Ollama at the configured endpoint, then refresh the inventory.",
            )
        elif self._selected and self._selected in self._all_models:
            self._select_name(self._selected)
        else:
            self._select_name(self._all_models[0])

    def _filter(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        family = str(self.family_filter.currentData() or "")
        previously = self._selected
        selected_item = None
        self.model_list.blockSignals(True)
        self.model_list.clear()
        for model in self.context.models:
            details = model.details or {}
            model_family = str(details.get("family") or "unknown")
            haystack = f"{model.name} {model_family} {details.get('parameter_size') or ''}".casefold()
            if query and query not in haystack:
                continue
            if family and model_family != family:
                continue
            profile = self.context.profile_for(model.name)
            marker = "◆" if profile else "◇"
            text = (
                f"{marker}  {model.name}\n"
                f"     {model_family} · {details.get('parameter_size') or '—'} · "
                f"{format_bytes(model.size_bytes)}"
            )
            item = two_line_item(text, height=50, data=model.name)
            item.setToolTip(
                f"Digest: {model.digest or 'unknown'}\n"
                f"Profiled: {'yes' if profile else 'no'}"
            )
            self.model_list.addItem(item)
            if model.name == previously:
                selected_item = item
        self.model_list.blockSignals(False)
        if selected_item is not None:
            self.model_list.setCurrentItem(selected_item)
        elif self.model_list.count():
            self.model_list.setCurrentRow(0)
        else:
            self._render_empty(
                "No matching models",
                "Clear the search or change the family filter to see local artifacts.",
            )

    def _selection_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is None:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if name:
            self._select_name(name)

    def _select_name(self, name: str) -> None:
        self._selected = name
        profile = self.context.profile_for(name)
        installed = self.context.installed(name)
        self.profile_button.setEnabled(bool(name))
        self.benchmark_button.setEnabled(bool(name) and installed is not None)
        self._render_model(name, installed, profile)
        for index in range(self.model_list.count()):
            item = self.model_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == name:
                self.model_list.blockSignals(True)
                self.model_list.setCurrentItem(item)
                self.model_list.blockSignals(False)
                break

    def _clear_detail(self) -> None:
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    @staticmethod
    def _clear_layout(layout: Any) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                ModelCenterPage._clear_layout(item.layout())

    def _render_empty(self, title: str, copy: str) -> None:
        self._clear_detail()
        self.detail_layout.addWidget(empty_panel(title, copy))
        self.detail_layout.addStretch(1)

    def _render_model(self, name: str, installed: Any, profile: ModelProfile | None) -> None:
        self._clear_detail()
        details = installed.details if installed is not None else {}
        title_row = QHBoxLayout()
        title_copy = QVBoxLayout()
        title_copy.setSpacing(2)
        title_copy.addWidget(label("MODEL DOSSIER", "Eyebrow"))
        title_copy.addWidget(label(name, "PageTitle"))
        subtitle = (
            f"{profile.professional_title} · {profile.primary_specialization}"
            if profile else "No evidence-backed profile stored for this artifact."
        )
        title_copy.addWidget(label(subtitle, "PageSubtitle", wrap=True))
        title_row.addLayout(title_copy, 1)
        if profile:
            title_row.addWidget(Pill("PROFILED", "green"))
        else:
            title_row.addWidget(Pill("UNPROFILED", "amber"))
        self.detail_layout.addLayout(title_row)

        meta_line = QHBoxLayout()
        meta_line.setSpacing(6)
        meta_line.addWidget(Pill(str(details.get("family") or "unknown"), "muted"))
        meta_line.addWidget(Pill(str(details.get("parameter_size") or "—"), "muted"))
        meta_line.addWidget(Pill(str(details.get("quantization_level") or "—"), "muted"))
        if profile:
            meta_line.addWidget(Pill(profile.agentability_status.replace("_", " "), "violet"))
        meta_line.addStretch(1)
        self.detail_layout.addLayout(meta_line)

        metrics = QGridLayout()
        metrics.setSpacing(9)
        values = [
            ("PARAMETERS", str(details.get("parameter_size") or "—"),
             str(details.get("family") or "unknown")),
            ("CONTEXT", f"{(profile.context_length if profile else None) or '—'}",
             "tokens declared"),
            ("ARTIFACT", format_bytes(installed.size_bytes if installed else 0),
             str((installed.digest if installed else "")[:12] or "no digest")),
            ("QUANTIZATION", str(details.get("quantization_level") or "—"),
             "local artifact"),
        ]
        for index, (heading, value, detail) in enumerate(values):
            tile = MetricTile(heading, value, detail, tone="cyan")
            metrics.addWidget(tile, 0, index)
        self.detail_layout.addLayout(metrics)

        if profile is None:
            action = button("Profile this model", variant="Primary",
                            on_click=self.profile_selected)
            self.detail_layout.addWidget(empty_panel(
                "This artifact has not been assessed.",
                "Profiling runs bounded local probes and records observed capabilities. "
                "Declared metadata alone is never treated as empirical evidence.",
                action,
            ))
        else:
            self.detail_layout.addWidget(self._capability_panel(profile))
            self.detail_layout.addWidget(self._identity_panel(profile))
        self.detail_layout.addWidget(self._benchmark_panel(name))
        self.detail_layout.addStretch(1)

    def _capability_panel(self, profile: ModelProfile) -> QWidget:
        card = Card(margins=(17, 14, 17, 13))
        card.body.addWidget(SectionHeader(
            "Capability evidence",
            "Observed scores are separate from declared metadata.",
            actions=[button("Profile again", variant="Ghost", on_click=self.profile_selected)],
        ))
        card.body.addWidget(divider())
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(10)
        capabilities = sorted(profile.capabilities, key=lambda item: item.capability)
        for index, capability in enumerate(capabilities):
            if capability.capability in {"instruction_following", "reasoning", "code_generation",
                                         "tool_calling", "structured_output", "vision"}:
                grid.addWidget(CapabilityBar(capability.capability, profile), index // 2, index % 2)
        card.body.addLayout(grid)
        evidence = QTextBrowser()
        evidence.setMinimumHeight(150)
        rationale = "".join(
            f"<li>{html.escape(item)}</li>" for item in profile.identity_rationale
        )
        limitations = "".join(
            f"<li>{html.escape(item)}</li>" for item in profile.known_limitations[:8]
        )
        evidence.setHtml(
            f"<div style='color:{COLORS['text_secondary']};font-size:12px;'>"
            f"<h3 style='color:{COLORS['ice']};font-size:13px;'>Why this identity?</h3>"
            f"<ul>{rationale or '<li>No identity rationale recorded.</li>'}</ul>"
            f"<h3 style='color:{COLORS['ice']};font-size:13px;'>Known limitations</h3>"
            f"<ul>{limitations or '<li>None recorded.</li>'}</ul></div>"
        )
        card.body.addWidget(evidence)
        return card

    def _identity_panel(self, profile: ModelProfile) -> QWidget:
        card = Card(margins=(17, 14, 17, 13))
        card.body.addWidget(SectionHeader(
            "Intelligence dossier",
            "Publisher evidence and operational envelope.",
        ))
        card.body.addWidget(divider())
        row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(label("DECLARED PURPOSE", "MetricLabel"))
        left.addWidget(label(profile.declared_purpose or "Not established", "Body", wrap=True))
        left.addSpacing(4)
        left.addWidget(label("RECOMMENDED ROLES", "MetricLabel"))
        left.addWidget(label(", ".join(profile.recommended_roles) or "Not established",
                          "Body", wrap=True))
        right = QVBoxLayout()
        right.addWidget(label("AGENTABILITY", "MetricLabel"))
        right.addWidget(label(
            f"{profile.agentability_mode} / {profile.agentability_status}",
            "Body", wrap=True,
        ))
        right.addSpacing(4)
        right.addWidget(label("IDENTITY CONFIDENCE", "MetricLabel"))
        right.addWidget(label(f"{profile.identity_confidence:.0%}", "Body"))
        row.addLayout(left, 3)
        row.addLayout(right, 2)
        card.body.addLayout(row)
        source = QTextBrowser()
        source.setMinimumHeight(90)
        evidence_html = "".join(
            f"<li><span style='color:{COLORS['cyan']};'>[{html.escape(item.level)}]</span> "
            f"<b>{html.escape(item.claim)}</b>: {html.escape(item.value)}"
            + (f" <a style='color:{COLORS['cyan']};' href='{html.escape(item.source_url, quote=True)}'>source</a>"
               if item.source_url else "")
            + "</li>"
            for item in profile.origin_evidence[:12]
        )
        source.setHtml(
            f"<div style='color:{COLORS['text_secondary']};font-size:11px;'>"
            f"<ul>{evidence_html}</ul></div>"
        )
        card.body.addWidget(source)
        return card

    def _benchmark_panel(self, name: str) -> QWidget:
        card = Card(margins=(17, 14, 17, 13))
        card.body.addWidget(SectionHeader(
            "Local benchmark history",
            "Deterministic core suite. Diagnostic, not certification.",
            actions=[button("Run core benchmark", variant="Ghost", on_click=self.benchmark_selected)],
        ))
        card.body.addWidget(divider())
        records = self._benchmark_records(name)
        if not records:
            card.body.addWidget(label(
                "No saved benchmark report for this model. Run the local core suite to create "
                "evidence with a pinned manifest and host-side scoring.",
                "Muted", wrap=True,
            ))
            return card
        scores = [float(item.get("score") or 0.0) for item in records]
        chart = Sparkline(tone="cyan")
        chart.set_values(scores)
        chart.setFixedHeight(54)
        card.body.addWidget(chart)
        for record in records[-4:][::-1]:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(Pill(f"{float(record.get('score') or 0):.0f}/100",
                               "green" if float(record.get("score") or 0) >= 60 else "amber"))
            row.addWidget(label(
                f"{record.get('total_passed', '—')}/{record.get('total_attempts', '—')} trials · "
                f"{format_timestamp(record.get('completed_at'))} · "
                f"{record.get('suite_id', 'core')}@{record.get('suite_version', '—')}",
                "Muted", wrap=True,
            ), 1)
            card.body.addLayout(row)
        return card

    def _benchmark_records(self, name: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        directory = self.context.paths.profiles_dir / "benchmarks"
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("model_name") == name:
                payload["_path"] = str(path)
                records.append(payload)
        return sorted(records, key=lambda item: str(item.get("completed_at") or ""))

    def profile_selected(self) -> None:
        if not self._selected:
            return
        name = self._selected
        self.profile_button.setEnabled(False)
        self.progress.show()
        self.context.set_state("thinking", f"Profiling {name}")
        repeats = 1
        worker = FunctionWorker(
            self.context.profiler.profile,
            name,
            True,
            lambda message: worker.signals.progress.emit(message),
            repeats,
        )
        self._profile_worker = worker
        worker.signals.progress.connect(lambda message: self.context.notify(message))
        worker.signals.result.connect(self._profile_ready)
        worker.signals.error.connect(self._operation_failed)
        worker.signals.finished.connect(self._profile_finished)
        self.context.pool.start(worker)

    def _profile_ready(self, profile: ModelProfile) -> None:
        self.context.repository.save_profile(profile)
        self.context.reload_profiles()
        self.context.events.data_changed.emit("profiles")
        self.render_selected()
        self.context.set_state("complete", f"Profile saved for {profile.model_name}")
        self.context.notify(f"Profiled {profile.model_name}: {profile.professional_title}")

    def _profile_finished(self) -> None:
        self.progress.hide()
        self.profile_button.setEnabled(bool(self._selected))
        self._profile_worker = None
        if self.context.settings.reduced_motion:
            return
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2400, lambda: self.context.set_state("standby", "Local intelligence ready"))

    def benchmark_selected(self) -> None:
        if not self._selected:
            return
        installed = self.context.installed(self._selected)
        if installed is None:
            self.context.notify("Select an installed model before running a benchmark.")
            return
        if self._benchmark_cancel is not None:
            return
        from threading import Event

        cancel = Event()
        self._benchmark_cancel = cancel
        self.cancel_button.setEnabled(True)
        self.benchmark_button.setEnabled(False)
        self.progress.show()
        self.context.set_state("executing", f"Benchmarking {self._selected}")
        runner = BenchmarkRunner(self.context.client, keep_alive=self.context.settings.keep_alive)
        worker = FunctionWorker(
            runner.run,
            self._selected,
            model_digest=installed.digest,
            suite_id="core",
            repeats=3,
            cancel_event=cancel,
            progress=lambda message: worker.signals.progress.emit(message),
        )
        worker.signals.progress.connect(self.context.notify)
        worker.signals.result.connect(self._benchmark_ready)
        worker.signals.error.connect(self._benchmark_failed)
        worker.signals.finished.connect(self._benchmark_finished)
        self.context.pool.start(worker)

    def _benchmark_ready(self, report: Any) -> None:
        self._last_benchmark = report
        destination = (
            self.context.paths.profiles_dir / "benchmarks"
            / f"{report.model_name.replace('/', '_').replace(':', '_')}-{report.run_id[:8]}.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.context.set_state("complete", f"Benchmark completed for {report.model_name}")
        self.context.notify(f"Benchmark saved · {report.score:.0f}/100 · {destination.name}")
        self.render_selected()

    def _benchmark_failed(self, message: str) -> None:
        if self._benchmark_cancel is not None and self._benchmark_cancel.is_set():
            self.context.set_state("standby", "Benchmark cancelled")
            self.context.notify("Benchmark cancelled; partial results were not certified.")
            return
        self._operation_failed(message)

    def _benchmark_finished(self) -> None:
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        self.benchmark_button.setEnabled(bool(self._selected))
        self._benchmark_cancel = None
        from PySide6.QtCore import QTimer

        QTimer.singleShot(2600, lambda: self.context.set_state("standby", "Local intelligence ready"))

    def cancel_benchmark(self) -> None:
        if self._benchmark_cancel is not None:
            self._benchmark_cancel.set()
            self.cancel_button.setEnabled(False)
            self.context.notify("Benchmark stop requested; cancelling the active stream.")

    def _operation_failed(self, message: str) -> None:
        self.context.set_state("warning", "Model intelligence operation failed")
        self.context.notify(message.splitlines()[0][:220])
        QMessageBox.critical(self, "Model operation failed", message[:2400])

    def render_selected(self) -> None:
        if self._selected:
            self._select_name(self._selected)

    def open_comparison(self) -> None:
        if not self._all_models:
            self.context.notify("No models available to compare.")
            return
        dialog = ModelComparisonDialog(self.context, self)
        dialog.exec()

    def research_selected(self) -> None:
        """Preserve optional publisher research without changing empirical evidence."""
        if not self.context.settings.enable_remote_model_research:
            QMessageBox.information(
                self,
                "Remote research disabled",
                "Enable public model-card research in Settings before searching Hugging Face.",
            )
            return
        if not self._selected:
            return
        model_name = self._selected
        self.context.set_state("thinking", f"Searching publisher evidence for {model_name}")
        researcher = HuggingFaceResearcher()
        worker = FunctionWorker(researcher.search_candidates, model_name)
        worker.signals.result.connect(lambda candidates: self._research_candidates(model_name, candidates))
        worker.signals.error.connect(self._operation_failed)
        self.context.pool.start(worker)

    def _research_candidates(self, model_name: str, candidates: list[Any]) -> None:
        if not candidates:
            self.context.notify("No public model-card candidates found.")
            return
        labels = [
            f"{candidate.repository_id} · {candidate.pipeline_tag or 'task undisclosed'} · "
            f"name match {candidate.match_score:.0%}"
            for candidate in candidates
        ]
        selected, accepted = QInputDialog.getItem(
            self, "Confirm upstream model identity",
            f"Select the exact publisher repository for {model_name}. Candidates are hints, not proof:",
            labels, 0, False,
        )
        if not accepted:
            return
        candidate = candidates[labels.index(selected)]
        self.context.set_state("thinking", "Retrieving publisher dossier")
        worker = FunctionWorker(HuggingFaceResearcher().fetch_model_card, candidate.repository_id)
        worker.signals.result.connect(lambda card: self._attach_card(model_name, card))
        worker.signals.error.connect(self._operation_failed)
        self.context.pool.start(worker)

    def _attach_card(self, model_name: str, card: dict[str, Any]) -> None:
        profile = self.context.profile_for(model_name)
        if profile is None:
            QMessageBox.warning(self, "Profile required", "Profile this model before attaching evidence.")
            return
        attach_model_card_evidence(profile, card)
        self.context.repository.save_profile(profile)
        self.context.reload_profiles()
        self.context.events.data_changed.emit("profiles")
        safe = model_name.replace("/", "_").replace(":", "_")
        markdown = str(card.get("model_card_markdown") or "")
        (self.context.paths.profiles_dir / f"{safe}.model-card.md").write_text(markdown, encoding="utf-8")
        self.context.set_state("complete", "Publisher evidence attached")
        self.context.notify(f"Publisher dossier attached for {model_name}.")
        self.render_selected()

    def _apply_motion(self) -> None:
        return


class ModelComparisonDialog(QDialog):
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("Model comparison")
        self.resize(900, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        root.addWidget(label("MODEL INTELLIGENCE", "Eyebrow"))
        root.addWidget(label("Compare installed intelligence", "PageTitle"))
        root.addWidget(label(
            "Only installed artifacts are compared. A blank empirical field means the model "
            "has not been profiled; declared metadata is labelled separately.",
            "PageSubtitle", wrap=True,
        ))
        checks = QWidget()
        check_layout = QGridLayout(checks)
        check_layout.setSpacing(7)
        self._checks: dict[str, QCheckBox] = {}
        for index, model in enumerate(context.models):
            box = QCheckBox(model.name)
            if context.profile_for(model.name) is None:
                box.setToolTip("No saved empirical profile; metadata comparison only.")
            self._checks[model.name] = box
            check_layout.addWidget(box, index // 3, index % 3)
        root.addWidget(checks)
        controls = QHBoxLayout()
        controls.addStretch(1)
        build = button("Compare selected", variant="Primary", on_click=self._build)
        controls.addWidget(build)
        root.addLayout(controls)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Model", "Parameters", "Artifact", "Context", "Core score", "Observed capabilities",
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self.table, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        root.addWidget(close)
        if len(self._checks) >= 2:
            first_two = list(self._checks.values())[:2]
            for box in first_two:
                box.setChecked(True)
            self._build()

    def _build(self) -> None:
        selected = [name for name, box in self._checks.items() if box.isChecked()]
        self.table.setRowCount(len(selected))
        for row, name in enumerate(selected):
            installed = self.context.installed(name)
            profile = self.context.profile_for(name)
            reports = self._reports_for(name)
            score = f"{reports[-1].get('score', 0):.0f}/100" if reports else "—"
            observed = []
            if profile:
                observed = [
                    item.capability.replace("_", " ")
                    for item in profile.capabilities
                    if item.status in {"passed", "partial"}
                ]
            values = [
                name,
                str((installed.details if installed else {}).get("parameter_size") or "—"),
                format_bytes(installed.size_bytes if installed else 0),
                f"{profile.context_length} tokens" if profile and profile.context_length else "—",
                score,
                ", ".join(observed[:5]) or "not profiled",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setToolTip(f"Digest: {(installed.digest if installed else 'unknown')[:24]}")
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

    def _reports_for(self, name: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted((self.context.paths.profiles_dir / "benchmarks").glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("model_name") == name:
                records.append(payload)
        return records
