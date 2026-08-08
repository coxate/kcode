from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from kcode.memory.models import (
    DecisionKind,
    MemoryDecision,
    MemoryProposal,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
)


class MemoryReviewScreen(ModalScreen[MemoryDecision | None]):
    BINDINGS = [
        Binding("escape", "later", "Later", show=False),
        Binding("1", "approve", "Approve", show=False),
        Binding("2", "edit", "Edit and approve", show=False),
        Binding("3", "reject", "Reject", show=False),
    ]
    CSS = """
    MemoryReviewScreen { align: center middle; background: $background 60%; }
    #memory-review-dialog {
        width: 88; height: auto; max-height: 90%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #memory-review-dialog Input { margin-bottom: 1; }
    #memory-review-actions { height: auto; }
    #memory-review-actions Button { width: 1fr; }
    """

    def __init__(
        self,
        proposal: MemoryProposal,
        targets: tuple[MemoryRecord, ...] = (),
    ) -> None:
        super().__init__()
        self.proposal = proposal
        self.targets = targets

    def compose(self) -> ComposeResult:
        target_text = ""
        if self.targets:
            target_text = "\n\nExisting:\n" + "\n".join(
                f"- {record.title}: {record.summary}" for record in self.targets
            )
        with Vertical(id="memory-review-dialog"):
            yield Label(
                f"长期记忆候选 · {self.proposal.action.value} · "
                f"{self.proposal.scope.value}/{self.proposal.type.value}"
            )
            yield Static(
                f"Reason: {self.proposal.reason}\nEvidence: {self.proposal.evidence}"
                f"{target_text}",
                markup=False,
            )
            yield Input(value=self.proposal.title, id="memory-title", placeholder="Title")
            yield Input(value=self.proposal.summary, id="memory-summary", placeholder="Summary")
            yield Input(
                value=self.proposal.application,
                id="memory-application",
                placeholder="How to apply",
            )
            with Horizontal(id="memory-review-actions"):
                yield Button("1. 确认", id="approve", variant="success")
                yield Button("2. 编辑后确认", id="edit")
                yield Button("3. 拒绝", id="reject", variant="error")

    def on_mount(self) -> None:
        self.query_one("#approve", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.action_approve()
        elif event.button.id == "edit":
            self.action_edit()
        else:
            self.action_reject()

    def action_approve(self) -> None:
        self.dismiss(MemoryDecision(proposal_id=self.proposal.id, kind=DecisionKind.APPROVE))

    def action_edit(self) -> None:
        values = (
            self.query_one("#memory-title", Input).value.strip(),
            self.query_one("#memory-summary", Input).value.strip(),
            self.query_one("#memory-application", Input).value.strip(),
        )
        if all(values):
            self.dismiss(
                MemoryDecision(
                    proposal_id=self.proposal.id,
                    kind=DecisionKind.EDIT,
                    title=values[0],
                    summary=values[1],
                    application=values[2],
                )
            )

    def action_reject(self) -> None:
        self.dismiss(MemoryDecision(proposal_id=self.proposal.id, kind=DecisionKind.REJECT))

    def action_later(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True, slots=True)
class MemoryPanelAction:
    kind: str
    scope: MemoryScope | None = None
    item_id: str | None = None


class MemoryScreen(ModalScreen[MemoryPanelAction | None]):
    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("enter", "choose", "Review", show=False),
        Binding("i", "toggle", "Activate / Inactivate", show=False),
        Binding("e", "edit", "Edit", show=False),
        Binding("d", "delete", "Permanently delete", show=False),
    ]
    CSS = """
    MemoryScreen { align: center middle; background: $background 60%; }
    #memory-dialog {
        width: 96; height: 82%; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #memory-warning { height: auto; max-height: 5; color: $warning; }
    #memory-options { height: 1fr; margin-top: 1; }
    """

    def __init__(
        self,
        records: tuple[MemoryRecord, ...],
        proposals: tuple[MemoryProposal, ...],
        warnings: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.records = records
        self.proposals = proposals
        self.warnings = warnings

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-dialog"):
            yield Label("长期记忆 · Enter 审核 · E 编辑 · I 失效/恢复 · D 永久删除")
            yield Static("\n".join(self.warnings[-3:]), id="memory-warning", markup=False)
            yield OptionList(*self._options(), id="memory-options")

    def on_mount(self) -> None:
        options = self.query_one("#memory-options", OptionList)
        if options.option_count:
            options.highlighted = 0
            options.focus()

    def _options(self) -> list[Option]:
        options: list[Option] = []
        for proposal in self.proposals:
            options.append(
                Option(
                    f"[待审核] {proposal.action.value} · {proposal.scope.value} · "
                    f"{proposal.title}\n  {proposal.summary}",
                    id=f"proposal:{proposal.id}",
                )
            )
        for record in sorted(
            self.records,
            key=lambda item: (item.status != MemoryStatus.ACTIVE, item.scope.value, item.title),
        ):
            options.append(
                Option(
                    f"[{record.status.value}] {record.scope.value}/{record.type.value} · "
                    f"{record.title}\n  {record.summary}",
                    id=f"record:{record.scope.value}:{record.id}",
                )
            )
        if not options:
            options.append(Option("还没有长期记忆或待审核候选", id=None, disabled=True))
        return options

    def _selected(self) -> str | None:
        options = self.query_one("#memory-options", OptionList)
        index = options.highlighted
        if index is None or not (0 <= index < options.option_count):
            return None
        return options.get_option_at_index(index).id

    def action_choose(self) -> None:
        selected = self._selected()
        if selected and selected.startswith("proposal:"):
            self.dismiss(MemoryPanelAction("review", item_id=selected.split(":", 1)[1]))

    def action_toggle(self) -> None:
        selected = self._selected()
        if not selected or not selected.startswith("record:"):
            return
        _, raw_scope, memory_id = selected.split(":", 2)
        self.dismiss(MemoryPanelAction("toggle", MemoryScope(raw_scope), memory_id))

    def action_delete(self) -> None:
        selected = self._selected()
        if not selected or not selected.startswith("record:"):
            return
        _, raw_scope, memory_id = selected.split(":", 2)
        self.dismiss(MemoryPanelAction("delete", MemoryScope(raw_scope), memory_id))

    def action_edit(self) -> None:
        selected = self._selected()
        if not selected or not selected.startswith("record:"):
            return
        _, raw_scope, memory_id = selected.split(":", 2)
        self.dismiss(MemoryPanelAction("edit", MemoryScope(raw_scope), memory_id))

    def action_close(self) -> None:
        self.dismiss(None)


class MemoryDeleteScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Delete", show=False),
    ]
    CSS = """
    MemoryDeleteScreen { align: center middle; background: $background 60%; }
    #memory-delete-dialog {
        width: 70; height: auto; padding: 1 2;
        border: round $error; background: $surface;
    }
    """

    def __init__(self, record: MemoryRecord) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-delete-dialog"):
            yield Label("永久删除长期记忆？")
            yield Static(
                f"{self.record.title}\n此操作不可从 KCode 内恢复。",
                markup=False,
            )
            with Horizontal():
                yield Button("取消", id="cancel")
                yield Button("永久删除", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class MemoryEditScreen(ModalScreen[tuple[str, str, str, str] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "save", "Save", show=False),
    ]
    CSS = """
    MemoryEditScreen { align: center middle; background: $background 60%; }
    #memory-edit-dialog {
        width: 88; height: auto; padding: 1 2;
        border: round $accent; background: $surface;
    }
    #memory-edit-dialog Input { margin-bottom: 1; }
    """

    def __init__(self, record: MemoryRecord) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        with Vertical(id="memory-edit-dialog"):
            yield Label(f"编辑长期记忆 · {self.record.scope.value}/{self.record.type.value}")
            yield Input(value=self.record.title, id="edit-title", placeholder="Title")
            yield Input(value=self.record.summary, id="edit-summary", placeholder="Summary")
            yield Input(
                value=self.record.application,
                id="edit-application",
                placeholder="How to apply",
            )
            yield Input(value=self.record.body, id="edit-body", placeholder="Details")
            with Horizontal():
                yield Button("取消", id="cancel")
                yield Button("保存", id="save", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        values = (
            self.query_one("#edit-title", Input).value.strip(),
            self.query_one("#edit-summary", Input).value.strip(),
            self.query_one("#edit-application", Input).value.strip(),
            self.query_one("#edit-body", Input).value.strip(),
        )
        if all(values[:3]):
            self.dismiss(values)

    def action_cancel(self) -> None:
        self.dismiss(None)
