#!/usr/bin/env python3
"""
Test script for SELL signal JSON parsing issues.
"""
import sys
sys.path.append('/root/trading')

from app.risk_manager.manager import RiskManager, create_problematic_sell_response

def test_sell_signal_json_parsing():
    """
    Standalone test function to diagnose SELL signal JSON parsing issues.
    """
    import logging

    # Set up logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    )
    logger = logging.getLogger(__name__)

    logger.info("🧪 Starting SELL signal JSON parsing test...")

    # Create RiskManager instance
    risk_manager = RiskManager()

    # Test with problematic SELL response
    problematic_response = create_problematic_sell_response()

    logger.info("📄 Testing with response length: %d", len(problematic_response))
    logger.info("📄 Response preview: %s", problematic_response[:200] + "...")

    # Test the parsing
    result = risk_manager.test_sell_signal_parsing(problematic_response)

    logger.info("🎯 Final result: %s", result.action)
    logger.info("💰 Amount: %.4f", result.amount)
    logger.info("📊 Confidence: %.1f%%", result.glm_confidence)
    logger.info("📝 Reasoning: %s", result.reasoning)

    return result

if __name__ == "__main__":
    test_sell_signal_json_parsing()