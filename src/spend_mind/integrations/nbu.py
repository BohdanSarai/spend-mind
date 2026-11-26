import asyncio
import json
from datetime import date
from decimal import Decimal

import httpx

EXCHANGE_RATE_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchangenew"
TIMEOUT_SECONDS = 5


class ExchangeRateUnavailable(Exception):
    pass


async def get_historical_rate(
    client: httpx.AsyncClient, currency: str, on_date: date
) -> Decimal:
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            response = await client.get(
                EXCHANGE_RATE_URL,
                params={
                    "json": "",
                    "valcode": currency,
                    "date": on_date.strftime("%Y%m%d"),
                },
                timeout=TIMEOUT_SECONDS,
            )
        response.raise_for_status()
        records = json.loads(response.content, parse_float=Decimal, parse_int=Decimal)
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise ExchangeRateUnavailable from exc

    if not isinstance(records, list) or len(records) != 1:
        raise ExchangeRateUnavailable
    record = records[0]
    if (
        not isinstance(record, dict)
        or record.get("cc") != currency
        or record.get("exchangedate") != on_date.strftime("%d.%m.%Y")
    ):
        raise ExchangeRateUnavailable
    rate = record.get("rate")
    if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0:
        raise ExchangeRateUnavailable
    return rate
