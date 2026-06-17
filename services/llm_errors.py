"""services/llm_errors.py — общие исключения LLM-провайдеров."""


class LLMError(Exception):
    """Базовое исключение для ошибок LLM-клиента."""


class LLMRateLimitError(LLMError):
    """Rate limit от API (429)."""


class LLMContextTooLargeError(LLMError):
    """Текст превышает контекстное окно модели."""


class LLMModelNotFoundError(LLMError):
    """Модель недоступна у провайдера (404)."""


class DailyLimitExceededError(LLMError):
    """Превышен дневной лимит запросов пользователя."""
