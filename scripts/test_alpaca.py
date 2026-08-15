from __future__ import annotations

import asyncio
import json

import requests
import websockets

from app.config import settings


def headers():
    return {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}


async def auth_ws(label: str, uri: str):
    async with websockets.connect(uri, close_timeout=5) as ws:
        await ws.send(json.dumps({"action": "auth", "key": settings.alpaca_key, "secret": settings.alpaca_secret}))
        print(f"{label} auth:", await asyncio.wait_for(ws.recv(), timeout=10))


async def main():
    r = requests.get(f"{settings.alpaca_trading_base}/v2/assets/AAPL", headers=headers(), timeout=10)
    print("REST asset:", r.status_code, r.text[:300])
    await auth_ws("SIP", settings.alpaca_market_ws)
    if settings.enable_overnight_stream:
        await auth_ws(settings.alpaca_overnight_feed.upper(), settings.alpaca_overnight_ws)
    await auth_ws("News", settings.alpaca_news_ws)


if __name__ == "__main__":
    asyncio.run(main())
