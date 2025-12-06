"""
Prompt Template Constants and Schema Definitions

This module contains all string templates, JSON schemas, and glossary definitions
used in NOF1.AI-style prompt building. Extracted for token optimization.
"""

from typing import Dict

# =============================================================================
# FEW-SHOT TRAINING & HARD RULES (Currently disabled for GLM freedom)
# =============================================================================

FEW_SHOT_TRAINING = ''

HARD_RULES_BLOCK = ""

# =============================================================================
# GLOSSARY DEFINITIONS
# =============================================================================

GLOSSARY_TERMS: Dict[str, str] = {
    "PASSIVE_ABSORPTION": """• PASSIVE_ABSORPTION (Gizli Alis):
   - Gorunum: Fiyat destek seviyesinde sabit, CVD dusuyor
   - Anlam: Buyuk oyuncular limit emirlerle satisi karsiliyor
   - ⚠️ Acik SHORT pozisyon varsa, sicrama riski""",
    
    "PASSIVE_DISTRIBUTION": """• PASSIVE_DISTRIBUTION (Gizli Satis):
   - Gorunum: Fiyat direnc seviyesinde sabit, CVD yukseliyor
   - Anlam: Buyuk oyuncular satiyor
   - ⚠️ Acik LONG pozisyon varsa, dusus riski""",
    
    "TREND_CONFIRMED": """• TREND_CONFIRMED:
   - Gorunum: CVD ve fiyat ayni yonde hareket ediyor
   - Anlam: Trend saglikli, hacim fiyati destekliyor""",
    
    "BEARISH_DIVERGENCE": """• BEARISH_DIVERGENCE:
   - Gorunum: Fiyat yukseliyor ama CVD dusuyor
   - Anlam: Yukselis zayifliyor, reversal riski""",
    
    "BULLISH_DIVERGENCE": """• BULLISH_DIVERGENCE:
   - Gorunum: Fiyat dusuyor ama CVD yukseliyor
   - Anlam: Dusus zayifliyor, reversal riski""",
    
    "AGGRESSIVE_SHORTING": """• AGGRESSIVE_SHORTING:
   - Gorunum: OI artiyor + Fiyat dusuyor
   - Anlam: Yeni short pozisyonlar aciliyor""",
    
    "LONG_LIQUIDATION": """• LONG_LIQUIDATION:
   - Gorunum: OI azaliyor + Fiyat dusuyor
   - Anlam: Long pozisyonlar likide ediliyor, cascade riski""",
    
    "SHORT_LIQUIDATION": """• SHORT_LIQUIDATION:
   - Gorunum: OI azaliyor + Fiyat yukseliyor
   - Anlam: Short pozisyonlar likide ediliyor, squeeze riski""",
    
    "NEW_LONGS": """• NEW_LONGS:
   - Gorunum: OI artiyor + Fiyat yukseliyor
   - Anlam: Yeni long pozisyonlar aciliyor""",
}

RSI_CONTEXT_RULES = """
RSI CONTEXT RULES:
   - ADX > 30 (Strong Trend): RSI 70+ = Momentum devami (satis DEGIL)
   - ADX < 20 (Range Market): RSI 70+ = Overbought, RSI 30- = Oversold"""

# =============================================================================
# INSTRUCTION TEMPLATES
# =============================================================================

def build_position_active_instructions_template(
    symbol: str,
    position_type: str,
    logic_gates_section: str,
    glossary_section: str
) -> str:
    """Template for instructions when position is open"""
    return f"""
================================================================================
GOREV: Mevcut pozisyonu degerlendir (HOLD veya CLOSE) - RED TEAM MODU
================================================================================

⚠️ POZİSYON DURUMU: {position_type} POZİSYON AÇIK

KULLANILABILIR SİNYALLER:
- HOLD: Pozisyonu koru, degisiklik yapma
- CLOSE: Tum pozisyonu kapat

❌ BUY/SELL VERME - Executor tarafindan BLOCKED edilecek!
{logic_gates_section}
ANALIZ WORKFLOW (Thesis/Antithesis/Synthesis):
1. PARSE: Input verilerini oku (Price, EMA, RSI, Funding, CVD) + Logic Gates sonuclarini incele
2. THESIS: Pozisyonun DEVAM etmesi icin faktorler - trend hala {position_type} yonunde mi?
3. ANTITHESIS: Pozisyonu KAPATMAK icin nedenler - Logic Gates riskleri + tersine donme sinyalleri
4. SYNTHESIS: Thesis vs Antithesis tart, Logic Gates sonucunu agirlikli degerlendir

CONFIDENCE RUBRIC (POZİSYON AÇIKKEN):
- Logic Gates Risk > 2 ise → CLOSE one (confidence 0-40 arasi)
- 0-40: Trend tersine donuyor veya stop seviyesine yaklasim → CLOSE
- 41-100: Trend devam ediyor veya belirsizlik → HOLD (pozisyonu koru)

CIKTI FORMATI (JSON):
```json
{{
  "{symbol}": {{
    "data_analysis": {{
      "adx_interpretation": "ADX degeri ve trend gucu yorumu",
      "volume_assessment": "Volume Ratio degerlendirmesi",
      "funding_view": "Funding rate yorumu",
      "timeframe_alignment": "1D/4H/1H trend uyumu sentezi (ornek: 'Tum TF bearish hizali')",
      "futures_deep_analysis": "OI degisimi + Funding + L/S ratio birlikte yorumu",
      "volatility_impact": "Volatilite rejiminin pozisyon boyutu ve SL uzerindeki etkisi"
    }},
    "market_mechanics": {{
      "volume_quality": "Hacim durumu aciklamasi",
      "oi_interpretation": "OI analiz sonucu",
      "vwap_status": "VWAP konumu",
      "liquidity_risk": "Likidasyon riski"
    }},
    "technical_blind_spots": {{
      "session_anomaly": "Seans anomalisi durumu",
      "data_conflict": "Veri tutarsizligi durumu",
      "micro_divergence": "1m divergence durumu"
    }},
    "thought_process": {{
      "thesis": "Pozisyonun devami icin destekleyici faktorler",
      "antithesis": "Logic Gates riskleri + kapatma nedenleri",
      "synthesis_verdict": "Thesis vs Antithesis degerlendirmesi"
    }},
    "signal": "HOLD" | "CLOSE",
    "confidence": <0-100>,
    "reasoning": "Detayli analiz aciklamasi (TURKCE yazilmali)"
  }}
}}
```

ONEMLI KURALLAR:
- "data_analysis" alani MUTLAKA doldurulmali (ADX, Volume, Funding yorumu)
- "timeframe_alignment" alani MUTLAKA doldurulmali (1D/4H/1H sentezi)
- "futures_deep_analysis" alani OI+Funding+L/S birlikte yorumlanmali
- "volatility_impact" alani HIGH/EXTREME rejimlerde pozisyon uzerindeki etkiyi icermeli
- "reasoning" alani MUTLAKA TURKCE yazilmalidir
- "market_mechanics" ve "technical_blind_spots" alanlarini Logic Gates sonuclarina gore doldur
- "thought_process" tum alanlarini MUTLAKA doldur
- stop_loss, take_profit YAZMA - bunlar sistem tarafindan hesaplanir
- {position_type} pozisyon icin ters sinyaller (trend donusu) veya Logic Gates riski > 2 ise CLOSE onermelisin
- HIGH/EXTREME volatilite rejiminde pozisyon riski artar, dikkatli degerlendir

{glossary_section}
"""


def build_no_position_instructions_template(
    symbol: str,
    glossary_section: str
) -> str:
    """Template for instructions when no position is open"""
    return f"""
================================================================================
GOREV: Market verilerini analiz et ve sinyal ver
================================================================================

POZİSYON DURUMU: POZİSYON YOK

KULLANILABILIR SİNYALLER:
- BUY: LONG pozisyon ac
- SELL: SHORT pozisyon ac
- HOLD: Bekle, islem yapma

ANALIZ WORKFLOW (Thesis/Antithesis/Synthesis):
1. PARSE: Input verilerini oku (Price, EMA, RSI, Funding, CVD)
2. THESIS: Analiz ettigin potansiyel trade yonundeki DESTEKLEYICI faktorleri listele
   (BUY icin bullish faktorler, SELL icin bearish faktorler)
3. ANTITHESIS (Devil's Advocate): Trade ALMAMAK icin nedenler ara - karsi faktorleri listele
4. SYNTHESIS: Thesis vs Antithesis tart, risk yuksekse confidence dusur, nihai karar ver

CONFIDENCE RUBRIC (POZİSYON YOKKEN):
- 0-50: Catisma var veya trend yok → HOLD
- 51-79: Zayif setup veya kotu R:R → HOLD (overtrading onleme)
- 80-89: Guclu sinyal, trend/momentum/volume hizali → BUY/SELL
- 90-100: A+ setup, tum indikatorler uyumlu → BUY/SELL (Agresif)

CIKTI FORMATI (JSON):
```json
{{
  "{symbol}": {{
    "data_analysis": {{
      "adx_interpretation": "ADX degeri ve trend gucu yorumu (ornek: ADX 24.7 = orta trend)",
      "volume_assessment": "Volume Ratio degerlendirmesi (ornek: 0.1x = dusuk hacim riski)",
      "funding_view": "Funding rate yorumu (ornek: 0.006% = notr)",
      "timeframe_alignment": "1D/4H/1H trend uyumu sentezi (ornek: 'Tum TF bearish hizali' veya '1D notr, 4H/1H bearish = zayif sinyal')",
      "futures_deep_analysis": "OI degisimi + Funding + L/S ratio birlikte yorumu (ornek: 'OI -1.6% + L/S 3.37 = crowded long, liquidation riski')",
      "volatility_impact": "Volatilite rejiminin pozisyon boyutu ve SL uzerindeki etkisi (ornek: 'HIGH vol = kucuk pozisyon, genis SL')"
    }},
    "thought_process": {{
      "thesis": "Sinyal yonunu destekleyen faktorler (BUY icin bullish, SELL icin bearish)",
      "antithesis": "Karsi faktorler ve riskler (sinyal yonunun tersi)",
      "synthesis_verdict": "Thesis vs Antithesis degerlendirmesi sonucu nihai mantiksal sonuc"
    }},
    "signal": "BUY" | "SELL" | "HOLD",
    "confidence": <0-100>,
    "reasoning": "Detayli analiz aciklamasi (TURKCE yazilmali)"
  }}
}}
```

ONEMLI KURALLAR:
- "data_analysis" alani MUTLAKA doldurulmali (ADX, Volume, Funding yorumu)
- "timeframe_alignment" alani MUTLAKA doldurulmali (1D/4H/1H sentezi)
- "futures_deep_analysis" alani OI+Funding+L/S birlikte yorumlanmali
- "volatility_impact" alani HIGH/EXTREME rejimlerde pozisyon boyutu onerisini icermeli
- "reasoning" alani MUTLAKA TURKCE yazilmalidir
- "thought_process" tum alanlarini MUTLAKA doldur (thesis, antithesis, synthesis_verdict)
- stop_loss, take_profit, invalidation_condition YAZMA - bunlar sistem tarafindan hesaplanir
- Thesis sinyal yonuyle uyumlu olmali (BUY sinyali icin bullish thesis, SELL icin bearish thesis)
- Dusuk hacim (Volume Ratio < 0.3) varsa confidence -10 dusur
- ADX < 25 ise RSI oversold/overbought kurallarini dikkate al
- HIGH/EXTREME volatilite rejiminde confidence -15 dusur
- 2+ ardisik kayip (streak) varsa confidence -5 uygula
- Fiyat destek/dirence %0.5'ten yakinsa (Distance < 0.5%) kirilma/bounce teyidi bekle veya confidence -10 uygula

{glossary_section}
"""


def build_dynamic_glossary(active_contexts: list) -> str:
    """
    Build dynamic glossary with only relevant terms based on active contexts.
    
    Args:
        active_contexts: List of active glossary context keys
        
    Returns:
        Formatted glossary string
    """
    if not active_contexts:
        # No specific contexts, return minimal glossary
        return RSI_CONTEXT_RULES
    
    # Build dynamic glossary with only relevant terms
    relevant_terms = []
    for ctx in active_contexts:
        if ctx in GLOSSARY_TERMS:
            relevant_terms.append(GLOSSARY_TERMS[ctx])
    
    if not relevant_terms:
        return RSI_CONTEXT_RULES
    
    terms_text = "\n\n".join(relevant_terms)
    
    return f"""
================================================================================
AKTIF MARKET DYNAMICS (Bu analize ozel)
================================================================================

{terms_text}
{RSI_CONTEXT_RULES}
"""


__all__ = [
    "FEW_SHOT_TRAINING",
    "HARD_RULES_BLOCK",
    "GLOSSARY_TERMS",
    "RSI_CONTEXT_RULES",
    "build_position_active_instructions_template",
    "build_no_position_instructions_template",
    "build_dynamic_glossary",
]
