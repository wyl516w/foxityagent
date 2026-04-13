from __future__ import annotations

from abc import ABC, abstractmethod

from agent_studio.core.models import ControlActionPayload, ControlActionResult


class InputController(ABC):
    @property
    @abstractmethod
    def controller_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def execute(self, payload: ControlActionPayload) -> ControlActionResult:
        raise NotImplementedError
