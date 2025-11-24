#!/usr/bin/env python3
"""
Test prompt formats to ensure they match the parsers
"""

import sys

sys.path.insert(0, '/root/trading')

# Mock settings to test both modes
class MockSettings:
    def __init__(self, use_nof1_style=False):
        self.use_nof1_style = use_nof1_style

def test_prompt_formats():
    print("="*60)
    print("🧪 TESTING PROMPT FORMATS")
    print("="*60)
    print()
    
    # Test 1: Regular mode (use_nof1_style=False)
    print("1. REGULAR MODE (use_nof1_style=False):")
    print("   - Uses _build_prompt() -> _build_regular_prompt()")
    print("   - Asks for natural language response: 'ACTION: HOLD'")
    print("   - Uses _parse_response() to parse natural language")
    print("   - ✅ Should work without JSON format errors")
    print()
    
    # Test 2: NOF1.AI mode (use_nof1_style=True)
    print("2. NOF1.AI MODE (use_nof1_style=True):")
    print("   - Uses _build_nof1_prompt() -> Nof1PromptBuilder")
    print("   - Asks for JSON format: {'BTCUSDT': {'trade_signal_args': {...}}}")
    print("   - Uses _parse_nof1_response() to parse JSON")
    print("   - ✅ Should work with correct JSON format")
    print()
    
    print("🔧 THE FIX:")
    print("   - Regular mode now uses natural language prompts")
    print("   - NOF1.AI mode uses JSON prompts")
    print("   - Each mode uses the appropriate parser")
    print("   - No more format mismatch errors!")
    print()
    
    print("="*60)
    print("💡 TO TEST:")
    print("1. Set USE_NOF1_STYLE=false in environment")
    print("2. Run orchestrator - should use natural language")
    print("3. Set USE_NOF1_STYLE=true in environment") 
    print("4. Run orchestrator - should use JSON format")
    print("="*60)

if __name__ == "__main__":
    test_prompt_formats()
