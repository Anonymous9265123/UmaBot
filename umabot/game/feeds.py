"""Share upstream game data among commands and server alert loops."""

import asyncio
import logging
from time import monotonic, time

import aiohttp

from .api import build_url, gametora_catalog, gametora_data, gametora_manifest, support_catalog_rows
from .calendar import CM_FORECAST_CATALOG, game_events, projected_events


log = logging.getLogger(__name__)
FEED_TTL = 3600
RETRY_DELAY = 60


class UmaFeeds:
    def __init__(self):
        self.values = {}
        self.refreshes = {}
        self.session = None
        self.closed = False

    def http(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self.session

    async def close(self):
        self.closed = True
        tasks = tuple(self.refreshes.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.session is not None:
            await self.session.close()

    async def cached(self, key, load, empty, *, ttl=FEED_TTL, timeout=None):
        value, expires = self.values.get(key, (empty, 0))
        if self.closed or monotonic() < expires:
            return value
        task = self.refreshes.get(key)
        if task is None:
            task = asyncio.create_task(self.refresh(key, load, value, ttl))
            self.refreshes[key] = task
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(task)
        except TimeoutError:
            return value

    async def refresh(self, key, load, previous, ttl):
        try:
            value = await load()
            self.values[key] = (value, monotonic() + ttl)
            return value
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            log.warning("Could not refresh %s: %s", key, error)
            self.values[key] = (previous, monotonic() + RETRY_DELAY)
            return previous
        finally:
            self.refreshes.pop(key, None)

    async def load_manifest(self):
        manifest = await gametora_manifest(self.http())
        if not isinstance(manifest, dict):
            raise ValueError("Unexpected GameTora manifest format")
        return manifest

    async def load_catalog(self, endpoint):
        if endpoint in {"character/list", "support", "en/news/latest/20"}:
            async with self.http().get(build_url(endpoint)) as response:
                response.raise_for_status()
                rows = await response.json(content_type=None)
        else:
            manifest = await self.cached("GameTora manifest", self.load_manifest, {})
            rows = await gametora_catalog(self.http(), endpoint, manifest=manifest)
        if not isinstance(rows, list):
            raise ValueError("Expected a list of records")
        if endpoint == "en/news/latest/20":
            return [row for row in rows if isinstance(row, dict)]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("Unexpected catalog record format")
        return rows

    async def catalog(self, endpoint, *, timeout=None):
        source = "support-cards" if endpoint == "gametora" else endpoint
        rows = await self.cached(source, lambda: self.load_catalog(source), [], timeout=timeout)
        return support_catalog_rows(rows) if endpoint == "gametora" else rows

    async def events(self):
        if endpoint == CM_FORECAST_CATALOG:
            rows = (await self.forecast_timeline(timeout=timeout)).get("future_cm")
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        return await self.cached("EN event calendar", self.load_events, [], ttl=60)

    async def load_events(self):
        return await game_events(self.catalog, int(time()))

    async def load_forecast_timeline(self):
        manifest = await self.cached("GameTora manifest", self.load_manifest, {})
        timeline = await gametora_data(self.http(), CM_FORECAST_CATALOG, manifest=manifest)
        if not isinstance(timeline, dict):
            raise ValueError("Unexpected GameTora forecast timeline format")
        return timeline

    async def forecast_timeline(self, *, timeout=None):
        return await self.cached("GameTora forecast timeline", self.load_forecast_timeline, {}, timeout=timeout)

    async def projections(self, confirmed, now):
        return await projected_events(await self.forecast_timeline(), self.catalog, confirmed, now)
