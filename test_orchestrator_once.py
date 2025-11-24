#!/usr/bin/env python3
"""
Test orchestrator for one cycle to debug GLM response format
"""

import asyncio
import logging
import os
import sys

# Add the project root to Python path
sys.path.insert(0, '/root/trading')

# Configure logging to see all output
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_single_cycle():
    """Run a single trading cycle to debug GLM response"""
    
    try:
        # Import here to avoid path issues
        from app.agents.short_term import PureDataCollector
        from app.executor.executor import Executor
        from app.orchestrator.runtime import AutomatedRunner
        from app.risk_manager.manager import RiskManager
        
        print("="*60)
        print("🧪 SINGLE CYCLE TEST - Debug GLM Response Format")
        print("="*60)
        
        # Create components
        agent = PureDataCollector(symbol="BTCUSDT")
        executor = Executor(symbol="BTCUSDT")
        risk_manager = RiskManager()
        
        # Create runner with 3-minute cycle (nof1.ai style)
        runner = AutomatedRunner(
            symbol="BTCUSDT",
            interval="15min",
            cycle_seconds=180,  # 3 minutes for nof1.ai style
            agent=agent,
            executor=executor,
            risk_manager=risk_manager,
            enable_feedback_collector=False,  # Disable for test
            enable_daily_retraining=False,     # Disable for test
        )
        
        print("\n📊 Running single trading cycle...")
        print("Watch for '🔍 Raw GLM response' to see what GLM returns")
        print("="*60)
        
        # Run just one cycle
        await runner._run_cycle()
        
        print("="*60)
        print("✅ Single cycle test complete!")
        print("Check the logs above for GLM response format issues")
        print("="*60)
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_single_cycle())
