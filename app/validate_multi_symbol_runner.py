import sys
import os
from unittest.mock import MagicMock

# Mock missing modules
sys.modules["influxdb_client"] = MagicMock()
sys.modules["influxdb_client.rest"] = MagicMock()
sys.modules["influxdb_client.client.write_api"] = MagicMock()
sys.modules["pydantic_settings"] = MagicMock()
sys.modules["pydantic"] = MagicMock()
sys.modules["psycopg2"] = MagicMock()
sys.modules["httpx"] = MagicMock()
sys.modules["joblib"] = MagicMock()
sys.modules["sklearn"] = MagicMock()
sys.modules["sklearn.ensemble"] = MagicMock()
sys.modules["sklearn.metrics"] = MagicMock()
sys.modules["sklearn.model_selection"] = MagicMock()

# Mock redis to prevent connection attempts during import
sys.modules["app.utils.redis"] = MagicMock()

# Mock settings to handle module-level usage
mock_settings = MagicMock()
mock_settings.get_settings.return_value.log_level = "INFO"
mock_settings.get_settings.return_value.use_nof1_style = False
mock_settings.get_settings.return_value.enable_position_monitor = True
mock_settings.get_settings.return_value.position_monitor_interval_seconds = 180
sys.modules["app.config.settings"] = mock_settings

sys.path.append(os.getcwd())

from unittest.mock import patch
from app.orchestrator.runtime import AutomatedRunner

def test_multi_symbol_initialization():
    print("Testing multi-symbol initialization...")
    
    with patch("app.orchestrator.runtime.get_settings") as mock_settings, \
         patch("app.orchestrator.runtime.configure_logging"), \
         patch("app.orchestrator.runtime.get_logger"), \
         patch("app.orchestrator.runtime.RiskManager"), \
         patch("app.orchestrator.runtime.Executor"), \
         patch("app.orchestrator.runtime.PureDataCollector"), \
         patch("app.orchestrator.runtime.FeedbackCollector"), \
         patch("app.orchestrator.runtime.ActiveLearningRetrainer"), \
         patch("app.orchestrator.runtime.AdvancedExitMonitor"), \
         patch("app.orchestrator.runtime.CycleTimingTracker"):
        
        mock_settings.return_value.use_nof1_style = False
        
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        runner = AutomatedRunner(symbols=symbols)
        
        # Check if components are initialized for each symbol
        assert len(runner._symbols) == 3, f"Expected 3 symbols, got {len(runner._symbols)}"
        assert "BTCUSDT" in runner._symbols
        assert "ETHUSDT" in runner._symbols
        assert "SOLUSDT" in runner._symbols
        
        assert len(runner._agents) == 3
        assert len(runner._executors) == 3
        assert len(runner._risk_managers) == 3
        assert len(runner._advanced_exit_monitors) == 3
        assert len(runner._timing_trackers) == 3
        
        print("✅ Initialization checks passed!")

if __name__ == "__main__":
    try:
        test_multi_symbol_initialization()
    except Exception as e:
        print(f"❌ Test failed: {e}")
        sys.exit(1)
