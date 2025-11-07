import asyncio
import json
from typing import Callable, Dict

import redis

from app.config.settings import get_settings
from app.utils.logging import get_logger


settings = get_settings()
logger = get_logger(__name__)


def start_stream_listener(channel: str, handler: Callable[[Dict], None]) -> None:
    client = redis.Redis.from_url(str(settings.redis.url))
    pubsub = client.pubsub()
    pubsub.subscribe(channel)

    async def _listen() -> None:
        while True:
            message = pubsub.get_message()
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                handler(data)
            await asyncio.sleep(0.1)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_listen())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.create_task(_listen())
        threading = __import__("threading")
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
