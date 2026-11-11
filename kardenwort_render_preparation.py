"""Core-first join policy; executors and provider effects remain caller-owned."""
from concurrent.futures import TimeoutError
from dataclasses import dataclass
from enum import Enum


class TranslationStatus(Enum):
    READY = 'ready'
    TIMED_OUT = 'timed_out'
    FAILED = 'failed'


@dataclass(frozen=True)
class PreparedSource:
    source: object
    status: TranslationStatus
    translation: str = ''
    error: Exception | None = None


def collect_preparation(core, translation, timeout, zid, logger):
    """A core failure wins even when translation failed earlier; never hide it."""
    try:
        source = core.result()
    except Exception as error:
        logger.error('[%s] Core preparation failed in parallel executor: %s', zid, error)
        raise
    try:
        value = translation.result(timeout=max(0.0, timeout))
    except TimeoutError:
        return PreparedSource(source, TranslationStatus.TIMED_OUT)
    except Exception as error:
        return PreparedSource(source, TranslationStatus.FAILED, error=error)
    return PreparedSource(source, TranslationStatus.READY, translation=value)
