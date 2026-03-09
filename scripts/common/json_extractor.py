"""
Fault-tolerant JSON extractor for LLM output.

Handles common issues with Agent output:
- Markdown code block fences (```json ... ```)
- Preamble/postamble text (greetings, explanations)
- Non-JSON artifacts mixed in
- Records raw output to error log on extraction failure
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional, Union

# Standard path injection for paper-agent common modules
_PAPER_AGENT_ROOT = Path(os.environ.get("PAPER_AGENT_ROOT", str(Path(__file__).resolve().parent.parent)))
if str(_PAPER_AGENT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_PAPER_AGENT_ROOT / "scripts"))

logger = logging.getLogger("paper_agent.json_extractor")


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code block fences (```json ... ``` or ``` ... ```)."""
    # Match ```json\n...\n``` or ```\n...\n```
    pattern = r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        # Return the longest match (likely the main content)
        return max(matches, key=len).strip()
    return text


def _find_outermost_bracket(text: str, open_char: str, close_char: str) -> Optional[str]:
    """Find the outermost balanced bracket pair in text."""
    depth = 0
    start_idx = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue

        if ch == "\\":
            if in_string:
                escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_char:
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0 and start_idx >= 0:
                return text[start_idx : i + 1]

    return None


def extract_json_array(text: str) -> Optional[list]:
    """Extract the outermost JSON array [...] from text.

    Args:
        text: Raw text potentially containing a JSON array.

    Returns:
        Parsed list if found and valid, None otherwise.
    """
    # First try stripping markdown fences
    cleaned = _strip_markdown_fences(text)

    # Try to find outermost [...]
    candidate = _find_outermost_bracket(cleaned, "[", "]")
    if candidate:
        try:
            result = json.loads(candidate)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Fallback: try on original text if cleaned didn't work
    if cleaned != text:
        candidate = _find_outermost_bracket(text, "[", "]")
        if candidate:
            try:
                result = json.loads(candidate)
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

    return None


def extract_json_object(text: str) -> Optional[dict]:
    """Extract the outermost JSON object {...} from text.

    Args:
        text: Raw text potentially containing a JSON object.

    Returns:
        Parsed dict if found and valid, None otherwise.
    """
    # First try stripping markdown fences
    cleaned = _strip_markdown_fences(text)

    # Try to find outermost {...}
    candidate = _find_outermost_bracket(cleaned, "{", "}")
    if candidate:
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Fallback: try on original text
    if cleaned != text:
        candidate = _find_outermost_bracket(text, "{", "}")
        if candidate:
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

    return None


def extract_json(text: str) -> Optional[Union[list, dict]]:
    """Extract JSON (array or object) from text, trying array first.

    Args:
        text: Raw text potentially containing JSON.

    Returns:
        Parsed list or dict if found and valid, None otherwise.
    """
    # Try array first (most common for batch scoring results)
    result = extract_json_array(text)
    if result is not None:
        return result

    # Then try object
    result = extract_json_object(text)
    if result is not None:
        return result

    return None


def extract_json_with_fallback(
    text: str,
    default: Any = None,
    error_log_path: Optional[str] = None,
    context: str = "",
) -> Any:
    """Extract JSON from text with fallback and error logging.

    Args:
        text: Raw text to extract JSON from.
        default: Default value to return on failure.
        error_log_path: Optional path to write raw output on failure.
        context: Context string for error messages.

    Returns:
        Extracted JSON or default value.
    """
    result = extract_json(text)
    if result is not None:
        return result

    # Extraction failed — log the raw output
    logger.error(
        "JSON extraction failed%s. Raw output length: %d chars",
        f" ({context})" if context else "",
        len(text),
    )

    if error_log_path:
        try:
            error_dir = os.path.dirname(error_log_path)
            if error_dir:
                os.makedirs(error_dir, exist_ok=True)
            with open(error_log_path, "w", encoding="utf-8") as f:
                f.write(f"# JSON Extraction Failed\n")
                f.write(f"# Context: {context}\n")
                f.write(f"# Raw output length: {len(text)} chars\n\n")
                f.write(text)
            logger.info("Raw output saved to: %s", error_log_path)
        except OSError as e:
            logger.error("Failed to write error log to %s: %s", error_log_path, e)

    return default
