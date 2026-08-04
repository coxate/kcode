from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol

from kcode.conversation import ConversationMessage
from kcode.events import ProviderEvent
from kcode.tools.base import ToolDefinition


class ChatProvider(Protocol):
    @property
    def display_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def stream(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> AsyncIterator[ProviderEvent]: ...
