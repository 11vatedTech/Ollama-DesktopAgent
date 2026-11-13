"""Command Center: the live operational overview of the local intelligence system."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.recovery import RecoveryStatus, load_recovery_candidates
from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    Pill,
    RingGauge,
    SectionHeader,
    StatusDot,
    button,
    divider,
    label,
)
from ollama_workstation.ui.intelligence_core import IntelligenceCore
from ollama_workstation.ui.pages.common import (
    format_bytes,
    format_timestamp,
    make_scroll,
    prepare_list,
    two_line_item,
)
from ollama_workstation.ui.state import AppContext, EventBus


class CommandCenterPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._runs: list[dict[str, Any]] = []
        self._workforce: list[dict[str, Any]] = []
        self._attention: list[Any] = []
        self._build()
        self._connect()
        self.refresh()
        self._apply_responsive_layout()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 0)
        root.setSpacing(16)
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        action_layout.addWidget(button("New conversation", variant="Ghost",
                                       on_click=lambda: self.context.navigate("chat")))
        action_layout.addWidget(button("Refresh", variant="Secondary", on_click=self.refresh_all))
        root.addWidget(PageHeader(
            "11VATED / OPERATIONS",
            "Command Center",
            "One live surface for local models, agents, telemetry and coordinated work.",
            actions=[actions],
        ))

        content = QWidget()
        content.setObjectName("PageBody")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 4, 28)
        body.setSpacing(16)
        body.addWidget(self._hero())
        body.addLayout(self._telemetry_grid())
        body.addLayout(self._lower_grid())
        scroll = make_scroll(content)
        root.addWidget(scroll, 1)

    def _hero(self) -> QWidget:
        hero = QWidget()
        hero.setObjectName("Hero")
        hero.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hero_grid = QGridLayout(hero)
        self.hero_grid.setContentsMargins(28, 22, 30, 22)
        self.hero_grid.setSpacing(26)
        self.hero_grid.setColumnStretch(1, 1)

        self.core = IntelligenceCore(caption=True)
        self.core.setMinimumSize(300, 300)
        self.core.setMaximumWidth(390)

        copy_widget = QWidget()
        copy = QVBoxLayout(copy_widget)
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(7)
        self.online_pill = Pill("Checking local runtime", "muted")
        top.addWidget(self.online_pill)
        top.addWidget(Pill("LOCAL-FIRST", "violet"))
        top.addStretch(1)
        copy.addLayout(top)
        self.hero_title = label("Your intelligence environment, online.", "HeroTitle", wrap=True)
        copy.addWidget(self.hero_title)
        self.hero_copy = label(
            "Ollama is the inference floor. Model intelligence, specialist agents and every "
            "execution trace stay on this machine.",
            "HeroCopy",
            wrap=True,
        )
        self.hero_copy.setMinimumWidth(0)
        self.hero_copy.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        copy_widget.setMinimumWidth(0)
        copy.addWidget(self.hero_copy)
        copy.addSpacing(6)

        self.stat_models = self._hero_stat("MODELS", "—", "installed locally")
        self.stat_gpu = self._hero_stat("GPU", "—", "runtime headroom")
        self.stat_active = self._hero_stat("ACTIVE", "0", "operations in flight")
        self.stat_attention = self._hero_stat("ATTENTION", "0", "items requiring review")
        self.stats_widget = QWidget()
        self.stats_grid = QGridLayout(self.stats_widget)
        self.stats_grid.setContentsMargins(0, 0, 0, 0)
        self.stats_grid.setHorizontalSpacing(22)
        self.stats_grid.setVerticalSpacing(7)
        self.stat_widgets = [
            self.stat_models, self.stat_gpu, self.stat_active, self.stat_attention,
        ]
        copy.addWidget(self.stats_widget)
        copy.addSpacing(6)

        self.quick_widget = QWidget()
        self.quick_grid = QGridLayout(self.quick_widget)
        self.quick_grid.setContentsMargins(0, 0, 0, 0)
        self.quick_grid.setHorizontalSpacing(8)
        self.quick_grid.setVerticalSpacing(7)
        self.quick_buttons = [
            button("Start conversation", variant="Primary",
                   on_click=lambda: self.context.navigate("chat")),
            button("Profile a model", on_click=lambda: self.context.navigate("models")),
            button("Compose workforce", on_click=lambda: self.context.navigate("workforce")),
        ]
        copy.addWidget(self.quick_widget)
        self.hero_copy_widget = copy_widget
        self.hero_grid.addWidget(self.core, 0, 0, Qt.AlignmentFlag.AlignVCenter)
        self.hero_grid.addWidget(copy_widget, 0, 1)
        self._hero_narrow: bool | None = None
        self._layout_stats(4)
        self._layout_quick(3)
        return hero

    @staticmethod
    def _hero_stat(heading: str, value: str, detail: str) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        heading_label = label(heading, "MetricLabel")
        value_label = label(value, "MetricValueSmall")
        detail_label = label(detail, "Faint")
        lay.addWidget(heading_label)
        lay.addWidget(value_label)
        lay.addWidget(detail_label)
        widget._value = value_label  # type: ignore[attr-defined]
        widget._detail = detail_label  # type: ignore[attr-defined]
        return widget

    @staticmethod
    def _set_hero_stat(widget: QWidget, value: str, detail: str | None = None) -> None:
        widget._value.setText(value)  # type: ignore[attr-defined]
        if detail is not None:
            widget._detail.setText(detail)  # type: ignore[attr-defined]

    def _layout_stats(self, columns: int) -> None:
        for widget in self.stat_widgets:
            self.stats_grid.removeWidget(widget)
        for index in range(4):
            self.stats_grid.setColumnStretch(index, 0)
        for index, widget in enumerate(self.stat_widgets):
            self.stats_grid.addWidget(widget, index // columns, index % columns)
        for index in range(columns):
            self.stats_grid.setColumnStretch(index, 1)

    def _layout_quick(self, columns: int) -> None:
        for widget in self.quick_buttons:
            self.quick_grid.removeWidget(widget)
        for index in range(3):
            self.quick_grid.setColumnStretch(index, 0)
        for index, widget in enumerate(self.quick_buttons):
            self.quick_grid.addWidget(widget, index // columns, index % columns)
        for index in range(columns):
            self.quick_grid.setColumnStretch(index, 1)

    def _telemetry_grid(self) -> QGridLayout:
        self.telemetry_grid = QGridLayout()
        self.telemetry_grid.setSpacing(12)
        self.gpu_gauge = RingGauge("GPU LOAD", tone="cyan")
        self.vram_gauge = RingGauge("VRAM", tone="violet")
        self.ram_gauge = RingGauge("MEMORY", tone="blue")
        self.cpu_gauge = RingGauge("CPU", tone="green")
        self.disk_gauge = RingGauge("DISK", tone="amber")
        self.telemetry_cards: list[QWidget] = []
        for widget in (
            self.gpu_gauge, self.vram_gauge, self.ram_gauge, self.cpu_gauge, self.disk_gauge
        ):
            card = Card(margins=(12, 9, 12, 8))
            card.body.addWidget(widget)
            self.telemetry_cards.append(card)
        self._telemetry_columns = 0
        self._layout_telemetry(5)
        return self.telemetry_grid

    def _layout_telemetry(self, columns: int) -> None:
        if columns == self._telemetry_columns:
            return
        for card in self.telemetry_cards:
            self.telemetry_grid.removeWidget(card)
        for index in range(5):
            self.telemetry_grid.setColumnStretch(index, 0)
        for index, card in enumerate(self.telemetry_cards):
            self.telemetry_grid.addWidget(card, index // columns, index % columns)
        for index in range(columns):
            self.telemetry_grid.setColumnStretch(index, 1)
        self._telemetry_columns = columns

    def _lower_grid(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(16)
        self.lower_grid = grid
        self.lower_cards: list[QWidget] = []

        activity = Card(margins=(17, 15, 17, 13))
        header = SectionHeader(
            "Active operations",
            "The same activity state used by Agent Operations.",
            actions=[button("Open operations", variant="Ghost",
                            on_click=lambda: self.context.navigate("operations"))],
        )
        activity.body.addWidget(header)
        activity.body.addWidget(divider())
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(154)
        self.activity_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        prepare_list(self.activity_list, row_height=50)
        activity.body.addWidget(self.activity_list, 1)
        self.lower_cards.append(activity)

        runs = Card(margins=(17, 15, 17, 13))
        runs.body.addWidget(SectionHeader(
            "Recent execution",
            "Durable journals and workforce runs.",
            actions=[button("All history", variant="Ghost",
                            on_click=lambda: self.context.navigate("history"))],
        ))
        runs.body.addWidget(divider())
        self.runs_list = QListWidget()
        self.runs_list.setMinimumHeight(154)
        self.runs_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        prepare_list(self.runs_list, row_height=50)
        self.runs_list.itemClicked.connect(lambda _item: self.context.navigate("history"))
        runs.body.addWidget(self.runs_list, 1)
        self.lower_cards.append(runs)

        attention = Card(margins=(17, 15, 17, 13))
        attention.body.addWidget(SectionHeader(
            "Needs attention",
            "Checkpoint and workflow conditions.",
            actions=[button("Recovery", variant="Ghost",
                            on_click=lambda: self.context.navigate("history"))],
        ))
        attention.body.addWidget(divider())
        self.attention_list = QListWidget()
        self.attention_list.setMinimumHeight(154)
        self.attention_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        prepare_list(self.attention_list, row_height=50)
        self.attention_list.itemClicked.connect(lambda _item: self.context.navigate("history"))
        attention.body.addWidget(self.attention_list, 1)
        self.lower_cards.append(attention)
        self._lower_columns = 0
        self._layout_lower(3)
        return grid

    def _layout_lower(self, columns: int) -> None:
        if columns == self._lower_columns:
            return
        for card in self.lower_cards:
            self.lower_grid.removeWidget(card)
        for index in range(3):
            self.lower_grid.setColumnStretch(index, 0)
        for index, card in enumerate(self.lower_cards):
            if columns == 2 and index == 2:
                self.lower_grid.addWidget(card, 1, 0, 1, 2)
            else:
                self.lower_grid.addWidget(card, index // columns, index % columns)
        for index in range(columns):
            self.lower_grid.setColumnStretch(index, 1)
        self._lower_columns = columns

    def _apply_responsive_layout(self) -> None:
        if not hasattr(self, "hero_grid"):
            return
        narrow = self.width() < 1050
        if narrow != self._hero_narrow:
            self.hero_grid.removeWidget(self.core)
            self.hero_grid.removeWidget(self.hero_copy_widget)
            for index in range(3):
                self.hero_grid.setColumnStretch(index, 0)
            if narrow:
                # Put the human-readable brief before the large core at compact
                # widths so the primary action is not pushed below the fold.
                self.hero_grid.addWidget(self.hero_copy_widget, 0, 0)
                self.hero_grid.addWidget(
                    self.core, 1, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                )
                self.hero_grid.setColumnStretch(0, 1)
                self.core.setMinimumSize(220, 220)
                self.core.setMaximumSize(300, 300)
            else:
                self.hero_grid.addWidget(self.core, 0, 0, Qt.AlignmentFlag.AlignVCenter)
                self.hero_grid.addWidget(self.hero_copy_widget, 0, 1)
                self.hero_grid.setColumnStretch(1, 1)
                self.core.setMinimumSize(300, 300)
                self.core.setMaximumWidth(390)
                self.core.setMaximumHeight(16777215)
            self._hero_narrow = narrow
        # The compact page must reflow its action rows on every resize, not just
        # when the breakpoint changes. Otherwise a width captured at 1120 px
        # remains fixed when the window later reaches 900 px.
        available = max(280, self.width() - 150)
        self.hero_copy_widget.setMaximumWidth(available if narrow else 16777215)
        self.hero_copy_widget.setSizePolicy(
            QSizePolicy.Policy.Ignored if narrow else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )
        self.hero_title.setMaximumWidth(available if narrow else 16777215)
        self.hero_copy.setMaximumWidth(available if narrow else 16777215)
        self.hero_copy.setMinimumWidth(0)
        self._layout_stats(2 if narrow else 4)
        self._layout_quick(2 if narrow else 3)
        self._layout_telemetry(3 if narrow else 5)
        self._layout_lower(2 if narrow else 3)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _connect(self) -> None:
        events: EventBus = self.context.events
        events.telemetry.connect(self.apply_telemetry)
        events.data_changed.connect(self._data_changed)
        events.core_state.connect(self.core.set_state)
        events.theme_changed.connect(self._apply_motion)
        self.context.activity.changed.connect(self._refresh_activity)
        self._apply_motion()

    def _apply_motion(self) -> None:
        self.core.set_motion(
            reduced=self.context.settings.reduced_motion,
            intensity=self.context.settings.animation_intensity,
        )

    def _data_changed(self, kind: str) -> None:
        if kind in {"runs", "workforce", "models", "profiles", "settings", "telemetry"}:
            self.refresh()

    def refresh_all(self) -> None:
        self.refresh()
        self.context.events.refresh_inventory.emit()

    def refresh(self) -> None:
        self._refresh_activity()
        self._refresh_runs()
        self._refresh_attention()
        if self.context.telemetry:
            self.apply_telemetry(self.context.telemetry)
        online = self.context.ollama_online
        self.online_pill.setText("LOCAL RUNTIME ONLINE" if online else "LOCAL RUNTIME OFFLINE")
        self.online_pill.set_tone("green" if online else "red")
        count = len(self.context.models)
        profile_count = len(self.context.profiles)
        empirical_count = sum(
            1 for profile in self.context.profiles
            if any(item.status in {"passed", "partial", "failed"}
                   for item in profile.capabilities)
        )
        self._set_hero_stat(
            self.stat_models, str(count), f"{profile_count} metadata profiles"
        )
        gpus = self.context.telemetry.get("nvidia_gpus") or []
        if gpus:
            gpu = gpus[0]
            self._set_hero_stat(
                self.stat_gpu,
                self._adapter_name(gpu),
                f"{gpu.get('memory_free_mib') or '—'} MiB free",
            )
        self.hero_copy.setText(
            f"{count} local models discovered. {profile_count} metadata profiles; "
            f"{empirical_count} with empirical capability evidence. "
            "Every action below is executed on this machine."
            if online else
            "Ollama is not answering at the configured endpoint. Local records remain available; "
            "start Ollama to resume inference."
        )

    def _refresh_activity(self) -> None:
        operations = self.context.activity.active()
        self.activity_list.clear()
        self._set_hero_stat(self.stat_active, str(len(operations)),
                            "operations in flight" if operations else "system is quiet")
        if not operations:
            item = QListWidgetItem("Intelligence core is standing by. No active work.")
            item.setForeground(Qt.GlobalColor.gray)
            self.activity_list.addItem(item)
            return
        for operation in operations:
            status = operation.detail or "in progress"
            prefix = operation.kind.upper()
            item = two_line_item(
                f"{prefix}  ·  {operation.title}\n{status}",
                data=operation.operation_id,
            )
            self.activity_list.addItem(item)

    def _refresh_runs(self) -> None:
        try:
            self._runs = self.context.repository.list_runs(4)
            self._workforce = self.context.repository.list_workforce_runs(3)
        except Exception as exc:
            self.runs_list.clear()
            self.runs_list.addItem(f"Unable to read execution journal: {exc}")
            return
        self.runs_list.clear()
        rows: list[tuple[str, str]] = []
        for run in self._workforce:
            rows.append((
                "WORKFORCE",
                f"{str(run.get('status') or 'unknown').upper()}  ·  "
                f"{format_timestamp(run.get('created_at'), short=True)}  ·  "
                f"{self._summary(run.get('objective'), 52)}",
            ))
        for run in self._runs:
            rows.append((
                str(run.get("status") or "unknown").upper(),
                f"{run.get('agent_title') or 'Agent'}  ·  "
                f"{format_timestamp(run.get('created_at'), short=True)}  ·  "
                f"{self._summary(run.get('task'), 48)}",
            ))
        for prefix, text in rows[:6]:
            item = two_line_item(f"{prefix}\n{text}", height=50)
            item.setForeground(
                Qt.GlobalColor.gray if prefix.upper() in {"CANCELLED", "STALE"} else
                Qt.GlobalColor.white
            )
            self.runs_list.addItem(item)
        if not rows:
            self.runs_list.addItem("No executions yet. Start a conversation or workforce run.")

    def _refresh_attention(self) -> None:
        try:
            candidates = load_recovery_candidates(self.context.repository)
        except Exception:
            candidates = []
        self._attention = [
            item for item in candidates
            if item.status in {
                RecoveryStatus.NEEDS_REVIEW, RecoveryStatus.CORRUPT,
                RecoveryStatus.EXHAUSTED, RecoveryStatus.RESUMABLE,
                RecoveryStatus.LEASED, RecoveryStatus.WORKSPACE_BUSY,
            }
        ]
        self.attention_list.clear()
        self._set_hero_stat(self.stat_attention, str(len(self._attention)),
                            "items requiring review" if self._attention else "nothing pending")
        for item in self._attention[:5]:
            marker = (
                "LEASED" if item.status is RecoveryStatus.LEASED else
                "WORKSPACE BUSY" if item.status is RecoveryStatus.WORKSPACE_BUSY else
                "REVIEW" if item.status is not RecoveryStatus.RESUMABLE else "RESUMABLE"
            )
            row = two_line_item(
                f"{marker}  ·  {item.agent_title or 'Agent'}\n"
                f"step {item.step}/{item.step_budget or '?'}  ·  {self._summary(item.task, 38)}"
            )
            row.setForeground(Qt.GlobalColor.yellow if marker == "REVIEW" else Qt.GlobalColor.cyan)
            self.attention_list.addItem(row)
        if not self._attention:
            self.attention_list.addItem("No checkpoint needs a human decision.")

    @staticmethod
    def _adapter_name(gpu: dict[str, Any]) -> str:
        name = str(gpu.get("name") or "No NVIDIA adapter").replace("NVIDIA ", "")
        for suffix in (" Laptop GPU", " GPU"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name

    def apply_telemetry(self, payload: dict[str, Any]) -> None:
        gpus = payload.get("nvidia_gpus") or []
        gpu = gpus[0] if gpus else {}
        adapter = self._adapter_name(gpu)
        utilization = gpu.get("utilization_percent")
        self.gpu_gauge.set_value(
            float(utilization) if utilization is not None else None,
            detail=adapter,
        )
        total_vram = gpu.get("memory_total_mib")
        used_vram = gpu.get("memory_used_mib")
        vram_percent = (
            used_vram / total_vram * 100 if total_vram and used_vram is not None else None
        )
        self.vram_gauge.set_value(
            vram_percent,
            detail=(
                f"{used_vram / 1024:.1f} / {total_vram / 1024:.1f} GB"
                if total_vram else "unavailable"
            ),
        )
        used = gpu.get("memory_used_mib")
        self._set_hero_stat(
            self.stat_gpu,
            adapter,
            f"{int(used):,} MiB used" if used is not None else "telemetry unavailable",
        )
        memory = payload.get("system_memory") or {}
        total = memory.get("total_bytes")
        used_memory = memory.get("used_bytes")
        self.ram_gauge.set_value(
            used_memory / total * 100 if total and used_memory is not None else None,
            detail=(
                f"{format_bytes(used_memory)} / {format_bytes(total)}"
                if total else "unavailable"
            ),
        )
        cpu = payload.get("cpu_percent")
        self.cpu_gauge.set_value(
            float(cpu) if cpu is not None else None,
            detail=f"{payload.get('logical_cpu_count') or '—'} logical processors",
        )
        disk = payload.get("disk") or {}
        self.disk_gauge.set_value(
            float(disk["percent"]) if disk.get("percent") is not None else None,
            detail=f"{format_bytes(disk.get('free_bytes'))} free" if disk else "unavailable",
        )

    @staticmethod
    def _summary(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class AttentionRow(QWidget):
    """Reserved for future rich attention rows; kept simple to avoid stale data."""

    def __init__(self, title: str, detail: str, tone: str = "amber", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(8)
        layout.addWidget(StatusDot(tone, 6))
        copy = QVBoxLayout()
        copy.setSpacing(0)
        copy.addWidget(label(title, "Body"))
        copy.addWidget(label(detail, "Faint", wrap=True))
        layout.addLayout(copy, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
