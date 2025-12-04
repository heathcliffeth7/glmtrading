import json
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple
from sqlalchemy.orm import Session

from app.risk_manager.glm_client import GLMClient
from app.risk_manager.risk_controls import validate_exit_plan_update, ValidationError
from app.executor.ledger import Trade

logger = logging.getLogger(__name__)


class DynamicExitUpdater:
    def __init__(self, glm_client: GLMClient, session: Session):
        self.glm_client = glm_client
        self.session = session
        self.min_update_interval_seconds = 360
        self.max_updates_per_position = 10
    
    def evaluate_update_necessity(
        self,
        position_data: Dict,
        market_data: Dict
    ) -> Tuple[bool, Optional[str]]:
        """
        Exit plan güncellemesi gerekli mi değerlendir

        DEVRE DISI: GLM'den exit plan isteme kaldirildi.
        TP/SL tamamen Python tarafinda ATR-bazli hesaplaniyor.

        Returns:
            (should_update, reason)
        """
        # GLM exit plan updates disabled - TP/SL calculated by Python only
        return False, "GLM exit updates disabled - using Python ATR-based calculations"

        position_id = position_data.get('position_id')
        exit_plan_history = position_data.get('exit_plan_history', {})
        updates = exit_plan_history.get('updates', [])
        
        if len(updates) >= self.max_updates_per_position:
            logger.info(f"Position {position_id} max update limit reached")
            return False, "Max update limit reached"
        
        if updates:
            last_update_time = datetime.fromisoformat(updates[-1]['timestamp'])
            seconds_since_last = (datetime.utcnow() - last_update_time).total_seconds()
            
            if seconds_since_last < self.min_update_interval_seconds:
                logger.debug(
                    f"Position {position_id} updated {seconds_since_last:.0f}s ago, "
                    f"min interval: {self.min_update_interval_seconds}s"
                )
                return False, "Too soon since last update"
        
        volatility_change = abs(
            market_data.get('current_volatility', 0) - 
            position_data.get('initial_volatility', 0)
        )
        
        price_change_pct = abs(
            (market_data.get('current_price', 0) - position_data.get('entry_price', 1)) /
            position_data.get('entry_price', 1)
        )
        
        if volatility_change > 0.1:
            return True, f"Volatility changed by {volatility_change:.2f}"
        
        if price_change_pct > 0.01:
            return True, f"Price changed by {price_change_pct*100:.2f}%"
        
        position_age_minutes = (
            datetime.utcnow() - position_data.get('timestamp', datetime.utcnow())
        ).total_seconds() / 60
        
        if position_age_minutes > 30 and len(updates) == 0:
            return True, "Position older than 30 minutes, no updates yet"
        
        return False, "No significant changes"
    
    def prepare_glm_context_for_update(
        self,
        position_data: Dict,
        market_data: Dict
    ) -> str:
        """GLM'e gönderilecek exit plan review bağlamını hazırla"""
        
        entry_price = position_data['entry_price']
        current_price = market_data['current_price']
        position_type = position_data['position_side']
        current_exit_plan = position_data['exit_plan']
        
        position_age_minutes = (
            datetime.utcnow() - position_data['timestamp']
        ).total_seconds() / 60
        
        volatility_change = (
            market_data.get('current_volatility', 0) - 
            position_data.get('initial_volatility', 0)
        )
        
        price_change_pct = ((current_price - entry_price) / entry_price) * 100
        
        updates_history = position_data.get('exit_plan_history', {}).get('updates', [])
        history_text = ""
        if updates_history:
            history_text = "Past Exit Plan Updates:\n"
            for idx, update in enumerate(updates_history[-3:], 1):
                minutes_ago = (
                    datetime.utcnow() - datetime.fromisoformat(update['timestamp'])
                ).total_seconds() / 60
                history_text += f"  - {minutes_ago:.0f} min ago: {update['trigger']} (reason: {update.get('reasoning', 'N/A')})\n"
        else:
            history_text = "Past Exit Plan Updates:\n  - No updates yet\n"
        
        invalidation_rules = self._get_invalidation_rules(position_type)
        
        context = f"""
Current Position:
  Entry Price: ${entry_price:,.2f}
  Position Type: {position_type}
  Position Age: {position_age_minutes:.0f} minutes
  Current Exit Plan:
    - Profit Target: ${current_exit_plan.get('profit_target', 0):,.2f}
    - Stop Loss: ${current_exit_plan.get('stop_loss', 0):,.2f}
    - Invalidation: "{current_exit_plan.get('invalidation_condition', 'N/A')}"

Market Changes Since Position Open:
  - Volatility: {position_data.get('initial_volatility', 0):.2f} → {market_data.get('current_volatility', 0):.2f} ({volatility_change:+.2f})
  - Trend Strength: {market_data.get('trend_strength', 0):.2f}
  - RSI: {market_data.get('rsi', 0):.0f}
  - Current Price: ${current_price:,.2f} ({price_change_pct:+.2f}%)

{history_text}

{invalidation_rules}

TASK: Dinamik formül çarpanlarını belirle ve yeni exit plan öner.
UYARI: Invalidation condition'ın konumu MUTLAKA yukarıdaki kurallara uymalı!

Response Format (STRICT JSON):
{{
  "update_needed": true/false,
  "reasoning": "Volatilite arttı, SL genişletilmeli...",
  "multipliers": {{
    "volatility_multiplier": 1.6,
    "trend_multiplier": 1.3,
    "momentum_multiplier": 1.2,
    "age_multiplier": 1.1
  }},
  "new_exit_plan": {{
    "stop_loss": 101000,
    "profit_target": 114500,
    "invalidation_condition": "If price closes below 104000 on 5m candle"
  }}
}}
"""
        return context
    
    def _get_invalidation_rules(self, position_type: str) -> str:
        """Invalidation kurallarını döndür"""
        
        if position_type == "LONG":
            return """
⚠️ CRITICAL INVALIDATION CONDITION RULES:

LONG Pozisyon için:
  ✅ Invalidation Condition MUTLAKA Stop Loss'un ÜSTÜNDE olmalı
  ✅ Mantık: Invalidation erken uyarı sistemidir, SL'den önce tetiklenmeli
  ✅ Doğru sıralama: Entry > Invalidation > Stop Loss
  ✅ Örnek: Entry=$110,000, SL=$105,000 → Invalidation=$107,000 ✅
  ❌ YANLIŞ: Entry=$110,000, SL=$105,000 → Invalidation=$104,000 ❌

Invalidation Condition Formülü:
  - LONG: Invalidation = Entry - (Entry - SL) * 0.4  (SL ile Entry arasında %40 noktada)
"""
        else:
            return """
⚠️ CRITICAL INVALIDATION CONDITION RULES:

SHORT Pozisyon için:
  ✅ Invalidation Condition MUTLAKA Stop Loss'un ALTINDA olmalı
  ✅ Mantık: Invalidation erken uyarı sistemidir, SL'den önce tetiklenmeli
  ✅ Doğru sıralama: Stop Loss > Invalidation > Entry
  ✅ Örnek: Entry=$110,000, SL=$115,000 → Invalidation=$113,000 ✅
  ❌ YANLIŞ: Entry=$110,000, SL=$115,000 → Invalidation=$116,000 ❌

Invalidation Condition Formülü:
  - SHORT: Invalidation = Entry + (SL - Entry) * 0.4  (Entry ile SL arasında %40 noktada)
"""
    
    def request_glm_exit_plan_update(
        self,
        context: str,
        position_id: str
    ) -> Optional[Dict]:
        """GLM'den exit plan güncellemesi iste"""
        
        messages = [
            {
                "role": "system",
                "content": "You are a professional trading risk manager. Analyze position and market data to update exit plans dynamically."
            },
            {
                "role": "user",
                "content": context
            }
        ]
        
        try:
            response = self.glm_client.request(messages, trace_id=f"exit_update_{position_id}")
            
            if not response or 'choices' not in response:
                logger.error(f"Invalid GLM response for position {position_id}")
                return None
            
            content = response['choices'][0]['message']['content']
            
            json_match = content
            if '```json' in content:
                json_match = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                json_match = content.split('```')[1].split('```')[0].strip()
            
            data = json.loads(json_match)
            
            if not data.get('update_needed'):
                logger.info(f"GLM decided no update needed for {position_id}: {data.get('reasoning')}")
                return None
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse GLM JSON response: {e}")
            return None
        except Exception as e:
            logger.error(f"GLM request failed for position {position_id}: {e}", exc_info=True)
            return None
    
    def apply_exit_plan_update(
        self,
        position_id: str,
        new_exit_plan: Dict,
        trigger: str,
        reasoning: str
    ) -> bool:
        """Exit plan güncellemesini database'e uygula"""
        
        try:
            trade = self.session.query(Trade).filter_by(
                position_id=position_id,
                close_price=None
            ).first()
            
            if not trade:
                logger.error(f"Trade not found for position {position_id}")
                return False
            
            old_exit_plan = trade.exit_plan or {}
            
            is_valid, error_msg = validate_exit_plan_update(
                old_plan=old_exit_plan,
                new_plan=new_exit_plan,
                entry_price=trade.price,
                position_side=trade.position_side
            )
            
            if not is_valid:
                logger.warning(
                    f"Exit plan validation failed for {position_id}: {error_msg}"
                )
                return False
            
            if not trade.exit_plan_history:
                trade.exit_plan_history = {"updates": []}
            
            update_record = {
                "timestamp": datetime.utcnow().isoformat(),
                "trigger": trigger,
                "old_exit_plan": old_exit_plan,
                "new_exit_plan": new_exit_plan,
                "reasoning": reasoning
            }
            
            trade.exit_plan_history['updates'].append(update_record)
            
            trade.exit_plan = new_exit_plan
            
            self.session.commit()
            
            logger.info(
                f"Exit plan updated for {position_id} | "
                f"old_sl={old_exit_plan.get('stop_loss'):.2f} new_sl={new_exit_plan.get('stop_loss'):.2f} | "
                f"old_tp={old_exit_plan.get('profit_target'):.2f} new_tp={new_exit_plan.get('profit_target'):.2f} | "
                f"reason={trigger}"
            )
            
            # Enhanced logging: Güncellenen değerleri detaylı göster
            logger.info(
                "✅ Exit plan committed to DB | position_id=%s | "
                "new_sl=%.2f new_tp=%.2f new_inv='%s'",
                position_id,
                new_exit_plan.get('stop_loss'),
                new_exit_plan.get('profit_target'),
                new_exit_plan.get('invalidation_condition', 'N/A')[:50]
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply exit plan update for {position_id}: {e}", exc_info=True)
            self.session.rollback()
            return False
    
    def process_position_update(
        self,
        position_data: Dict,
        market_data: Dict
    ) -> Optional[Dict]:
        """Pozisyon için exit plan güncellemesi yap (ana orchestrator)"""
        
        position_id = position_data.get('position_id')
        
        should_update, reason = self.evaluate_update_necessity(position_data, market_data)
        
        if not should_update:
            logger.debug(f"Position {position_id} update skipped: {reason}")
            return None
        
        logger.info(f"Position {position_id} update evaluation: {reason}")
        
        context = self.prepare_glm_context_for_update(position_data, market_data)
        
        glm_response = self.request_glm_exit_plan_update(context, position_id)
        
        if not glm_response:
            return None
        
        new_exit_plan = glm_response.get('new_exit_plan')
        reasoning = glm_response.get('reasoning', 'GLM decision')
        
        success = self.apply_exit_plan_update(
            position_id=position_id,
            new_exit_plan=new_exit_plan,
            trigger=reason,
            reasoning=reasoning
        )
        
        if success:
            return {
                "position_id": position_id,
                "old_exit_plan": position_data['exit_plan'],
                "new_exit_plan": new_exit_plan,
                "reasoning": reasoning
            }
        
        return None
