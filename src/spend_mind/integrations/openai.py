import asyncio
from enum import StrEnum

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from openai.types.responses import Response

from spend_mind.config import Settings


class OpenAIErrorKind(StrEnum):
    TIMEOUT = "openai_timeout"
    CONNECTION = "openai_connection_error"
    AUTHENTICATION = "openai_authentication_error"
    RATE_LIMIT = "openai_rate_limit"
    API = "openai_api_error"


class OpenAIRequestFailed(Exception):
    def __init__(self, kind: OpenAIErrorKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class OpenAIResponses:
    def __init__(
        self, client: AsyncOpenAI, model: str, total_timeout_seconds: float
    ) -> None:
        self._client = client
        self._model = model
        self._total_timeout_seconds = total_timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIResponses | None":
        secret = settings.openai_api_key
        if secret is None or not (api_key := secret.get_secret_value().strip()):
            return None
        client = AsyncOpenAI(
            api_key=api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
        return cls(client, settings.openai_model, settings.openai_total_timeout_seconds)

    async def create_response(self, input_text: str) -> Response:
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                response = await self._client.responses.create(
                    model=self._model, input=input_text, store=False
                )
        except (APITimeoutError, TimeoutError):
            kind = OpenAIErrorKind.TIMEOUT
        except APIConnectionError:
            kind = OpenAIErrorKind.CONNECTION
        except AuthenticationError:
            kind = OpenAIErrorKind.AUTHENTICATION
        except RateLimitError:
            kind = OpenAIErrorKind.RATE_LIMIT
        except APIError:
            kind = OpenAIErrorKind.API
        else:
            if response.status != "failed":
                return response
            kind = OpenAIErrorKind.API

        # SDK exceptions may retain the request and response. Keep only a safe code.
        raise OpenAIRequestFailed(kind)

    async def close(self) -> None:
        await self._client.close()
