"""
Volume Analyzer - Volume-based confirmation system for trade signals.

Features:
- CVD (Cumulative Volume Delta)
- OBV (On Balance Volume)
- Volume Profile (POC, VAH, VAL)
- VWAP with standard deviation bands

Integrates with trading signals to provide volume-based confirmation.
"""

from typing import List, Dict, Tuple, Optional, Any
import logging

logger = logging.getLogger(__name__)


class VolumeAnalyzer:
    """
    Volume-based confirmation system for trade signals.

    Integrates:
    - CVD (Cumulative Volume Delta)
    - OBV (On Balance Volume)
    - Volume Profile (POC, VAH, VAL)
    - VWAP with standard deviation bands
    """

    def __init__(self, mode: str = "scalp", feature_enabled: bool = True):
        """
        Initialize VolumeAnalyzer.

        Args:
            mode: Trading mode - "scalp" or "swing"
            feature_enabled: Enable/disable the feature
        """
        self.mode = mode
        self.feature_enabled = feature_enabled
        self._poc_cache = {}  # Point of Control cache

        logger.info("VolumeAnalyzer initialized | Mode: %s | Enabled: %s", mode, feature_enabled)

    def calculate_cvd(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float]
    ) -> Tuple[List[float], str]:
        """
        Cumulative Volume Delta - Buyer vs Seller pressure.

        Logic:
        - If close is in upper half of bar -> buyer dominant
        - If close is in lower half of bar -> seller dominant

        Args:
            closes: Close prices
            highs: High prices
            lows: Low prices
            volumes: Volume values

        Returns:
            Tuple[List[float], str]: CVD series and trend direction
        """
        if not self.feature_enabled or len(closes) < 2:
            return [], "NEUTRAL"

        cvd = []
        cumulative = 0.0

        for i in range(len(closes)):
            high = highs[i]
            low = lows[i]
            close = closes[i]
            volume = volumes[i]

            # Bar range
            bar_range = high - low
            if bar_range <= 0:
                delta = 0
            else:
                # Close position: 0 = lowest, 1 = highest
                close_position = (close - low) / bar_range
                # Delta: -1 to +1, multiplied by volume
                delta = (2 * close_position - 1) * volume

            cumulative += delta
            cvd.append(cumulative)

        # Trend determination: slope of last 20 bars
        if len(cvd) >= 20:
            recent = cvd[-20:]
            slope = (recent[-1] - recent[0]) / 19

            # Normalize slope by average volume
            avg_vol = sum(volumes[-20:]) / 20
            normalized_slope = slope / avg_vol if avg_vol > 0 else 0

            if normalized_slope > 0.1:
                trend = "BULLISH"
            elif normalized_slope < -0.1:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
        else:
            trend = "NEUTRAL"

        return cvd, trend

    def calculate_obv(
        self,
        closes: List[float],
        volumes: List[float]
    ) -> Tuple[List[float], bool]:
        """
        On Balance Volume - Price-Volume confirmation check.

        Args:
            closes: Close prices
            volumes: Volume values

        Returns:
            Tuple[List[float], bool]: OBV series and divergence flag
        """
        if not self.feature_enabled or len(closes) < 2:
            return [], False

        obv = [0.0]

        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i-1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])

        # Divergence check: last 20 bars
        if len(closes) >= 20:
            price_higher = closes[-1] > closes[-20]
            obv_higher = obv[-1] > obv[-20]

            # Bearish divergence: price rising but OBV falling
            # Bullish divergence: price falling but OBV rising
            divergence = price_higher != obv_higher
        else:
            divergence = False

        return obv, divergence

    def get_obv_trend(
        self,
        closes: List[float],
        volumes: List[float],
        lookback: int = 10
    ) -> str:
        """
        OBV trend yönünü döndürür: RISING, FALLING, NEUTRAL

        Args:
            closes: Close prices
            volumes: Volume values
            lookback: Number of bars to calculate slope

        Returns:
            str: "RISING", "FALLING", "NEUTRAL", or "N/A"
        """
        obv, _ = self.calculate_obv(closes, volumes)
        if len(obv) < lookback:
            return "N/A"

        # Calculate slope of OBV over lookback period
        slope = (obv[-1] - obv[-lookback]) / lookback

        # Normalize by average volume to make threshold asset-agnostic
        avg_vol = sum(volumes[-lookback:]) / lookback if volumes else 1
        normalized = slope / avg_vol if avg_vol > 0 else 0

        if normalized > 0.05:
            return "RISING"
        elif normalized < -0.05:
            return "FALLING"
        return "NEUTRAL"

    def calculate_volume_profile(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
        num_bins: int = 50,
        lookback: int = 100
    ) -> Dict[str, Any]:
        """
        Volume Profile Analysis - Where is liquidity?

        Args:
            closes: Close prices
            highs: High prices
            lows: Low prices
            volumes: Volume values
            num_bins: Number of price bins
            lookback: Bars to look back

        Returns:
            Dict with:
            - poc: Point of Control (highest volume level)
            - vah: Value Area High (70% volume upper bound)
            - val: Value Area Low (70% volume lower bound)
            - hvn: High Volume Nodes (support/resistance)
            - lvn: Low Volume Nodes (fast movement zones)
        """
        if not self.feature_enabled:
            return {"poc": 0, "vah": 0, "val": 0, "hvn": [], "lvn": []}

        if len(closes) < lookback:
            lookback = len(closes)

        if lookback < 10:
            return {"poc": closes[-1] if closes else 0, "vah": 0, "val": 0, "hvn": [], "lvn": []}

        # Get recent data
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        recent_volumes = volumes[-lookback:]
        recent_closes = closes[-lookback:]

        # Price range
        price_min = min(recent_lows)
        price_max = max(recent_highs)
        price_range = price_max - price_min

        if price_range <= 0:
            return {"poc": recent_closes[-1], "vah": price_max, "val": price_min, "hvn": [], "lvn": []}

        # Bin size
        bin_size = price_range / num_bins

        # Volume distribution by price level
        volume_at_price = [0.0] * num_bins

        for i in range(lookback):
            high = recent_highs[i]
            low = recent_lows[i]
            vol = recent_volumes[i]

            # Which bins does this bar touch?
            low_bin = int((low - price_min) / bin_size)
            high_bin = int((high - price_min) / bin_size)

            low_bin = max(0, min(low_bin, num_bins - 1))
            high_bin = max(0, min(high_bin, num_bins - 1))

            # Distribute volume evenly across bins
            num_bins_touched = high_bin - low_bin + 1
            vol_per_bin = vol / num_bins_touched

            for b in range(low_bin, high_bin + 1):
                volume_at_price[b] += vol_per_bin

        # POC: Highest volume level
        poc_bin = volume_at_price.index(max(volume_at_price))
        poc = price_min + (poc_bin + 0.5) * bin_size

        # Value Area: 70% of volume
        total_volume = sum(volume_at_price)
        target_volume = total_volume * 0.7

        # Expand from POC
        val_bin = poc_bin
        vah_bin = poc_bin
        accumulated_volume = volume_at_price[poc_bin]

        while accumulated_volume < target_volume:
            # Which way to expand?
            vol_up = volume_at_price[vah_bin + 1] if vah_bin + 1 < num_bins else 0
            vol_down = volume_at_price[val_bin - 1] if val_bin - 1 >= 0 else 0

            if vol_up >= vol_down and vah_bin + 1 < num_bins:
                vah_bin += 1
                accumulated_volume += vol_up
            elif val_bin - 1 >= 0:
                val_bin -= 1
                accumulated_volume += vol_down
            else:
                break

        vah = price_min + (vah_bin + 1) * bin_size
        val = price_min + val_bin * bin_size

        # Detect HVN and LVN
        avg_volume = total_volume / num_bins
        hvn = []  # High Volume Nodes
        lvn = []  # Low Volume Nodes

        for i, vol in enumerate(volume_at_price):
            price_level = price_min + (i + 0.5) * bin_size
            if vol > avg_volume * 1.5:
                hvn.append(price_level)
            elif vol < avg_volume * 0.5:
                lvn.append(price_level)

        return {
            "poc": round(poc, 2),
            "vah": round(vah, 2),
            "val": round(val, 2),
            "hvn": [round(p, 2) for p in hvn[:5]],  # Top 5
            "lvn": [round(p, 2) for p in lvn[:5]],
        }

    def calculate_vwap(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
        session_length: int = 96  # 24 hours * 4 (15m bars) or adjust
    ) -> Dict[str, float]:
        """
        VWAP - Volume Weighted Average Price

        Institutional traders use VWAP as reference:
        - Price > VWAP -> Bullish bias
        - Price < VWAP -> Bearish bias

        Args:
            highs: High prices
            lows: Low prices
            closes: Close prices
            volumes: Volume values
            session_length: Bars for session

        Returns:
            Dict with VWAP and standard deviation bands
        """
        if not self.feature_enabled or len(closes) < 10:
            return {"vwap": 0, "upper_1": 0, "lower_1": 0, "upper_2": 0, "lower_2": 0}

        # Session-based VWAP
        length = min(len(closes), session_length)

        typical_prices = []
        for i in range(-length, 0):
            tp = (highs[i] + lows[i] + closes[i]) / 3
            typical_prices.append(tp)

        recent_volumes = volumes[-length:]

        # Cumulative TP*Volume and Cumulative Volume
        cum_tp_vol = sum(tp * vol for tp, vol in zip(typical_prices, recent_volumes))
        cum_vol = sum(recent_volumes)

        if cum_vol <= 0:
            return {"vwap": closes[-1], "upper_1": 0, "lower_1": 0, "upper_2": 0, "lower_2": 0}

        vwap = cum_tp_vol / cum_vol

        # Standard deviation
        squared_diffs = [(tp - vwap) ** 2 * vol for tp, vol in zip(typical_prices, recent_volumes)]
        variance = sum(squared_diffs) / cum_vol
        std = variance ** 0.5

        return {
            "vwap": round(vwap, 2),
            "upper_1": round(vwap + std, 2),
            "lower_1": round(vwap - std, 2),
            "upper_2": round(vwap + 2 * std, 2),
            "lower_2": round(vwap - 2 * std, 2),
        }

    def get_volume_confirmation(
        self,
        signal: str,  # "BUY" or "SELL"
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
        current_price: float
    ) -> Dict[str, Any]:
        """
        Trade signal volume-based confirmation score.

        Args:
            signal: Proposed signal ("BUY" or "SELL")
            closes: Close prices
            highs: High prices
            lows: Low prices
            volumes: Volume values
            current_price: Current market price

        Returns:
            Dict with:
            - confirmed: bool
            - score: 0-100
            - reasons: List[str]
            - warnings: List[str]
        """
        if not self.feature_enabled:
            return {
                "confirmed": True,
                "score": 50,
                "reasons": ["Volume analysis disabled"],
                "warnings": [],
                "feature_enabled": False,
            }

        reasons = []
        warnings = []
        score = 50  # Base score

        # 1. CVD Analysis
        cvd, cvd_trend = self.calculate_cvd(closes, highs, lows, volumes)

        if signal == "BUY":
            if cvd_trend == "BULLISH":
                score += 15
                reasons.append("CVD trend bullish - buyer dominance")
            elif cvd_trend == "BEARISH":
                score -= 20
                warnings.append("CVD bearish - sellers dominant despite BUY signal")
        else:  # SELL
            if cvd_trend == "BEARISH":
                score += 15
                reasons.append("CVD trend bearish - seller dominance")
            elif cvd_trend == "BULLISH":
                score -= 20
                warnings.append("CVD bullish - buyers dominant despite SELL signal")

        # 2. OBV Divergence Check
        obv, has_divergence = self.calculate_obv(closes, volumes)

        if has_divergence:
            score -= 15
            warnings.append("OBV divergence detected - trend reversal risk")

        # 3. Volume Profile - POC, VAH, VAL
        vp = self.calculate_volume_profile(closes, highs, lows, volumes)

        poc = vp["poc"]
        vah = vp["vah"]
        val = vp["val"]

        if signal == "BUY":
            # LONG ideal: price near POC or VAL
            if current_price <= poc * 1.005:  # Near or below POC
                score += 10
                reasons.append(f"Price near/below POC ({poc}) - good LONG entry")
            elif val > 0 and current_price <= val * 1.01:  # Near VAL
                score += 15
                reasons.append(f"Price at VAL ({val}) - strong support zone")
            elif vah > 0 and current_price >= vah:  # Above VAH
                score -= 10
                warnings.append(f"Price above VAH ({vah}) - extended, wait for pullback")
        else:  # SELL
            if current_price >= poc * 0.995:
                score += 10
                reasons.append(f"Price near/above POC ({poc}) - good SHORT entry")
            elif vah > 0 and current_price >= vah * 0.99:
                score += 15
                reasons.append(f"Price at VAH ({vah}) - strong resistance zone")
            elif val > 0 and current_price <= val:
                score -= 10
                warnings.append(f"Price below VAL ({val}) - extended, wait for bounce")

        # 4. VWAP Analysis
        vwap_data = self.calculate_vwap(highs, lows, closes, volumes)
        vwap = vwap_data["vwap"]

        if vwap > 0:
            if signal == "BUY":
                if current_price < vwap:
                    score += 10
                    reasons.append(f"Price below VWAP ({vwap}) - institutional buy zone")
                elif current_price > vwap_data["upper_2"]:
                    score -= 15
                    warnings.append(f"Price above VWAP+2s - overextended")
            else:  # SELL
                if current_price > vwap:
                    score += 10
                    reasons.append(f"Price above VWAP ({vwap}) - institutional sell zone")
                elif current_price < vwap_data["lower_2"]:
                    score -= 15
                    warnings.append(f"Price below VWAP-2s - oversold")

        # 5. Recent volume spike check
        if len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            recent_vol = volumes[-1]

            if recent_vol > avg_vol * 2:
                # Volume spike - attention!
                if signal == "BUY" and closes[-1] > closes[-2]:
                    score += 10
                    reasons.append("Volume spike on up move - strong buying")
                elif signal == "SELL" and closes[-1] < closes[-2]:
                    score += 10
                    reasons.append("Volume spike on down move - strong selling")
                else:
                    warnings.append("Volume spike against signal direction")

        # Final score clamping
        score = max(0, min(100, score))
        confirmed = score >= 60 and len(warnings) <= 1

        return {
            "confirmed": confirmed,
            "score": score,
            "reasons": reasons,
            "warnings": warnings,
            "cvd_trend": cvd_trend,
            "vwap": vwap,
            "poc": poc,
            "vah": vah,
            "val": val,
            "feature_enabled": True,
        }

    def detect_volume_spike(
        self,
        volumes: List[float],
        lookback: int = 20,
        threshold: float = 2.0
    ) -> Tuple[bool, float]:
        """
        Son bar'ın volume'u ortalamaya göre spike mi kontrol eder.

        Args:
            volumes: Volume değerleri listesi
            lookback: Ortalama hesaplamak için bakılacak bar sayısı
            threshold: Spike kabul eşiği (örn: 2.0 = 2x ortalama)

        Returns:
            (is_spike, ratio) - True if current > threshold * avg
        """
        if not self.feature_enabled or len(volumes) < lookback + 1:
            return False, 1.0

        # Son bar hariç son N bar'ın ortalaması
        avg_volume = sum(volumes[-lookback - 1:-1]) / lookback
        current_volume = volumes[-1]

        if avg_volume <= 0:
            return False, 1.0

        ratio = current_volume / avg_volume
        is_spike = ratio >= threshold

        return is_spike, round(ratio, 2)

    def get_prompt_section(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
        current_price: float
    ) -> str:
        """Generate volume analysis section for GLM prompt"""
        if not self.feature_enabled or len(volumes) < 20:
            return "VOLUME ANALYSIS: Insufficient data or disabled"

        # Get all volume metrics
        cvd, cvd_trend = self.calculate_cvd(closes, highs, lows, volumes)
        vp = self.calculate_volume_profile(closes, highs, lows, volumes)
        vwap_data = self.calculate_vwap(highs, lows, closes, volumes)
        _, has_divergence = self.calculate_obv(closes, volumes)

        lines = [
            "",
            "=" * 60,
            "VOLUME ANALYSIS (Python-computed)",
            "=" * 60,
            "",
            f"CVD Trend: {cvd_trend}",
            f"OBV Divergence: {'YES - Reversal Risk!' if has_divergence else 'No'}",
            "",
            "Volume Profile:",
            f"  - POC (High Vol): ${vp['poc']:,.2f}",
            f"  - VAH (Value High): ${vp['vah']:,.2f}",
            f"  - VAL (Value Low): ${vp['val']:,.2f}",
            "",
            f"VWAP: ${vwap_data['vwap']:,.2f}",
            f"  - Upper 1s: ${vwap_data['upper_1']:,.2f}",
            f"  - Lower 1s: ${vwap_data['lower_1']:,.2f}",
            "",
        ]

        # Position relative to key levels
        if vwap_data['vwap'] > 0:
            if current_price > vwap_data['vwap']:
                diff_pct = ((current_price/vwap_data['vwap'])-1)*100
                lines.append(f"Price ABOVE VWAP (+{diff_pct:.2f}%)")
            else:
                diff_pct = ((current_price/vwap_data['vwap'])-1)*100
                lines.append(f"Price BELOW VWAP ({diff_pct:.2f}%)")

        if vp['vah'] > 0 and vp['val'] > 0:
            if current_price > vp['vah']:
                lines.append("Price ABOVE Value Area - Extended")
            elif current_price < vp['val']:
                lines.append("Price BELOW Value Area - Oversold")
            else:
                lines.append("Price INSIDE Value Area - Fair Value")

        return "\n".join(lines)


# Usage example
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Test with sample data
    analyzer = VolumeAnalyzer()

    # Generate sample data
    import random
    random.seed(42)

    closes = [100000 + random.uniform(-1000, 1000) for _ in range(100)]
    highs = [c + random.uniform(100, 500) for c in closes]
    lows = [c - random.uniform(100, 500) for c in closes]
    volumes = [random.uniform(1000, 5000) for _ in range(100)]

    # Calculate CVD
    cvd, trend = analyzer.calculate_cvd(closes, highs, lows, volumes)
    print(f"CVD Trend: {trend}")
    print(f"Last CVD value: {cvd[-1]:.2f}")

    # Calculate Volume Profile
    vp = analyzer.calculate_volume_profile(closes, highs, lows, volumes)
    print(f"\nVolume Profile:")
    print(f"  POC: ${vp['poc']:,.2f}")
    print(f"  VAH: ${vp['vah']:,.2f}")
    print(f"  VAL: ${vp['val']:,.2f}")

    # Get volume confirmation
    current = closes[-1]
    confirmation = analyzer.get_volume_confirmation("BUY", closes, highs, lows, volumes, current)
    print(f"\nVolume Confirmation for BUY:")
    print(f"  Confirmed: {confirmation['confirmed']}")
    print(f"  Score: {confirmation['score']}")
    print(f"  Reasons: {confirmation['reasons']}")
    print(f"  Warnings: {confirmation['warnings']}")

    # Get prompt section
    print("\n" + analyzer.get_prompt_section(closes, highs, lows, volumes, current))
