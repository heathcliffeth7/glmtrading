from app.config.settings import get_settings


def test_settings_loads_defaults() -> None:
    settings = get_settings()
    assert settings.environment == "development"


def test_twelve_data_keys_loaded() -> None:
    keys = get_settings().twelve_data.api_keys
    assert isinstance(keys, list)
    assert len(keys) == 11
