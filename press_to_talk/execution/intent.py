from __future__ import annotations

import asyncio
from typing import Any


class IntentExecutionRunner:
    def __init__(self, cfg: Any) -> None:
        from ..agent.agent import OpenAICompatibleAgent

        self.agent = OpenAICompatibleAgent(cfg)

    async def run_async(self, transcript: str) -> str:
        return await self.agent.chat(transcript)

    async def classify_async(self, transcript: str) -> str:
        return await self.agent.classify_intent(transcript)

    def run(self, transcript: str) -> str:
        return asyncio.run(self.run_async(transcript))

    def classify(self, transcript: str) -> str:
        return asyncio.run(self.classify_async(transcript))
