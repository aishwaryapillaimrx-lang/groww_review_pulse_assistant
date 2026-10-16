"""Claude client that returns schema-valid JSON, plus a test stub (implementation.md §5.2/5.3).

Sonnet 5.5 rejects forced tool_choice, so JSON comes from structured outputs
(output_config.format) instead of a forced tool call.
"""
import json
import os
import threading

import anthropic
from dotenv import load_dotenv

load_dotenv()

FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICE_PER_MTOK = {"input": 2.00, "output": 10.00}  # claude-sonnet-5-5; update if the model changes


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, model=None):
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.client = anthropic.Anthropic()
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self._lock = threading.Lock()

    def call_json(self, system, user, schema, effort="low", max_tokens=16000):
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        with self._lock:
            self.calls += 1
            self.input_tokens += response.usage.input_tokens
            self.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise LLMError(f"Model refused: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise LLMError("Response hit max_tokens before finishing")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    def cost_usd(self):
        return (self.input_tokens * PRICE_PER_MTOK["input"] + self.output_tokens * PRICE_PER_MTOK["output"]) / 1_000_000


class StubLLM:
    """Offline stand-in: `handler(system, user, schema)` returns the canned JSON."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = 0

    def call_json(self, system, user, schema, effort="low", max_tokens=16000):
        self.calls += 1
        return self.handler(system, user, schema)
