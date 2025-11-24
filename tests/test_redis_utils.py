import json
from unittest.mock import MagicMock

import pytest

from app.utils.redis import publish


@pytest.fixture(autouse=True)
def mock_redis_client(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr("app.utils.redis._publisher", mock_client)
    return mock_client


def test_publish_serializes_json(mock_redis_client):
    publish("channel", {"foo": "bar"})
    mock_redis_client.publish.assert_called_once()
    args, kwargs = mock_redis_client.publish.call_args
    assert args[0] == "channel"
    assert json.loads(args[1]) == {"foo": "bar"}
