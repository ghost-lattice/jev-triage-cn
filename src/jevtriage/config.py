from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json



def _scalar(value: str) -> Any:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _yaml_subset(text: str) -> dict[str, Any]:
    """Parse the small, documented YAML subset used by this project's configs.

    Supported: space-indented mappings, mapping lists, plain/quoted scalars,
    booleans, full-width punctuation in values, and ``|``/``>`` multiline text.
    Unsupported: anchors, aliases, flow collections, tags, comments after values,
    and arbitrary YAML scalar coercions. Keeping this limited parser internal
    avoids a dependency/install; use JSON configs if this subset is insufficient.
    """
    lines = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lines.append((len(line) - len(line.lstrip(" ")), line.strip()))

    def block(index: int, indent: int) -> tuple[Any, int]:
        is_list = lines[index][1].startswith("- ")
        value: Any = [] if is_list else {}
        while index < len(lines) and lines[index][0] == indent:
            content = lines[index][1]
            if is_list:
                if not content.startswith("- "):
                    break
                content = content[2:].strip()
                if not content:
                    index += 1
                    child, index = block(index, lines[index][0])
                    value.append(child)
                    continue
                key, separator, raw = content.partition(":")
                if not separator:
                    raise ValueError("list items must be mappings in this YAML subset")
                item = {key.strip(): _scalar(raw)} if raw.strip() else {key.strip(): None}
                index += 1
                if index < len(lines) and lines[index][0] > indent:
                    child, index = block(index, lines[index][0])
                    if item[key.strip()] is None:
                        item[key.strip()] = child
                    elif isinstance(child, dict):
                        item.update(child)
                value.append(item)
                continue
            if content.startswith("- "):
                break
            key, separator, raw = content.partition(":")
            if not separator:
                raise ValueError("expected key: value in YAML config")
            index += 1
            if raw.strip() in {"|", ">"}:
                parts = []
                while index < len(lines) and lines[index][0] > indent:
                    parts.append(lines[index][1])
                    index += 1
                value[key.strip()] = ("\n" if raw.strip() == "|" else " ").join(parts)
            elif raw.strip():
                value[key.strip()] = _scalar(raw)
            elif index < len(lines) and lines[index][0] > indent:
                child, index = block(index, lines[index][0])
                value[key.strip()] = child
            else:
                value[key.strip()] = {}
        return value, index

    if not lines:
        raise ValueError("config is empty")
    parsed, end = block(0, lines[0][0])
    if end != len(lines) or not isinstance(parsed, dict):
        raise ValueError("unsupported YAML config structure")
    return parsed


@dataclass(frozen=True)
class ChoiceQuestion:
    id: str
    instructions: str
    options: dict[str, str]


@dataclass(frozen=True)
class TriageConfig:
    scenario: str
    model: str
    text_column: str
    classification: ChoiceQuestion
    additional_questions: tuple[ChoiceQuestion, ...]
    review_band: float
    digest: str


def _choice(raw: dict[str, Any], label: str) -> ChoiceQuestion:
    options = raw.get("options")
    if not isinstance(options, dict) or not 2 <= len(options) <= 255:
        raise ValueError(f"{label}.options must have 2 to 255 entries")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in options.items()):
        raise ValueError(f"{label}.options must map strings to Chinese descriptions")
    if raw.get("type", "choice") != "choice":
        raise ValueError(f"{label} must use choice so confidence is available")
    return ChoiceQuestion(str(raw["id"]), str(raw["instructions"]), options)


def load_config(path: str | Path) -> TriageConfig:
    raw = _yaml_subset(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config must be a YAML mapping")
    classification = _choice(raw["classification"], "classification")
    additional = tuple(_choice(item, "additional_questions item") for item in raw.get("additional_questions", []))
    ids = [classification.id, *(question.id for question in additional)]
    if len(ids) != len(set(ids)):
        raise ValueError("question ids must be unique")
    model = str(raw.get("model", "jev-1.13.0"))
    digest = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    review_band = float(raw.get("review_band", 0.15))
    if not 0.0 <= review_band <= 1.0:
        raise ValueError("review_band must be between 0 and 1")
    return TriageConfig(str(raw["scenario"]), model, str(raw.get("text_column", "text")), classification, additional, review_band, digest)
