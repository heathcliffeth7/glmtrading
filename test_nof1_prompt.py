#!/usr/bin/env python3
"""
Test NOF1.AI Style Prompt Generation
Shows exactly what GLM receives
"""

from app.agents.short_term import PureDataCollector
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder

print('='*80)
print('NOF1.AI STYLE PROMPT GENERATION TEST')
print('='*80)
print()

# Collect raw market data
print('🔄 Collecting raw market data...')
collector = PureDataCollector(symbol="BTCUSDT")
signal = collector.generate_signal()

# Get raw data
raw_market_data = signal.metadata.get("raw_market_data", {})
htf_analysis = signal.metadata.get("htf_analysis")

# Sample portfolio metrics (FRESH START - no position)
portfolio_metrics = {
    "equity": 10000.0,
    "available_cash": 10000.0,
    "position": 0.0,  # FLAT - no position
    "entry_price": 0.0,
    "current_price": 0.0,
    "unrealized_pnl": 0.0,
    "total_pnl": 0.0,
    "leverage": 1,
    "exit_plan": {},
    "sharpe_ratio": 0.0
}

# Build NOF1.AI prompt
print('📝 Building NOF1.AI style prompt...')
print()

builder = Nof1PromptBuilder()
prompt_content = builder.build_prompt(
    raw_market_data=raw_market_data,
    portfolio_metrics=portfolio_metrics,
    htf_analysis=htf_analysis,
)

print('='*80)
print('GENERATED PROMPT (THIS IS WHAT GLM SEES):')
print('='*80)
print()
print(prompt_content)
print()
print('='*80)
print('PROMPT STATISTICS:')
print('='*80)
print(f'Total characters: {len(prompt_content):,}')
print(f'Total lines: {len(prompt_content.split(chr(10)))}')
print(f'Estimated tokens: ~{len(prompt_content.split()) * 1.3:.0f}')
print()

# Show what data was included
print('='*80)
print('INCLUDED DATA SUMMARY:')
print('='*80)
print()

current_snapshots = raw_market_data.get("current_snapshots", {})
historical_arrays = raw_market_data.get("historical_arrays", {})

print(f'📊 Timeframes included: {len(current_snapshots)}')
for tf in sorted(current_snapshots.keys()):
    snap = current_snapshots.get(tf, {})
    hist = historical_arrays.get(tf, {})
    if snap:
        print(f'  • {tf}: {len(snap)} current indicators, {len(hist)} historical arrays')

print()
print('✅ NOF1.AI Prompt Test Complete!')
print()
print('💡 This is the EXACT format GLM receives for trading decisions')
print('💡 GLM can now make fully informed decisions with all market data')
print()
