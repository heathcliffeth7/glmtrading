#!/usr/bin/env python3
"""
Test settings to check if nof1 style is enabled
"""

import sys

sys.path.insert(0, '/root/trading')

try:
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    print("="*60)
    print("🔧 CURRENT SETTINGS")
    print("="*60)
    print(f"use_nof1_style: {settings.use_nof1_style}")
    print(f"environment: {settings.environment}")
    print(f"log_level: {settings.log_level}")
    print()
    
    if settings.use_nof1_style:
        print("✅ NOF1.AI style is ENABLED")
        print("   - Using 3-minute cycles")
        print("   - Expecting JSON responses in NOF1.AI format")
        print("   - Using fixed 10x leverage")
    else:
        print("❌ NOF1.AI style is DISABLED")
        print("   - Using 15-minute cycles (default)")
        print("   - Expecting regular GLM responses")
        print("   - Using variable leverage")
    
    print("="*60)
    print()
    print("💡 If you're getting 'Invalid nof1.ai response format' error:")
    print("   1. Check if use_nof1_style should be enabled")
    print("   2. If enabled, GLM must return exact JSON format")
    print("   3. If disabled, system shouldn't parse nof1.ai format")
    print("="*60)
    
except Exception as e:
    print(f"❌ Error loading settings: {e}")
    import traceback
    traceback.print_exc()
