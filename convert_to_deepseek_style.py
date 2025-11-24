#!/usr/bin/env python3
"""
DeepSeek-style prompt formatter.
Converts our multi-timeframe data to DeepSeek's array-based format.
"""

from app.agents.derivatives import DerivativesAgent
from datetime import datetime


def format_deepseek_style_prompt(agent_signal):
    """
    Format signal data in DeepSeek style:
    - Simple current values
    - Arrays (oldest → newest)
    - Clean, readable format
    """
    
    # Get historical data
    historical = agent_signal.metadata.get("historical_data", {})
    intraday_1m = historical.get("intraday_1m", {})
    main_30min = historical.get("main_30min", {})
    longterm_4h = historical.get("longterm_4h", {})
    
    # Extract current values (from reasoning or latest array)
    # For demo, we'll use last values from arrays
    
    def get_latest(data_dict, key, default=0):
        values = data_dict.get(key, [])
        return values[-1] if values else default
    
    # Current values (from main 30min)
    current_price = get_latest(main_30min, "close", 0)
    current_ema20 = get_latest(main_30min, "ema_20", 0)
    current_macd = get_latest(main_30min, "macd", 0)
    current_rsi = get_latest(main_30min, "rsi_14", 50)
    
    # Format arrays (round to reasonable precision)
    def format_array(values, decimals=2):
        return [round(v, decimals) for v in values] if values else []
    
    prompt = f"""
BTCUSDT TRADING ANALYSIS

Current time: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}

ALL OF THE PRICE OR SIGNAL DATA BELOW IS ORDERED: OLDEST → NEWEST

=== CURRENT MARKET STATE ===

current_price = {current_price:.2f}
current_ema20 = {current_ema20:.2f}
current_macd = {current_macd:.2f}
current_rsi (14 period) = {current_rsi:.2f}

In addition, here is the latest BTC futures data:

Long/Short Ratio: 2.35
Open Interest: 8339566740
Funding Rate: 0.000024

=== INTRADAY SERIES (1-minute intervals, oldest → latest) ===

Prices (last 10 minutes):
{format_array(intraday_1m.get('close', []), 2)}

EMA indicators (20-period):
{format_array(intraday_1m.get('ema_20', []), 2)}

MACD indicators:
{format_array(intraday_1m.get('macd', []), 2)}

RSI indicators (14-period):
{format_array(intraday_1m.get('rsi_14', []), 2)}

=== MAIN TIMEFRAME (30-minute intervals, oldest → latest) ===

Prices (last 5 hours):
{format_array(main_30min.get('close', []), 2)}

EMA indicators (20-period):
{format_array(main_30min.get('ema_20', []), 2)}

EMA indicators (50-period):
{format_array(main_30min.get('ema_50', []), 2)}

MACD indicators:
{format_array(main_30min.get('macd', []), 2)}

RSI indicators (14-period):
{format_array(main_30min.get('rsi_14', []), 2)}

Stochastic K:
{format_array(main_30min.get('stoch_k', []), 2)}

ATR indicators (14-period):
{format_array(main_30min.get('atr_14', []), 2)}

MFI indicators:
{format_array(main_30min.get('mfi', []), 2)}

=== LONGER-TERM CONTEXT (4-hour timeframe) ===

20-Period EMA: {get_latest(longterm_4h, 'ema_20', 0):.2f}
50-Period EMA: {get_latest(longterm_4h, 'ema_50', 0):.2f}

Current Volume: {get_latest(longterm_4h, 'volume', 0):.2f}

MACD indicators (last 40 hours):
{format_array(longterm_4h.get('macd', []), 2)}

RSI indicators (14-period):
{format_array(longterm_4h.get('rsi_14', []), 2)}

ATR indicators (14-period):
{format_array(longterm_4h.get('atr_14', []), 2)}

=== ML MODEL PREDICTION ===

Direction: {agent_signal.direction}
Confidence: {agent_signal.confidence:.2f}

=== ANALYSIS REQUIRED ===

Based on the multi-timeframe data above, please analyze:

1. MOMENTUM ANALYSIS:
   - What's the momentum direction across timeframes?
   - Are 1m, 30m, and 4h aligned or diverging?
   - Is the current momentum strengthening or weakening?

2. TREND IDENTIFICATION:
   - What's the dominant trend on each timeframe?
   - Are we in an uptrend, downtrend, or consolidation?
   - Any trend reversals forming?

3. ENTRY TIMING:
   - Is this a good entry point based on 1m data?
   - Should we wait for a pullback?
   - Is price near key support/resistance levels?

4. RISK ASSESSMENT:
   - What's the volatility level (check ATR)?
   - Are we overbought/oversold (check RSI)?
   - What's the optimal stop-loss placement?

5. FINAL DECISION:
   Should we:
   - BUY (enter long position)
   - SELL (enter short position)
   - HOLD (wait for better setup)
   
   Provide confidence level (0-1) and detailed reasoning.

Please respond in JSON format:
{{
  "karar": "BUY|SELL|HOLD",
  "miktar": 0.5,  // 0.0-1.0 (portion of capital)
  "kaldıraç": 10,  // 5-20x
  "gerekçe": "Detailed analysis in Turkish"
}}
"""
    
    return prompt


# Demo
if __name__ == "__main__":
    print("="*80)
    print("DEEPSEEK-STYLE PROMPT GENERATOR")
    print("="*80)
    print()
    
    # Generate a signal
    agent = DerivativesAgent()
    signal = agent.generate_signal()
    
    # Convert to DeepSeek style
    prompt = format_deepseek_style_prompt(signal)
    
    print(prompt)
    print()
    print("="*80)
    print("This prompt can be sent directly to GLM in DeepSeek format!")
    print("="*80)
