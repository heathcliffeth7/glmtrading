"""Feedback Collector Service - Periyodik olarak prediction sonuçlarını topla"""
import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.executor.ledger import (
    engine,
    get_pending_predictions,
    update_prediction_glm_feedback,
    update_prediction_result,
    PredictionLog,
)
from app.risk_manager.glm_client import GLMClient
from app.utils.influx import query_price_at_time
from app.utils.logging import get_logger


logger = get_logger(__name__)


class FeedbackCollector:
    """
    Periyodik olarak prediction'ların actual result'larını topla ve GLM'e feedback sor
    
    Workflow:
    1. 15 dakika geçmiş, henüz result toplanmamış prediction'ları bul
    2. InfluxDB'den o zamandaki fiyatı çek
    3. Actual result hesapla (fiyat değişimi %)
    4. GLM'e sor: "Tahmin doğru muydu?"
    5. DB'ye kaydet
    """
    
    def __init__(
        self,
        check_interval_minutes: int = 5,
        result_after_minutes: int = 15,
        batch_size: int = 20,
        enable_glm_feedback: bool = True,
    ):
        self.check_interval = check_interval_minutes
        self.result_after = result_after_minutes
        self.batch_size = batch_size
        self.enable_glm = enable_glm_feedback
        self.glm = GLMClient() if enable_glm_feedback else None
        self._running = False
        self._start_logged = False
    
    async def start(self):
        """Start the feedback collector loop"""
        self._running = True
        if not self._start_logged:
            logger.info(
                "Feedback Collector started: check_interval=%dm result_after=%dm batch_size=%d glm=%s",
                self.check_interval,
                self.result_after,
                self.batch_size,
                self.enable_glm,
            )
            self._start_logged = True
        
        try:
            while self._running:
                await self._collect_cycle()
                await asyncio.sleep(self.check_interval * 60)
        except asyncio.CancelledError:
            logger.info("Feedback Collector cancelled")
            raise
        except Exception as exc:
            logger.error("Feedback Collector error: %s", exc, exc_info=True)
            raise
    
    def stop(self):
        """Stop the feedback collector"""
        if self._running:
            self._running = False
            logger.info("Feedback Collector stopped")
        else:
            self._running = False
    
    async def _collect_cycle(self):
        """Single collection cycle"""
        start_time = datetime.utcnow()
        
        # Don't send these logs to Telegram
        logger.info("=== Feedback Collection Cycle Started ===", extra={"skip_telegram": True})
        
        with Session(engine) as session:
            # 1. Get pending predictions
            pending = get_pending_predictions(
                session,
                minutes_after=self.result_after,
                limit=self.batch_size,
            )
            
            if not pending:
                logger.info("No pending predictions to collect", extra={"skip_telegram": True})
                return
            
            logger.info("Found %d predictions to collect (age > %d min)", len(pending), self.result_after)
            
            # 2. Collect actual results
            results_collected = 0
            glm_feedbacks_collected = 0
            
            for pred in pending:
                try:
                    # Calculate actual result
                    actual = self._calculate_actual_result(pred)
                    
                    if actual is None:
                        logger.warning("Could not calculate result for prediction %d", pred.id)
                        continue
                    
                    # Update prediction result (with retry for locked database)
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            update_prediction_result(
                                session,
                                prediction_id=pred.id,
                                actual_price_change_pct=actual["price_change_pct"],
                                actual_direction=actual["direction"],
                                minutes_after=self.result_after,
                            )
                            results_collected += 1
                            break  # Success
                        except Exception as e:
                            session.rollback()  # Rollback before retry
                            if "database is locked" in str(e) and attempt < max_retries - 1:
                                logger.warning(f"Database locked, retry {attempt + 1}/{max_retries}")
                                await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
                            else:
                                logger.error(f"Failed to update prediction {pred.id} after {max_retries} attempts: {e}")
                                break  # Skip this prediction instead of crashing
                    
                    # Ask GLM for feedback (if enabled)
                    if self.enable_glm:
                        glm_feedback = await self._ask_glm_feedback(pred, actual)
                        
                        if glm_feedback:
                            update_prediction_glm_feedback(
                                session,
                                prediction_id=pred.id,
                                glm_correct=glm_feedback["correct"],
                                glm_important_feature=glm_feedback["important_feature"],
                                glm_reasoning=glm_feedback["reasoning"],
                            )
                            glm_feedbacks_collected += 1
                    
                    session.commit()
                    
                    logger.info(
                        "✅ Collected feedback for prediction %d: predicted=%s actual=%s correct=%s",
                        pred.id,
                        pred.predicted_direction,
                        actual["direction"],
                        pred.predicted_direction == actual["direction"],
                    )
                    
                except Exception as exc:
                    logger.error("Failed to collect feedback for prediction %d: %s", pred.id, exc)
                    session.rollback()
                    continue
        
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            "=== Feedback Collection Cycle Complete: %d results, %d GLM feedbacks in %.1fs ===",
            results_collected,
            glm_feedbacks_collected,
            elapsed,
        )
    
    def _calculate_actual_result(self, pred: PredictionLog) -> dict | None:
        """
        Calculate actual result after N minutes
        
        Returns:
            {
                "price_change_pct": float,
                "direction": "BUY/SELL/HOLD",
                "future_price": float
            }
        """
        # Future time = prediction time + result_after minutes
        future_time = pred.timestamp + timedelta(minutes=self.result_after)
        
        # Query price at future time (use 30min interval to match data feed)
        future_price = query_price_at_time(
            symbol=pred.symbol,
            target_time=future_time,
            interval="30min",  # Match data feed interval
            window_minutes=30,
        )
        
        if future_price is None:
            return None
        
        # Get base price (close_price at prediction time)
        base_price = pred.close_price
        
        # Fallback: If close_price is 0 or missing, try to get it from InfluxDB/Binance
        if base_price <= 0:
            logger.debug("close_price is 0 for prediction %d, fetching from InfluxDB/Binance", pred.id)
            base_price = query_price_at_time(
                symbol=pred.symbol,
                target_time=pred.timestamp,  # Prediction time
                interval="30min",
                window_minutes=30,
            )
            
            if base_price is None or base_price <= 0:
                logger.warning("Could not get base price for prediction %d", pred.id)
                return None
        
        # Calculate price change
        price_change_pct = ((future_price - base_price) / base_price) * 100
        
        # Determine direction (same thresholds as training: ±0.5%)
        if price_change_pct > 0.5:
            direction = "BUY"
        elif price_change_pct < -0.5:
            direction = "SELL"
        else:
            direction = "HOLD"
        
        return {
            "price_change_pct": price_change_pct,
            "direction": direction,
            "future_price": future_price,
        }
    
    async def _ask_glm_feedback(self, pred: PredictionLog, actual: dict) -> dict | None:
        """
        Ask GLM for feedback on prediction quality
        
        Returns:
            {
                "correct": bool,
                "important_feature": str,
                "reasoning": str
            }
        """
        if not self.glm:
            return None
        
        prompt = self._build_glm_feedback_prompt(pred, actual)
        
        try:
            response = self.glm.request(prompt)
            parsed = self._parse_glm_feedback_response(response)
            return parsed
        except Exception as exc:
            logger.error("GLM feedback request failed: %s", exc)
            return None
    
    def _build_glm_feedback_prompt(self, pred: PredictionLog, actual: dict) -> list[dict]:
        """Build GLM prompt for feedback"""
        return [{
            "role": "system",
            "content": "Sen bir makine öğrenmesi uzmanısın. Türev piyasa modelinin tahminlerini değerlendiriyorsun."
        }, {
            "role": "user",
            "content": f"""
**Geçmiş Tahmin Analizi:**

**Girdi Features ({self.result_after} dakika önce):**
- Long/Short Ratio: {pred.long_short_ratio:.4f}
- Open Interest: {pred.open_interest:.2f}
- Funding Rate: {pred.funding_rate:.6f}
- EMA20: {pred.ema_20:.2f}, EMA50: {pred.ema_50:.2f}
- RSI: {pred.rsi_14:.2f}
- Fiyat: ${pred.close_price:.2f}

**Model Tahmini:**
- Yön: {pred.predicted_direction}
- Model Skoru: {pred.model_score:.4f} (>0.6=BUY, <0.4=SELL)
- Güven: {pred.confidence:.2f}

**Gerçek Sonuç ({self.result_after} dakika sonra):**
- Fiyat Değişimi: {actual['price_change_pct']:+.2f}%
- Yeni Fiyat: ${actual['future_price']:.2f}
- Gerçek Yön: {actual['direction']}

**Sorular:**
1. Model doğru tahmin etti mi? (tahmin={pred.predicted_direction}, gerçek={actual['direction']})
2. Eğer yanlışsa, hangi feature'a daha fazla dikkat etmeliydi?
3. Bu durumda hangi feature en önemli ipucuydu?

**Değerlendirme Kriterleri:**
- Funding rate çok yüksek/düşükse (>0.0005 veya <-0.0005) → Short squeeze/long squeeze sinyali
- Long/Short ratio çok dengesizse (>1.2 veya <0.8) → Reversal ihtimali
- RSI aşırı alım/satım bölgesinde (>70 veya <30) → Trend dönüşü
- EMA20 ve EMA50 kesişmesi → Trend değişimi

**CEVAP FORMATI (sadece JSON, başka açıklama ekleme):**
{{
  "dogru_mu": true,
  "onemli_feature": "funding_rate",
  "onemli_feature_weight": 0.85,
  "analiz": "Funding rate çok yüksek (+0.0008), long squeeze sinyali veriyordu. Model bunu doğru yorumladı."
}}
"""
        }]
    
    def _parse_glm_feedback_response(self, response: dict) -> dict | None:
        """Parse GLM feedback response"""
        try:
            content = response["choices"][0]["message"]["content"]
            
            # Clean markdown code blocks
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
            # Parse JSON
            data = json.loads(content)
            
            correct = data.get("dogru_mu", False)
            important_feature = data.get("onemli_feature", "unknown")
            reasoning = data.get("analiz", "No analysis provided")
            
            return {
                "correct": correct,
                "important_feature": important_feature,
                "reasoning": reasoning[:500],  # Truncate
            }
            
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            logger.error("Failed to parse GLM feedback: %s", exc)
            return None
