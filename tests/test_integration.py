from pathlib import Path

import pytest
import yaml

from infra import dummy_feed


@pytest.mark.integration
def test_docker_compose_definition():
    compose_file = Path(__file__).resolve().parents[1] / "infra/docker-compose.test.yml"
    assert compose_file.exists(), "Compose file is missing"

    with compose_file.open() as fp:
        config = yaml.safe_load(fp)

    services = config.get("services", {})
    assert "redis" in services
    assert "influxdb" in services
    assert "dummy-feed" in services
    dummy_service = services["dummy-feed"]
    assert dummy_service.get("build", {}).get("dockerfile") == "infra/dummy_feed.Dockerfile"


@pytest.mark.integration
def test_dummy_feed_generates_data():
    df = dummy_feed.generate_dummy_data(5)
    assert len(df) == 5
    required_columns = {"datetime", "open", "high", "low", "close", "volume"}
    assert required_columns.issubset(df.columns)
