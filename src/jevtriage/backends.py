from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import random
import time
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import ChoiceQuestion, TriageConfig


@dataclass(frozen=True)
class ChoiceResult:
    choice: str
    confidence: float
    top_probability: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class TriageResult:
    backend: str
    model: str
    classification: ChoiceResult
    additional: dict[str, ChoiceResult]
    usage: dict[str, int]

    def as_dict(self) -> dict:
        return {"backend": self.backend, "model": self.model, "classification": self.classification.__dict__, "additional": {key: value.__dict__ for key, value in self.additional.items()}, "usage": self.usage}


class Backend(Protocol):
    def triage(self, text: str, config: TriageConfig) -> TriageResult: ...


def _confidence(probabilities: dict[str, float]) -> float:
    top = max(probabilities.values())
    return 1.0 if len(probabilities) == 1 else round(max(0.0, (len(probabilities) * top - 1) / (len(probabilities) - 1)), 6)


def _choice_result(answer: dict) -> ChoiceResult:
    probabilities = {str(key): float(value) for key, value in answer["probabilities"].items()}
    return ChoiceResult(str(answer["choice"]), float(answer["confidence"]), max(probabilities.values()), probabilities)


class MockBackend:
    """Offline deterministic fake backend; its outputs are not model evaluations."""
    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.name = "mock"

    def _answer(self, text: str, question: ChoiceQuestion, digest: str) -> ChoiceResult:
        value = int(hashlib.sha256(f"{self.seed}|{digest}|{question.id}|{text}".encode()).hexdigest(), 16)
        rng = random.Random(value)
        weights = [rng.random() + 0.03 for _ in question.options]
        total = sum(weights)
        probabilities = {name: round(weight / total, 6) for name, weight in zip(question.options, weights)}
        # Keep exact normalization after rounding.
        first = next(iter(probabilities))
        probabilities[first] += 1.0 - sum(probabilities.values())
        choice = max(probabilities, key=probabilities.get)
        return ChoiceResult(choice, _confidence(probabilities), max(probabilities.values()), probabilities)

    def triage(self, text: str, config: TriageConfig) -> TriageResult:
        classification = self._answer(text, config.classification, config.digest)
        additional = {question.id: self._answer(text, question, config.digest) for question in config.additional_questions}
        estimated_input = max(1, len(text) + sum(len(question.instructions) + sum(map(len, question.options.values())) for question in (config.classification, *config.additional_questions)))
        return TriageResult(self.name, "mock-jev-1.13.0", classification, additional, {"input_tokens": estimated_input, "output_tokens": 0})


class JevBackend:
    """Minimal HTTP backend. State intentionally contains the text only."""
    endpoint = "https://api.typesafe.ai/v1/systemone"
    name = "jev"
    def __init__(self, on_success: Callable[[str, dict[str, int]], None] | None = None) -> None: self.on_success = on_success

    @staticmethod
    def _payload(text: str, config: TriageConfig) -> bytes:
        questions = {question.id: {"type": "choice", "instructions": question.instructions, "criteria": question.options} for question in (config.classification, *config.additional_questions)}
        return json.dumps({"state": text, "model": config.model, "questions": questions}, ensure_ascii=False).encode("utf-8")

    def triage(self, text: str, config: TriageConfig) -> TriageResult:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise RuntimeError("TYPESAFE_API_KEY is required for the jev backend")
        request = Request(self.endpoint, data=self._payload(text, config), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        for attempt in range(3):
            try:
                with urlopen(request, timeout=45) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                classification = _choice_result(raw["answers"][config.classification.id])
                additional = {question.id: _choice_result(raw["answers"][question.id]) for question in config.additional_questions}
                usage = {key: int(value) for key, value in raw["usage"].items()}
                if self.on_success: self.on_success(self.name, usage)
                return TriageResult(self.name, str(raw["model"]), classification, additional, usage)
            except HTTPError as error:
                # Respect rate limiting and only retry clearly transient service failures.
                if error.code not in {429, 500, 502, 503, 504, 529} or attempt == 2:
                    raise RuntimeError(f"Jev API request failed with HTTP {error.code}") from error
            except URLError as error:
                if attempt == 2:
                    raise RuntimeError("Jev API connection failed") from error
            time.sleep(1 * (2 ** attempt))
        raise AssertionError("unreachable")
