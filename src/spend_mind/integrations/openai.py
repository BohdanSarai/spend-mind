import asyncio
from collections.abc import Awaitable
from enum import StrEnum
from json import JSONDecodeError

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from openai.types.responses import Response
from openai.types.responses.response_input_param import ResponseInputParam
from pydantic import BaseModel, ValidationError

from spend_mind.config import Settings


class OpenAIErrorKind(StrEnum):
    TIMEOUT = "openai_timeout"
    CONNECTION = "openai_connection_error"
    AUTHENTICATION = "openai_authentication_error"
    RATE_LIMIT = "openai_rate_limit"
    API = "openai_api_error"
    INCOMPLETE = "openai_incomplete"
    FAILED = "openai_failed"
    REFUSAL = "openai_refusal"
    INVALID_OUTPUT = "openai_invalid_output"


class OpenAIRequestFailed(Exception):
    def __init__(self, kind: OpenAIErrorKind, http_status: int | None = None) -> None:
        self.kind = kind
        self.http_status = http_status
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

    async def _request[T](self, request: Awaitable[T]) -> T:
        http_status = None
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                return await request
        except (APITimeoutError, TimeoutError):
            kind = OpenAIErrorKind.TIMEOUT
        except APIConnectionError:
            kind = OpenAIErrorKind.CONNECTION
        except AuthenticationError:
            kind = OpenAIErrorKind.AUTHENTICATION
        except RateLimitError:
            kind = OpenAIErrorKind.RATE_LIMIT
        except APIStatusError as exc:
            kind = OpenAIErrorKind.API
            http_status = exc.status_code
        except APIError:
            kind = OpenAIErrorKind.API
        except (JSONDecodeError, ValidationError):
            kind = OpenAIErrorKind.INVALID_OUTPUT

        # SDK exceptions may retain the request and response. Keep only a safe code.
        raise OpenAIRequestFailed(kind, http_status)

    async def create_response(self, input_text: str) -> Response:
        response = await self._request(
            self._client.responses.create(
                model=self._model, input=input_text, store=False
            )
        )
        if response.status == "failed":
            raise OpenAIRequestFailed(OpenAIErrorKind.API)
        return response

    async def create_structured_response[T: BaseModel](
        self,
        input_text: str | ResponseInputParam,
        *,
        instructions: str,
        schema: type[T],
    ) -> T:
        # Keep strict schema generation, but inspect the response before parsing
        # the text so truncated JSON still reports an incomplete response.
        response = await self._request(
            self._client.responses.parse(
                model=self._model,
                input=input_text,
                instructions=instructions,
                text_format=schema,
                parse_as=None,
                store=False,
            )
        )
        if response.status == "failed":
            raise OpenAIRequestFailed(OpenAIErrorKind.FAILED)
        if response.status == "incomplete":
            raise OpenAIRequestFailed(OpenAIErrorKind.INCOMPLETE)

        content = [
            part
            for output in response.output or []
            if output.type == "message"
            for part in output.content or []
        ]
        if any(part.type == "refusal" for part in content):
            raise OpenAIRequestFailed(OpenAIErrorKind.REFUSAL)

        texts = [part.text for part in content if part.type == "output_text"]
        if response.status == "completed" and len(texts) == 1:
            try:
                return schema.model_validate_json(texts[0])
            except ValidationError:
                pass
        raise OpenAIRequestFailed(OpenAIErrorKind.INVALID_OUTPUT)

    async def close(self) -> None:
        await self._client.close()
