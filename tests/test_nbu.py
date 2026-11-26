from datetime import date
from decimal import Decimal

import httpx
import pytest

from spend_mind.integrations import nbu


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["USD", "EUR"])
async def test_fetches_exact_historical_rate(currency: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/NBUStatService/v1/statdirectory/exchangenew"
        assert request.url.params == httpx.QueryParams(
            {"json": "", "valcode": currency, "date": "20261127"}
        )
        assert set(request.extensions["timeout"].values()) == {5}
        return httpx.Response(
            200,
            text=f'[{{"cc":"{currency}","exchangedate":"27.11.2026","rate":42.12345678}}]',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        rate = await nbu.get_historical_rate(client, currency, date(2026, 11, 27))
    assert rate == Decimal("42.12345678")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, "[]"),
        (200, "[]"),
        (200, "broken"),
        (200, '[{"cc":"EUR","exchangedate":"27.11.2026","rate":42}]'),
        (200, '[{"cc":"USD","exchangedate":"26.11.2026","rate":42}]'),
        (200, '[{"cc":"USD","exchangedate":"27.11.2026","rate":0}]'),
        (200, '[{"cc":"USD","exchangedate":"27.11.2026","rate":NaN}]'),
    ],
)
async def test_rejects_unavailable_or_invalid_rates(status: int, body: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(nbu.ExchangeRateUnavailable):
            await nbu.get_historical_rate(client, "USD", date(2026, 11, 27))


@pytest.mark.asyncio
async def test_maps_nbu_timeout() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(nbu.ExchangeRateUnavailable):
            await nbu.get_historical_rate(client, "USD", date(2026, 11, 27))
