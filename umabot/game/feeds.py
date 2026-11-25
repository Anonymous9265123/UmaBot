"""Share upstream game data among commands and server alert loops."""

import asyncio
import logging
from collections import OrderedDict
from functools import partial
from time import monotonic, time
from urllib.parse import urlsplit

import aiohttp

from ..releases import support_releases, trainee_releases
from .api import build_url, fetch_record, gametora_data, gametora_manifest, gametora_version, support_catalog_rows
from .calendar import CM_FORECAST_CATALOG, game_events, projected_events
from .racetracks import track_search
from .search import NameSearch
from .skills import skill_search
from .upstream import UpstreamCooldown, UpstreamHTTP


log = logging.getLogger(__name__)
FEED_TTL = 3600
NEWS_TTL = 15 * 60
NEWS_ENDPOINT = "en/news/latest/20"
UMAPYOI_CATALOGS = {"character/list", "support", NEWS_ENDPOINT}
RETRY_DELAY = 60
CURRENT_BIRTHDAYS_URL = build_url("character/currentbirthdays")


class UmaFeeds:
    def __init__(self):
        self.values = {}
        self.refreshes = {}
        self.release_snapshots = {}
        self.autocomplete_indexes = {}
        self.skill_indexes = {}
        self.track_index = (None, None, None)
        self.support_catalog = (None, [])
        self.catalog_versions = {}
        self.catalog_attempts = {}
        self.records = OrderedDict()
        self.record_requests = {}
        self.session = None
        self.transport = None
        self.closed = False

    def http(self):
        if self.transport is None:
            if self.session is None:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
            self.transport = UpstreamHTTP(self.session)
        return self.transport

    async def record(self, url, *, timeout=30, allow_redirects=False):
        request_key = (url, allow_redirects, timeout)
        task = self.record_requests.get(request_key)
        if task is None or task.done():
            task = asyncio.create_task(self.load_record(url, timeout=timeout, allow_redirects=allow_redirects))
            self.record_requests[request_key] = task
            task.add_done_callback(lambda finished: self.finish_record(request_key, finished))
        elif self.transport and self.transport.cooling_down(urlsplit(url).hostname):
            return self.stale_record(url, allow_redirects)
        return await asyncio.shield(task)

    def finish_record(self, request_key, task):
        if self.record_requests.get(request_key) is task:
            del self.record_requests[request_key]
        if not task.cancelled():
            # A request may fail after all of its callers have cancelled.
            task.exception()

    async def load_record(self, url, *, timeout, allow_redirects):
        key = (url, allow_redirects)
        cacheable = url.partition("?")[0] != CURRENT_BIRTHDAYS_URL
        try:
            status, data = await fetch_record(self.http(), url, timeout=timeout, allow_redirects=allow_redirects)
        except UpstreamCooldown:
            return self.stale_record(url, allow_redirects)
        if cacheable and 200 <= status < 300:
            self.records[key] = (status, data)
            self.records.move_to_end(key)
            if len(self.records) > 256:
                self.records.popitem(last=False)
        return status, data

    def stale_record(self, url, allow_redirects):
        if url.partition("?")[0] == CURRENT_BIRTHDAYS_URL:
            return 429, None
        return self.records.get((url, allow_redirects), (429, None))

    async def close(self):
        self.closed = True
        tasks = (*self.refreshes.values(), *self.record_requests.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.session is not None:
            await self.session.close()

    async def cached(self, key, load, empty, *, ttl=FEED_TTL, timeout=None, host=None):
        value, expires = self.values.get(key, (empty, 0))
        if self.closed or monotonic() < expires or (host and self.transport and self.transport.cooling_down(host)):
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
        except UpstreamCooldown as error:
            self.values[key] = (previous, error.retry_at)
            return previous
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
        async with self.http().get(build_url(endpoint)) as response:
            response.raise_for_status()
            rows = await response.json(content_type=None)
        if not isinstance(rows, list):
            raise ValueError("Expected a list of records")
        if endpoint == NEWS_ENDPOINT:
            return [row for row in rows if isinstance(row, dict)]
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("Unexpected catalog record format")
        return rows

    async def load_version(self, endpoint, version, key):
        data = await gametora_data(self.http(), endpoint, version)
        if endpoint == CM_FORECAST_CATALOG:
            valid = isinstance(data, dict)
        else:
            valid = isinstance(data, list) and all(isinstance(row, dict) for row in data)
        if not valid:
            raise ValueError(f"Unexpected GameTora catalog format for {endpoint}")
        if self.catalog_attempts[endpoint] != version:
            return self.values[key][0]
        self.catalog_versions[endpoint] = version
        return data

    async def versioned_catalog(self, endpoint, empty, *, timeout=None):
        key = ("GameTora catalog", endpoint)
        try:
            async with asyncio.timeout(timeout):
                manifest = await self.cached("GameTora manifest", self.load_manifest, {}, host="gametora.com")
                previous = self.values.get(key, (empty, 0))[0]
                version = gametora_version(manifest, endpoint)
                if version is None:
                    return previous
                if self.catalog_attempts.get(endpoint) != version:
                    self.catalog_attempts[endpoint] = version
                    self.values[key] = (previous, 0)
                if self.catalog_versions.get(endpoint) == version:
                    return previous
                return await self.cached(
                    key, lambda: self.load_version(endpoint, version, key), empty, ttl=0, host="gametora.com",
                )
        except TimeoutError:
            return self.values.get(key, (empty, 0))[0]

    async def catalog(self, endpoint, *, timeout=None):
        if endpoint == CM_FORECAST_CATALOG:
            rows = (await self.forecast_timeline(timeout=timeout)).get("future_cm")
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        source = "support-cards" if endpoint == "gametora" else endpoint
        if source in UMAPYOI_CATALOGS:
            ttl = NEWS_TTL if source == NEWS_ENDPOINT else FEED_TTL
            rows = await self.cached(
                source, lambda: self.load_catalog(source), [], ttl=ttl, timeout=timeout, host="umapyoi.net",
            )
        else:
            rows = await self.versioned_catalog(source, [], timeout=timeout)
        if endpoint != "gametora":
            return rows
        previous, support = self.support_catalog
        if rows is not previous:
            support = support_catalog_rows(rows)
            self.support_catalog = (rows, support)
        return support

    def autocomplete_index(self, endpoint, records):
        """Reuse prepared names and ranked suggestions for the current catalog."""
        previous, index = self.autocomplete_indexes.get(endpoint, (None, None))
        if records is not previous:
            index = NameSearch(records, support=endpoint in {"gametora", "support"})
            self.autocomplete_indexes[endpoint] = (records, index)
        return index

    async def preload_search(self):
        """Build the command indexes a feature at a time using the shared cache."""
        async def index_catalog(endpoint):
            if records := await self.catalog(endpoint):
                self.autocomplete_index(endpoint, records)

        steps = (
            ("support cards", partial(index_catalog, "gametora")),
            ("characters", partial(index_catalog, "character/list")),
            ("racetracks", partial(self.tracks, timeout=None)),
            ("shared skills", partial(self.skills, unique=False, timeout=None)),
            ("unique skills", partial(self.skills, unique=True, timeout=None)),
        )
        for name, load in steps:
            try:
                await load()
            except Exception:
                log.exception("Could not preload search data for %s", name)

    async def skills(self, *, unique, timeout=2):
        if unique:
            skills, trainees = await asyncio.gather(
                self.catalog("skills", timeout=timeout), self.catalog("character-cards", timeout=timeout),
            )
        else:
            skills, trainees = await self.catalog("skills", timeout=timeout), ()
        previous, previous_trainees, index = self.skill_indexes.get(unique, (None, None, None))
        if skills is not previous or trainees is not previous_trainees:
            index = skill_search(skills, trainees, unique=unique)
            self.skill_indexes[unique] = (skills, trainees, index)
        return index

    async def events(self):
        return await self.cached("EN event calendar", self.load_events, [], ttl=60)

    async def tracks(self, *, timeout=2):
        tracks, names = await asyncio.gather(
            self.catalog("racetracks", timeout=timeout), self.catalog("racetracks_extended", timeout=timeout),
        )
        previous_tracks, previous_names, index = self.track_index
        if tracks is not previous_tracks or names is not previous_names:
            index = track_search(tracks, names)
            self.track_index = (tracks, names, index)
        return index

    async def release_snapshot(self, endpoint, build):
        """Reuse processed releases until the source changes or the next release goes live."""
        records = await self.catalog(endpoint)
        now = time()
        previous, built_at, next_release, result = self.release_snapshots.get(endpoint, (None, 0, 0, None))
        if records is not previous or not built_at <= now < next_release:
            result, next_release = build(records, now)
            self.release_snapshots[endpoint] = (records, now, next_release, result)
        return result

    async def support_releases(self):
        return await self.release_snapshot("support-cards", support_releases)

    async def trainee_releases(self):
        return await self.release_snapshot("character-cards", trainee_releases)

    async def load_events(self):
        return await game_events(self.catalog, int(time()))

    async def forecast_timeline(self, *, timeout=None):
        return await self.versioned_catalog(CM_FORECAST_CATALOG, {}, timeout=timeout)

    async def projections(self, confirmed, now):
        return await projected_events(await self.forecast_timeline(), self.catalog, confirmed, now)
