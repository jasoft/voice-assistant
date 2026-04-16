from __future__ import annotations

import asyncio
from typing import Any


class IntentExecutionRunner:
    def __init__(self, cfg: Any) -> None:
        from ..agent.agent import OpenAICompatibleAgent

        self.agent = OpenAICompatibleAgent(cfg)

    def run(self, transcript: str) -> str:
        return asyncio.run(self.agent.chat(transcript))

    def classify(self, transcript: str) -> str:
        return asyncio.run(self.agent.classify_intent(transcript))
