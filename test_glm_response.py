#!/usr/bin/env python3
"""
Test GLM Response Parsing
Test if the system correctly parses NOF1.AI format responses
"""

import json

from app.agents.short_term import PureDataCollector
from app.risk_manager.manager import RiskManager


def test_glm_response_parsing():
    """Test GLM response parsing with sample responses"""
    
    print('='*60)
    print('🧪 GLM RESPONSE PARSING TEST')
    print('='*60)
    print()
    
    # Create RiskManager
    risk_manager = RiskManager()
    
    # Sample GLM responses to test
    test_responses = [
        {
            "name": "Valid HOLD Response",
            "response": {
                "choices": [{
                    "message": {
                        "content": '''{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "HOLD",
      "quantity": 0.0,
      "profit_target": 0.0,
      "stop_loss": 0.0,
      "invalidation_condition": "N/A",
      "leverage": 1,
      "confidence": 0.8,
      "risk_usd": 0.0
    },
    "justification": "Market showing mixed signals, better to wait."
  }
}'''
                    }
                }]
            }
        },
        {
            "name": "Valid BUY Response", 
            "response": {
                "choices": [{
                    "message": {
                        "content": '''{
  "BTCUSDT": {
    "trade_signal_args": {
      "coin": "BTCUSDT",
      "signal": "BUY",
      "quantity": 0.05,
      "profit_target": 115000.0,
      "stop_loss": 105000.0,
      "invalidation_condition": "If price closes below 105000 on 30m candle",
      "leverage": 10,
      "confidence": 0.75,
      "risk_usd": 500.0
    },
    "justification": "RSI oversold at 30, good risk/reward for long position."
  }
}'''
                    }
                }]
            }
        },
        {
            "name": "Invalid Format - Missing BTCUSDT",
            "response": {
                "choices": [{
                    "message": {
                        "content": '''{
  "BTC": {
    "trade_signal_args": {
      "coin": "BTC",
      "signal": "BUY",
      "quantity": 0.05
    }
  }
}'''
                    }
                }]
            }
        },
        {
            "name": "Invalid Format - No JSON",
            "response": {
                "choices": [{
                    "message": {
                        "content": "I think we should hold the position because the market is uncertain."
                    }
                }]
            }
        }
    ]
    
    # Test portfolio metrics
    portfolio_metrics = {
        "equity": 10000.0,
        "available_cash": 10000.0,
        "position": 0.0,
        "price": 107800.0
    }
    
    print('📋 Testing GLM Response Parsing...')
    print()
    
    for i, test_case in enumerate(test_responses, 1):
        print(f'{i}. {test_case["name"]}')
        print('-' * 40)
        
        try:
            decision = risk_manager._parse_nof1_response(test_case["response"], portfolio_metrics)
            
            print(f'✅ Parsed Successfully:')
            print(f'   Action: {decision.action}')
            print(f'   Amount: {decision.amount:.4f}')
            print(f'   Leverage: {decision.leverage:.1f}')
            print(f'   Confidence: {decision.glm_confidence:.1f}%')
            print(f'   Reasoning: {decision.reasoning}')
            
        except Exception as e:
            print(f'❌ Parse Error: {e}')
        
        print()
    
    print('='*60)
    print('🎯 GLM Response Parsing Test Complete!')
    print('='*60)
    print()
    print('💡 If all tests pass, the system is ready for GLM responses!')
    print()

if __name__ == "__main__":
    test_glm_response_parsing()
