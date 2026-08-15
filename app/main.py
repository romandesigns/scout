from __future__ import annotations

import asyncio
import logging
import signal

from aiohttp import web

from .api import create_app
from .catalysts import CatalystWatcher
from .config import settings
from .db import Store
from .dispatch import Dispatcher
from .events import EventHub
from .market import MarketWatcher


async def serve_http(store: Store, market: MarketWatcher, events: EventHub, catalysts: CatalystWatcher, dispatcher: Dispatcher):
    app = create_app(store, market, events, catalysts, dispatcher)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.health_port)
    await site.start()
    await asyncio.Event().wait()


async def amain():
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("scout")
    store = Store(settings.data_dir / "state.db")
    events = EventHub()
    dispatcher = Dispatcher(store, events)
    market = MarketWatcher(store, dispatcher, events)
    dispatcher.set_snapshot_provider(market.snapshot)
    dispatcher.set_finding_listener(market.register_finding)
    catalysts = CatalystWatcher(store, dispatcher, market)

    log.info(
        "Starting %s feed=%s price=$%.2f-$%.2f catalyst=%ss wakeup=%ss bucket=%ss resend=%s ntfy=%s dashboard=%s",
        settings.app_name, settings.alpaca_feed, market.min_price, market.max_price,
        settings.sec_poll_seconds, settings.eval_seconds, settings.bucket_seconds,
        bool(settings.resend_api_key), bool(settings.ntfy_topic), settings.web_out_dir,
    )

    tasks = [
        asyncio.create_task(market.universe_loop(), name="universe"),
        asyncio.create_task(market.stream_loop(), name="market-sip"),
        *([asyncio.create_task(market.overnight_stream_loop(), name="market-overnight")] if settings.enable_overnight_stream else []),
        asyncio.create_task(catalysts.sec_loop(), name="sec"),
        asyncio.create_task(catalysts.alpaca_news_loop(), name="alpaca-news"),
        asyncio.create_task(serve_http(store, market, events, catalysts, dispatcher), name="http"),
    ]

    if settings.rss_feeds:
        tasks.append(asyncio.create_task(catalysts.rss_loop(), name="rss"))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait(tasks + [waiter], return_when=asyncio.FIRST_COMPLETED)
    if waiter not in done:
        for d in done:
            if d.exception():
                raise d.exception()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
