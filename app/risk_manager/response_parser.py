"""
Response Parser Module

Handles parsing and repair of GLM (LLM) responses, including:
- JSON extraction from markdown code blocks
- JSON repair for malformed responses
- Signal extraction and validation
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.risk_manager.decision_models import DataAnalysis, RiskDecision, ThoughtProcess
from app.utils.logging import get_logger


logger = get_logger(__name__)


class ResponseParser:
    """
    Parser for GLM API responses.

    Handles JSON extraction, repair, and conversion to RiskDecision objects.
    """

    def __init__(self, settings=None, glm_client=None, nof1_prompt_builder=None):
        """
        Initialize the response parser.

        Args:
            settings: Application settings object
            glm_client: GLM client for translation (optional)
            nof1_prompt_builder: Prompt builder for exit plan calculation (optional)
        """
        self._settings = settings
        self._glm = glm_client
        self._nof1_prompt_builder = nof1_prompt_builder

        # Parsing metrics
        self._parsing_metrics = {
            "total_json_requests": 0,
            "successful_json_parsing": 0,
            "json_parsing_errors": 0,
            "fallback_parsing_successes": 0,
            "fallback_parsing_failures": 0,
            "signal_recoveries": 0,
            "complete_failures": 0,
            "thought_process_present": 0,
            "thought_process_missing": 0,
            "thought_process_parse_errors": 0,
        }

    @property
    def parsing_metrics(self) -> Dict[str, int]:
        """Get current parsing metrics."""
        return self._parsing_metrics.copy()

    def reset_metrics(self) -> None:
        """Reset all parsing metrics to zero."""
        for key in self._parsing_metrics:
            self._parsing_metrics[key] = 0

    # =========================================================================
    # DATA STRUCTURE PARSING
    # =========================================================================

    def parse_data_analysis(self, data: dict) -> DataAnalysis:
        """
        Parse data_analysis from GLM response with graceful fallback.

        Expected format:
        {
            "adx_interpretation": "ADX 24.7 = orta trend gücü...",
            "volume_assessment": "Volume Ratio 0.1x = düşük hacim...",
            "funding_view": "Funding 0.0064% = nötr..."
        }
        """
        if not data or not isinstance(data, dict):
            return DataAnalysis()

        try:
            return DataAnalysis(
                adx_interpretation=str(data.get('adx_interpretation', ''))[:300],
                volume_assessment=str(data.get('volume_assessment', ''))[:300],
                funding_view=str(data.get('funding_view', ''))[:300],
            )
        except Exception as e:
            logger.warning("data_analysis parse error: %s", e)
            return DataAnalysis()

    def parse_thought_process(self, data: dict) -> ThoughtProcess:
        """
        Parse thought_process from GLM response with graceful fallback.

        Expected format:
        {
            "thesis_bullish": "Yükseliş faktörleri...",
            "antithesis_bearish": "Düşüş riskleri...",
            "synthesis_verdict": "Nihai sonuç..."
        }
        """
        if not data or not isinstance(data, dict):
            return ThoughtProcess()

        try:
            return ThoughtProcess(
                thesis_bullish=str(data.get('thesis_bullish', data.get('thesis', '')))[:500],
                antithesis_bearish=str(data.get('antithesis_bearish', data.get('antithesis', '')))[:500],
                synthesis_verdict=str(data.get('synthesis_verdict', data.get('synthesis', '')))[:500],
            )
        except Exception as e:
            logger.warning("thought_process parse error: %s", e)
            self._parsing_metrics["thought_process_parse_errors"] += 1
            return ThoughtProcess()

    # =========================================================================
    # JSON REPAIR METHODS
    # =========================================================================

    def fix_trailing_commas(self, json_str: str) -> str:
        """Remove trailing commas in objects and arrays."""
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        return json_str

    def fix_unclosed_strings(self, json_str: str) -> str:
        """Fix unclosed string literals."""
        if json_str.count('"') % 2 != 0:
            last_quote = json_str.rfind('"')
            if last_quote > 0:
                next_brace = json_str.find('}', last_quote)
                next_bracket = json_str.find(']', last_quote)

                positions = [pos for pos in [next_brace, next_bracket] if pos > last_quote]
                if positions:
                    close_pos = min(positions)
                    json_str = json_str[:close_pos] + '"' + json_str[close_pos:]
                    logger.debug("Fixed unclosed string in JSON")
        return json_str

    def fix_missing_commas(self, json_str: str) -> str:
        """Attempt to fix missing commas between JSON elements."""
        # Add missing commas between object properties
        json_str = re.sub(r'"\s*\n\s*"', '",\n    "', json_str)

        # Add missing commas between number/bool/null and key
        json_str = re.sub(r'(\d+|true|false|null)\s*\n\s*"', r'\1,\n    "', json_str)

        # Add missing commas after object/array closing and key
        json_str = re.sub(r'([}\]])\s*\n\s*"', r'\1,\n    "', json_str)

        # Add missing commas between array elements
        json_str = re.sub(r'([0-9.]+)\s*\n\s*([0-9.]+)', r'\1,\n\2', json_str)

        # Add missing commas for inline cases (minified JSON)
        json_str = re.sub(r'(\d+|true|false|null)\s*"', r'\1, "', json_str)
        json_str = re.sub(r'([}\]])\s*"', r'\1, "', json_str)

        return json_str

    def fix_malformed_numbers(self, json_str: str) -> str:
        """Fix malformed numbers (like extra decimals)."""
        # Fix numbers with multiple decimal points
        json_str = re.sub(r'(\d+\.\d+)\.\d+', r'\1', json_str)
        # Fix numbers that end with decimal point
        json_str = re.sub(r'(\d+)\.([^\d])', r'\1.0\2', json_str)
        return json_str

    def fix_boolean_null_values(self, json_str: str) -> str:
        """Fix common boolean and null value formatting issues."""
        # Fix quoted boolean/null values
        json_str = re.sub(r'"true"', 'true', json_str)
        json_str = re.sub(r'"false"', 'false', json_str)
        json_str = re.sub(r'"null"', 'null', json_str)
        # Fix TRUE/FALSE in upper case
        json_str = re.sub(r'\bTRUE\b', 'true', json_str)
        json_str = re.sub(r'\bFALSE\b', 'false', json_str)
        json_str = re.sub(r'\bNULL\b', 'null', json_str)
        return json_str

    # =========================================================================
    # JSON EXTRACTION AND PREPARATION
    # =========================================================================

    def prepare_json_payload(self, raw: str) -> str:
        """
        Clean GLM response so that json.loads accepts multi-line reasoning.

        Handles:
        - Markdown code block extraction
        - Truncated JSON repair
        - String escaping
        - Multiple repair attempts
        """
        raw = raw.strip()
        logger.debug("Original JSON payload length: %d", len(raw))

        # Extract JSON from markdown code block ANYWHERE in the text
        json_block_match = re.search(r'```json\s*([\s\S]*?)```', raw)
        if json_block_match:
            raw = json_block_match.group(1).strip()
            logger.info("Extracted JSON from markdown code block")
        else:
            # Fallback: try to find any code block
            code_block_match = re.search(r'```\s*([\s\S]*?)```', raw)
            if code_block_match:
                raw = code_block_match.group(1).strip()
                logger.info("Extracted content from generic code block")
            else:
                # Legacy fallback: Remove markdown code blocks with old patterns
                if raw.startswith("```json"):
                    raw = raw[7:]
                elif raw.startswith("```"):
                    raw = raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

        # Handle truncated JSON responses - find the last complete JSON object
        brace_count = 0
        last_complete_pos = -1
        in_string = False
        escape_next = False
        last_quote_pos = -1

        for i, ch in enumerate(raw):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                if in_string:
                    last_quote_pos = i
                continue
            if not in_string:
                if ch == '{':
                    brace_count += 1
                elif ch == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        last_complete_pos = i

        # If we found a complete JSON object, handle string truncation
        if last_complete_pos > 0 and last_complete_pos < len(raw) - 1:
            if in_string and last_quote_pos > 0:
                raw = raw[:last_complete_pos + 1]
                truncation_point = min(last_complete_pos, len(raw))
                if truncation_point > last_quote_pos:
                    raw = raw[:last_quote_pos] + '"' + raw[last_quote_pos + 1:last_complete_pos + 1]
                logger.info("Fixed truncated string at position %d", last_complete_pos)
            else:
                raw = raw[:last_complete_pos + 1]
                logger.info("Truncated JSON to complete object at position %d", last_complete_pos)

        # Enhanced string cleaning for newlines and special characters
        cleaned_chars = []
        in_string = False
        escape_next = False

        for i, ch in enumerate(raw):
            if in_string:
                if escape_next:
                    cleaned_chars.append(ch)
                    escape_next = False
                    continue
                if ch == "\\":
                    cleaned_chars.append(ch)
                    escape_next = True
                    continue
                if ch == '"':
                    # Check if this is a closing quote
                    is_closing = False
                    next_char_idx = i + 1
                    while next_char_idx < len(raw) and raw[next_char_idx].isspace():
                        next_char_idx += 1

                    if next_char_idx < len(raw):
                        next_char = raw[next_char_idx]
                        if next_char in [',', '}', ']', ':']:
                            is_closing = True
                    else:
                        is_closing = True

                    if is_closing:
                        cleaned_chars.append(ch)
                        in_string = False
                    else:
                        cleaned_chars.append('\\"')
                    continue

                if ch == "\n":
                    cleaned_chars.append("\\n")
                    continue
                if ch == "\r":
                    continue
                if ch == "\t":
                    cleaned_chars.append("\\t")
                    continue
                if ord(ch) < 32:
                    cleaned_chars.append(f"\\u{ord(ch):04x}")
                    continue
                cleaned_chars.append(ch)
            else:
                cleaned_chars.append(ch)
                if ch == '"':
                    in_string = True

        result = "".join(cleaned_chars)

        # Apply repair functions iteratively
        repair_funcs = [
            ("trailing commas", self.fix_trailing_commas),
            ("unclosed strings", self.fix_unclosed_strings),
            ("missing commas", self.fix_missing_commas),
            ("malformed numbers", self.fix_malformed_numbers),
            ("boolean/null values", self.fix_boolean_null_values),
        ]

        for attempt_name, repair_func in repair_funcs:
            try:
                json.loads(result)
                logger.debug("JSON is valid after %s repair", attempt_name)
                return result
            except json.JSONDecodeError as e:
                logger.debug("Attempting to fix %s: %s", attempt_name, str(e))
                result = repair_func(result)

        # Final validation attempt
        try:
            json.loads(result)
            logger.info("JSON successfully repaired after all attempts")
            return result
        except json.JSONDecodeError as e:
            logger.warning("JSON repair failed after all attempts: %s", str(e))
            logger.debug("Final JSON content: %s", result[:1000])
            return result

    # =========================================================================
    # QUANTITY NORMALIZATION
    # =========================================================================

    def normalize_quantity_to_allocation(
        self,
        action: str,
        quantity: float,
        portfolio_metrics: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Interpret GLM 'quantity' as equity allocation (0-1) and gracefully handle
        backwards-compatible coin-amount outputs.
        """
        price = 0.0
        equity = 10000.0
        current_position = 0.0

        if portfolio_metrics:
            price = portfolio_metrics.get("price", 0.0) or 0.0
            equity = portfolio_metrics.get("equity", 10000.0) or 10000.0
            current_position = abs(portfolio_metrics.get("position", 0.0) or 0.0)

        base_qty = abs(quantity)

        if action in ["HOLD", "CLOSE"]:
            if base_qty <= 1.0:
                return max(0.0, min(base_qty, 1.0))

            if current_position > 0.0:
                ratio = min(base_qty / current_position, 1.0)
                logger.info("Converted coin close amount %.6f to close ratio %.4f", base_qty, ratio)
                return ratio

            return 1.0

        # BUY / SELL
        if base_qty <= 1.0:
            return max(0.0, min(base_qty, 1.0))

        if price > 0.0 and equity > 0.0:
            ratio = (base_qty * price) / equity
            logger.info(
                "Converted coin amount %.6f to equity ratio %.4f (price=%.2f, equity=%.2f)",
                base_qty,
                ratio,
                price,
                equity,
            )
            return ratio

        return base_qty

    def normalize_leverage(self, value: float) -> float:
        """Normalize leverage to 1-20x range."""
        if value <= 0:
            return 1.0
        return max(1.0, min(value, 20.0))

    # =========================================================================
    # TRANSLATION
    # =========================================================================

    def translate_to_turkish(self, text: str) -> str:
        """
        Translate English text to Turkish using GLM.
        If the text is already in Turkish, returns it unchanged.
        """
        if not text or len(text) < 20:
            return text

        if not self._glm:
            return text

        # Check for common English words/patterns
        english_markers = [
            'the ', 'market', 'price', 'trend', 'bearish', 'bullish',
            'support', 'resistance', 'momentum', 'indicates', 'suggests',
            'trading', 'position', 'volume', 'level', 'break', 'strong',
            'despite', 'therefore', 'however', 'confluence'
        ]

        text_lower = text.lower()
        english_word_count = sum(1 for marker in english_markers if marker in text_lower)

        # If less than 3 English markers found, assume it's already Turkish
        if english_word_count < 3:
            return text

        logger.info("English reasoning detected (%d markers), translating...", english_word_count)

        try:
            translation_prompt = (
                "Aşağıdaki İngilizce kripto analiz metnini Türkçe'ye çevir. "
                "Sadece çeviriyi yaz, başka bir şey ekleme. "
                "Teknik terimleri (RSI, EMA, MACD, support, resistance) olduğu gibi bırak:\n\n"
                f"{text}"
            )

            response = self._glm.request([
                {"role": "user", "content": translation_prompt}
            ])

            if response and "choices" in response:
                translated = response["choices"][0]["message"]["content"].strip()
                logger.info("Translation completed: %d → %d chars", len(text), len(translated))
                return translated

        except Exception as e:
            logger.warning("Translation failed: %s - using original text", e)

        return text

    # =========================================================================
    # MAIN PARSING METHODS
    # =========================================================================

    def parse_response(
        self,
        response: dict,
        portfolio_metrics: Optional[Dict[str, Any]] = None
    ) -> RiskDecision:
        """
        Parse a standard GLM response into a RiskDecision.
        """
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            logger.error("GLM response parse error: %s", exc)
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning="LLM yanıtı okunamadı",
                decision_timestamp=datetime.now(timezone.utc)
            )

        parsed = self.parse_content(content, portfolio_metrics)
        if not parsed:
            logger.warning("GLM output format invalid: %s", content)
            return RiskDecision(
                action="HOLD",
                amount=0.0,
                reasoning=content,
                decision_timestamp=datetime.now(timezone.utc)
            )
        return RiskDecision(**parsed)

    def parse_content(
        self,
        content: str,
        portfolio_metrics: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Parse raw content string into a dictionary suitable for RiskDecision.
        """
        try:
            content = self.prepare_json_payload(content)
            payload = json.loads(content)

            thought_process = None
            data_analysis = None
            exit_plan = None

            # Extract trade details from nested structure if present
            if isinstance(payload, dict) and len(payload) == 1:
                first_key = next(iter(payload))
                if isinstance(payload[first_key], dict) and "trade_signal_args" in payload[first_key]:
                    logger.info("Detected nested NOF1 style response format")
                    signal_args = payload[first_key]["trade_signal_args"]
                    action = signal_args.get("signal", "HOLD").upper()
                    quantity = float(signal_args.get("quantity", 0))
                    amount = quantity

                    leverage = float(signal_args.get("leverage", 5))
                    glm_confidence = float(signal_args.get("confidence", 0)) * 100

                    stop_loss = float(signal_args.get("stop_loss", 0))
                    take_profit = float(signal_args.get("take_profit", 0))
                    invalidation = signal_args.get("invalidation_condition", "")

                    exit_plan = {
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "invalidation_condition": invalidation
                    }

                    reasoning = payload[first_key].get("gerekçe", "")
                    reason_primary = signal_args.get("reason_primary", "")
                    reason_secondary = signal_args.get("reason_secondary", "")

                    # Extract thought_process
                    if self._settings and self._settings.zai.enable_thought_process:
                        raw_tp = payload[first_key].get("thought_process", {})
                        if raw_tp:
                            thought_process = self.parse_thought_process(raw_tp)
                            self._parsing_metrics["thought_process_present"] += 1
                        else:
                            self._parsing_metrics["thought_process_missing"] += 1

                    # Extract data_analysis
                    raw_da = payload[first_key].get("data_analysis", {})
                    if raw_da:
                        data_analysis = self.parse_data_analysis(raw_da)

                else:
                    # Standard flat format (symbol as key)
                    symbol_data = payload[first_key]
                    action = symbol_data.get("signal", symbol_data.get("karar", "HOLD")).upper()
                    amount = float(symbol_data.get("miktar", 0))
                    leverage = float(symbol_data.get("kaldıraç", symbol_data.get("leverage", 5)))
                    reasoning = symbol_data.get("gerekçe", symbol_data.get("reasoning", ""))
                    glm_confidence = float(symbol_data.get("confidence", symbol_data.get("ai_confidence", 0)))
                    reason_primary = symbol_data.get("reason_primary", "")
                    reason_secondary = symbol_data.get("reason_secondary", "")

                    if "stop_loss" in symbol_data:
                        exit_plan = {
                            "stop_loss": float(symbol_data.get("stop_loss", 0)),
                            "take_profit": float(symbol_data.get("take_profit", symbol_data.get("profit_target", 0))),
                            "invalidation_condition": symbol_data.get("invalidation_condition", "")
                        }

                    if self._settings and self._settings.zai.enable_thought_process:
                        raw_tp = symbol_data.get("thought_process", {})
                        if raw_tp:
                            thought_process = self.parse_thought_process(raw_tp)
                            self._parsing_metrics["thought_process_present"] += 1
                        else:
                            self._parsing_metrics["thought_process_missing"] += 1

                    raw_da = symbol_data.get("data_analysis", {})
                    if raw_da:
                        data_analysis = self.parse_data_analysis(raw_da)

            else:
                # Standard flat format fallback
                action = payload.get("karar", payload.get("signal", "HOLD")).upper()
                amount = float(payload.get("miktar", 0))
                leverage = float(payload.get("kaldıraç", payload.get("leverage", 5)))
                reasoning = payload.get("gerekçe", payload.get("reasoning", ""))
                glm_confidence = float(payload.get("ai_confidence", payload.get("confidence", 0)))
                reason_primary = payload.get("reason_primary", "")
                reason_secondary = payload.get("reason_secondary", "")

                if "stop_loss" in payload:
                    exit_plan = {
                        "stop_loss": float(payload.get("stop_loss", 0)),
                        "take_profit": float(payload.get("take_profit", payload.get("profit_target", 0))),
                        "invalidation_condition": payload.get("invalidation_condition", "")
                    }

                if self._settings and self._settings.zai.enable_thought_process:
                    raw_tp = payload.get("thought_process", {})
                    if raw_tp:
                        thought_process = self.parse_thought_process(raw_tp)
                        self._parsing_metrics["thought_process_present"] += 1
                    else:
                        self._parsing_metrics["thought_process_missing"] += 1

                raw_da = payload.get("data_analysis", {})
                if raw_da:
                    data_analysis = self.parse_data_analysis(raw_da)

            if action not in {"BUY", "SELL", "HOLD", "CLOSE"}:
                return None

            amount = self.normalize_quantity_to_allocation(action, amount, portfolio_metrics)
            amount = max(0.0, amount)
            leverage = self.normalize_leverage(leverage)
            glm_confidence = max(0.0, min(100.0, glm_confidence))

            # Clear exit_plan for CLOSE and HOLD
            if action in ["CLOSE", "HOLD"]:
                exit_plan = None

            reasoning = self.translate_to_turkish(reasoning)

            return {
                "action": action,
                "amount": amount,
                "reasoning": reasoning,
                "leverage": leverage,
                "glm_confidence": glm_confidence,
                "reason_primary": reason_primary,
                "reason_secondary": reason_secondary,
                "exit_plan": exit_plan,
                "thought_process": thought_process,
                "data_analysis": data_analysis,
            }
        except Exception as e:
            logger.error("Error parsing GLM content: %s", e)
            return None
