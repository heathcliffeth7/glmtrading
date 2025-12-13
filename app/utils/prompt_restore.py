from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
from pathlib import Path
import re
from typing import Optional, Iterable, Sequence, Iterator, Any


@dataclass(frozen=True)
class PromptLogEntry:
    timestamp: datetime
    symbol: str
    prompt: str
    response: str
    signal: Optional[str] = None  # BUY/SELL/CLOSE/HOLD


_QWEN_HEADER_RE = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2}T[^\]]+)\]\s+(?P<provider>QWEN|GLM)\s+REQUEST/RESPONSE",
    re.IGNORECASE,
)
_GLM_PROMPT_HEADER_RE = re.compile(
    r"^(?:📤\s*)?GLM PROMPT\s*\|\s*(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+UTC\s*\|\s*(?P<symbol>[A-Z0-9]+USDT)\s*$"
)
_AGENT3_HEADER_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(?P<symbol>[A-Z0-9]+USDT)\s+-\s+agent3_decision\b",
    re.IGNORECASE,
)
_SYMBOL_IN_TEXT_RE = re.compile(r"\b([A-Z0-9]{3,}USDT)\b")


def _to_naive_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def infer_signal_from_response(response_text: str) -> Optional[str]:
    if not response_text:
        return None
    upper = response_text.upper()
    if '"SIGNAL"' not in upper and "'SIGNAL'" not in upper:
        return None
    for candidate in ("BUY", "SELL", "CLOSE", "HOLD"):
        if f'"{candidate}"' in upper or f"'{candidate}'" in upper:
            return candidate
    return None


def infer_symbol_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    match = _SYMBOL_IN_TEXT_RE.search(text.upper())
    return match.group(1) if match else None


def iter_prompt_log_entries(lines: Iterable[str]) -> Iterator[PromptLogEntry]:
    """
    Yield structured prompt/response log entries from an iterable of lines.

    Supports both:
    - QwenClient format: "[ts] QWEN REQUEST/RESPONSE" with ">>> PROMPT"/">>> RESPONSE"
    - GLMCommunicator format: "📤 GLM PROMPT | ... UTC | SYMBOL" with "🔷 USER CONTENT:"
    """
    current_ts: Optional[datetime] = None
    current_symbol: Optional[str] = None
    prompt_parts: list[str] = []
    response_parts: list[str] = []
    prompt_chars = 0
    response_chars = 0
    in_prompt = False
    in_response = False

    def _reset() -> None:
        nonlocal current_ts, current_symbol, prompt_parts, response_parts, prompt_chars, response_chars, in_prompt, in_response
        current_ts = None
        current_symbol = None
        prompt_parts = []
        response_parts = []
        prompt_chars = 0
        response_chars = 0
        in_prompt = False
        in_response = False

    def _flush() -> Optional[PromptLogEntry]:
        nonlocal current_ts, current_symbol, prompt_parts, response_parts, prompt_chars, response_chars, in_prompt, in_response
        if not current_ts or prompt_chars <= 0:
            _reset()
            return None

        prompt_text = "".join(prompt_parts).strip()
        response_text = "".join(response_parts).strip()
        symbol = current_symbol or infer_symbol_from_text(prompt_text)
        if not symbol:
            _reset()
            return None

        entry = PromptLogEntry(
            timestamp=_to_naive_utc(current_ts),
            symbol=symbol,
            prompt=prompt_text[:49000],
            response=response_text[:10000],
            signal=infer_signal_from_response(response_text),
        )
        _reset()
        return entry

    for raw_line in lines:
        line = raw_line.rstrip("\n")

        qwen_header = _QWEN_HEADER_RE.match(line.strip())
        if qwen_header:
            flushed = _flush()
            if flushed:
                yield flushed
            ts_str = qwen_header.group("timestamp")
            try:
                current_ts = datetime.fromisoformat(ts_str)
            except Exception:
                current_ts = None
            current_symbol = None
            continue

        glm_header = _GLM_PROMPT_HEADER_RE.match(line.strip())
        if glm_header:
            flushed = _flush()
            if flushed:
                yield flushed
            current_symbol = glm_header.group("symbol").upper()
            current_ts = datetime.strptime(glm_header.group("ts"), "%Y-%m-%d %H:%M:%S")
            continue

        agent3_header = _AGENT3_HEADER_RE.match(line.strip())
        if agent3_header:
            flushed = _flush()
            if flushed:
                yield flushed
            current_symbol = agent3_header.group("symbol").upper()
            current_ts = datetime.strptime(agent3_header.group("ts"), "%Y-%m-%d %H:%M:%S")
            continue

        stripped = line.strip()
        if stripped in {">>> PROMPT:", "--- PROMPT ---"} or "🔷 USER CONTENT:" in line or stripped == "USER CONTENT:":
            in_prompt = True
            in_response = False
            continue

        if stripped in {">>> RESPONSE:", "--- RESPONSE ---"} or "📥 GLM RESPONSE" in line or stripped == "GLM RESPONSE":
            in_prompt = False
            in_response = True
            continue

        if in_prompt:
            if prompt_chars < 49000:
                remaining = 49000 - prompt_chars
                chunk = raw_line if len(raw_line) <= remaining else raw_line[:remaining]
                prompt_parts.append(chunk)
                prompt_chars += len(chunk)
        elif in_response:
            if response_chars < 10000:
                remaining = 10000 - response_chars
                chunk = raw_line if len(raw_line) <= remaining else raw_line[:remaining]
                response_parts.append(chunk)
                response_chars += len(chunk)

    flushed = _flush()
    if flushed:
        yield flushed


def parse_prompt_log_text(text: str) -> list[PromptLogEntry]:
    """Parse a prompt/response log excerpt (glm_prompts.log) into structured entries."""
    return list(iter_prompt_log_entries(io.StringIO(text)))


def read_log_tail(path: Path, max_bytes: int = 20 * 1024 * 1024) -> str:
    """Read the last max_bytes of a log file as UTF-8 text."""
    if max_bytes <= 0:
        return ""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - max_bytes)
            f.seek(start)
            return f.read().decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return ""


def iter_log_lines_from_tail(path: Path, max_bytes: int = 20 * 1024 * 1024) -> Iterator[str]:
    """Stream UTF-8 log lines from the last max_bytes of a file (best-effort decoding)."""
    if max_bytes <= 0:
        return iter(())
    try:
        f = path.open("rb")
    except FileNotFoundError:
        return iter(())

    try:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - max_bytes)
        f.seek(start)
        wrapper = io.TextIOWrapper(f, encoding="utf-8", errors="ignore")

        def _gen() -> Iterator[str]:
            try:
                for line in wrapper:
                    yield line
            finally:
                try:
                    wrapper.detach()
                except Exception:
                    pass
                try:
                    f.close()
                except Exception:
                    pass

        return _gen()
    except Exception:
        try:
            f.close()
        except Exception:
            pass
        return iter(())


def find_best_entry_in_log_lines(
    lines: Iterable[str],
    *,
    symbol: str,
    target_timestamp: datetime,
    preferred_signals: Sequence[str] = (),
    tolerance_seconds: int = 600,
) -> tuple[Optional[PromptLogEntry], Optional[PromptLogEntry], dict[str, Any]]:
    """
    Stream-scan log lines and find the best strict match (within tolerance) and closest candidate.

    Returns: (best_match, closest_candidate, stats)
    """
    symbol = (symbol or "").upper()
    target_ts = _to_naive_utc(target_timestamp)

    best: Optional[PromptLogEntry] = None
    best_score: tuple[int, int, int] | None = None
    closest: Optional[PromptLogEntry] = None
    closest_diff: Optional[float] = None

    parsed_entries = 0
    symbol_entries = 0

    for entry in iter_prompt_log_entries(lines):
        parsed_entries += 1
        if entry.symbol.upper() != symbol:
            continue
        symbol_entries += 1

        diff_s = abs((_to_naive_utc(entry.timestamp) - target_ts).total_seconds())
        if closest is None or closest_diff is None or diff_s < closest_diff or (
            diff_s == closest_diff and len(entry.prompt or "") > len(closest.prompt or "")
        ):
            closest = entry
            closest_diff = diff_s

        if diff_s > tolerance_seconds:
            continue
        signal_mismatch = 0
        if preferred_signals:
            signal_mismatch = 0 if (entry.signal in preferred_signals) else 1
        score = (signal_mismatch, int(diff_s), -len(entry.prompt or ""))
        if best_score is None or score < best_score:
            best = entry
            best_score = score

    stats: dict[str, Any] = {
        "parsed_entries": parsed_entries,
        "symbol_entries": symbol_entries,
    }
    if closest is not None and closest_diff is not None:
        stats.update(
            {
                "closest_entry_ts": closest.timestamp.isoformat(),
                "closest_entry_diff_s": closest_diff,
                "closest_entry_signal": closest.signal,
                "closest_entry_prompt_len": len(closest.prompt or ""),
            }
        )
    return best, closest, stats


def find_best_entry_in_log_file(
    path: Path,
    *,
    max_bytes: int,
    symbol: str,
    target_timestamp: datetime,
    preferred_signals: Sequence[str] = (),
    tolerance_seconds: int = 600,
) -> tuple[Optional[PromptLogEntry], Optional[PromptLogEntry], dict[str, Any]]:
    """Convenience wrapper over find_best_entry_in_log_lines for file tails."""
    lines = iter_log_lines_from_tail(path, max_bytes=max_bytes)
    return find_best_entry_in_log_lines(
        lines,
        symbol=symbol,
        target_timestamp=target_timestamp,
        preferred_signals=preferred_signals,
        tolerance_seconds=tolerance_seconds,
    )


def find_best_entry(
    entries: Iterable[PromptLogEntry],
    *,
    symbol: str,
    target_timestamp: datetime,
    preferred_signals: Sequence[str] = (),
    tolerance_seconds: int = 600,
) -> Optional[PromptLogEntry]:
    """
    Find the best-matching log entry by symbol + timestamp proximity.

    If preferred_signals is provided, prefer entries whose parsed signal matches.
    """
    symbol = (symbol or "").upper()
    target_ts = _to_naive_utc(target_timestamp)

    best: Optional[PromptLogEntry] = None
    best_score: tuple[int, int, int] | None = None
    for entry in entries:
        if entry.symbol.upper() != symbol:
            continue
        diff = abs(int((_to_naive_utc(entry.timestamp) - target_ts).total_seconds()))
        if diff > tolerance_seconds:
            continue
        signal_mismatch = 0
        if preferred_signals:
            signal_mismatch = 0 if (entry.signal in preferred_signals) else 1
        score = (signal_mismatch, diff, -len(entry.prompt or ""))
        if best_score is None or score < best_score:
            best = entry
            best_score = score
    return best

