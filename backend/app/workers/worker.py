from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.tasks import process_inbound_message


class WorkerSettings:
    functions = [process_inbound_message]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
