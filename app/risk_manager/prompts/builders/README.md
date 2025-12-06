# Prompt Builder Modules

Modular components for building GLM prompts in NOF1.AI trading system.

## Architecture

The prompt builder uses an **Orchestrator Pattern**:
- `Nof1PromptBuilder`: Main orchestrator, coordinates all builders
- `NotificationHandler`: Redis notification management
- `ExitPlanCalculator`: Risk parameters and exit plan calculation
- `MarketStateBuilder`: Market state and technical analysis sections
- `AccountInfoBuilder`: Account information and performance feedback
- `EnhancedFeaturesBuilder`: Advanced analysis (volume, funding, etc.)

## Design Principles

1. **Single Responsibility**: Each builder handles one prompt section
2. **Dependency Injection**: Dependencies passed via constructor
3. **Explicit State**: No hidden global state, state passed as parameters
4. **Graceful Degradation**: Optional features degrade gracefully if unavailable

## Module Overview

### NotificationHandler (`notification_handler.py`)
**Lines:** ~188  
**Responsibilities:**
- Check Redis for position close notifications
- Validate notifications with HMAC signature
- Format notifications for prompt injection

**Security Features:**
- JSON parsing with error handling
- HMAC signature verification
- Pydantic schema validation
- Timestamp freshness check
- Prompt injection sanitization

### ExitPlanCalculator (`exit_plan_calculator.py`)
**Lines:** ~569  
**Responsibilities:**
- Volatility regime classification (low/medium/high/extreme)
- Stop loss/take profit distance bounds
- Leverage cap calculation
- Risk/reward ratio requirements
- BTC correlation context fetching
- Complete exit plan generation

**Design:**
- Stateless: All state passed as parameters
- Asset-agnostic: Uses relative volatility metrics
- Performance-aware: Adjusts parameters based on trading performance

### MarketStateBuilder (`market_state_builder.py`)
**Lines:** ~497  
**Responsibilities:**
- Build market state section (volatility, key levels, trend analysis)
- Build pre-calculated section (MTF structure, momentum vectors, proximity to levels)
- Generate contextual narrative (no judgments, raw data only)

**Design:**
- Volume analyzer optional (degraded mode support)
- HTF analysis optional
- No scoring/judgment - raw data only (GLM freedom)

### AccountInfoBuilder (`account_info_builder.py`)
**Lines:** ~366  
**Responsibilities:**
- Build account information section (equity, positions, PnL)
- Build performance feedback section (win rate, streak, trade history)
- Inject position close notifications

**Design:**
- Win rate display conditional (>=3 trades)
- Fee breakeven calculation uses TradingConfig
- Position status carefully formatted (LONG/SHORT/None)

### EnhancedFeaturesBuilder (`enhanced_features_builder.py`)
**Lines:** ~517  
**Responsibilities:**
- Build enhanced features section (volume, funding, liquidation, ADX)
- Build TP/Hold summary
- Session info, VWAP levels, drawdown data, correlation risk

**Design:**
- Enhanced features gating (returns empty if disabled)
- All analyzers optional (graceful degradation)
- CVD interpretation position-aware
- Active glossary context injection (mutable list)

### Nof1PromptBuilder (Orchestrator) (`nof1_prompt_builder.py`)
**Lines:** ~870 (reduced from 2,167)  
**Responsibilities:**
- Coordinate all builders
- Manage state (volatility, performance tracking)
- Provide public API (build_prompt, calculate_exit_plan)
- Initialize analyzers and builders

**Public API:**
- `build_prompt(market_data, portfolio_metrics, htf_analysis)` → str
- `calculate_exit_plan(signal, entry_price, historical_arrays)` → Dict
- Getter methods for enhanced features (position_sizer, drawdown_manager, etc.)

## Usage

```python
from app.risk_manager.nof1_prompt_builder import Nof1PromptBuilder

# Initialize builder (automatically initializes all sub-builders)
builder = Nof1PromptBuilder()

# Build prompt
prompt = builder.build_prompt(
    raw_market_data=market_data,
    portfolio_metrics=portfolio,
    htf_analysis=htf_data  # Optional
)

# Calculate exit plan
exit_plan = builder.calculate_exit_plan(
    signal="BUY",
    entry_price=100.0,
    historical_arrays=historical_data
)
```

## State Management

Performance state (consecutive_losses, win_rate, history) is managed by the orchestrator
and passed to builders as parameters. This enables:
- **Testability**: Easy mocking
- **Thread safety**: No shared mutable state
- **Clear data flow**: Explicit parameter passing

Volatility state (volatility, ATR, ratios) is calculated once in build_prompt() and 
passed to builders as a dictionary.

## File Structure

```
prompts/
├── builders/
│   ├── __init__.py                      # Builder exports
│   ├── notification_handler.py          # Redis notifications (~188 lines)
│   ├── exit_plan_calculator.py          # Risk calculations (~569 lines)
│   ├── market_state_builder.py          # Market analysis (~497 lines)
│   ├── account_info_builder.py          # Account info (~366 lines)
│   └── enhanced_features_builder.py     # Enhanced features (~517 lines)
├── data_validation.py                   # HMAC validation, sanitization
├── indicator_interpretation.py          # CVD, RSI, Funding/OI context
├── market_analysis.py                   # MTF alignment, conflicts
├── metrics_calculator.py                # Volatility, slope, divergence
├── models.py                            # Pydantic models
├── risk_parameters.py                   # Risk calculator
├── templates.py                         # Few-shot training, glossary
├── volatility_analysis.py               # Volatility analyzer
└── feature_analyzers.py                 # Feature check functions
```

## Testing

Each builder has dedicated unit tests (planned):
```bash
pytest tests/risk_manager/prompts/builders/test_notification_handler.py -v
pytest tests/risk_manager/prompts/builders/test_exit_plan_calculator.py -v
pytest tests/risk_manager/prompts/builders/test_market_state_builder.py -v
pytest tests/risk_manager/prompts/builders/test_account_info_builder.py -v
pytest tests/risk_manager/prompts/builders/test_enhanced_features_builder.py -v
```

## Migration from Monolithic to Modular

### Before (Monolithic)
```
nof1_prompt_builder.py: 2,167 lines
- All logic in one file
- Hard to test
- Hard to maintain
- Difficult to extend
```

### After (Modular)
```
nof1_prompt_builder.py: 870 lines (Orchestrator)
prompts/builders/: 5 modules, 2,137 lines total
- Each module single responsibility
- Easy to test in isolation
- Easy to maintain
- Easy to extend with new builders
```

### Benefits
- ✅ Modular architecture (each file <600 lines)
- ✅ Testability ↑↑ (isolated unit tests)
- ✅ Code readability ↑↑ (clear separation)
- ✅ Maintenance cost ↓ (changes are isolated)
- ✅ Backward compatible (public API unchanged)
- ✅ Token count unchanged (only organization improved)

## Security Features

All builders maintain security best practices:
- HMAC signature verification (NotificationHandler)
- Input sanitization (prompt injection prevention)
- Division by zero protection (ExitPlanCalculator)
- Safe value extraction (null/undefined handling)
- Redis connection error handling

## Extensibility

Adding a new builder is simple:

1. Create new module in `prompts/builders/`
2. Implement class with `build()` method
3. Add to `builders/__init__.py`
4. Update `prompts/__init__.py`
5. Initialize in `Nof1PromptBuilder.__init__()`
6. Call in `build_prompt()` method

Example:
```python
# prompts/builders/my_new_builder.py
class MyNewBuilder:
    def __init__(self, settings, analyzer):
        self._settings = settings
        self._analyzer = analyzer
    
    def build(self, data: Dict) -> str:
        # Build section
        return "formatted output"
```

---

## Version History

### v1.0 (Modular Refactoring)
- Split monolithic file into 6 modules
- Introduced orchestrator pattern
- 60% reduction in orchestrator file size
- Backward compatible with existing code

---

For questions or issues, contact the trading system team.
