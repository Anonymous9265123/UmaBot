import asyncio
import tempfile
import unittest
from collections import Counter, deque
from contextlib import asynccontextmanager, contextmanager
from email.utils import formatdate
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp

from umabot.alerts import UmaAlerts
from umabot.game.api import build_url
from umabot.game.feeds import FEED_TTL, NEWS_ENDPOINT, NEWS_TTL, UmaFeeds
from umabot.game.upstream import HostBudget, UMAPYOI_LIMITS, UpstreamCooldown


MANIFEST = "https://gametora.com/data/manifests/umamusume.json"
SKILLS = "https://gametora.com/data/umamusume/skills."
NEWS = build_url(NEWS_ENDPOINT)


class MockResponse:
    def __init__(self, data=None, status=200, headers=None):
        self.data = data
        self.status = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientError(f"HTTP {self.status}")

    async def json(self, **kwargs):
        await asyncio.sleep(0)
        return self.data


class MockSession:
    def __init__(self):
        self.actions = {}
        self.calls = Counter()
        self.options = {}
        self.close = AsyncMock()

    def add(self, url, *responses):
        self.actions.setdefault(url, deque()).extend(responses)

    @asynccontextmanager
    async def get(self, url, **kwargs):
        self.calls[url] += 1
        self.options[url] = kwargs
        if not self.actions.get(url):
            raise AssertionError(f"Unexpected mocked request for {url}")
        yield self.actions[url].popleft()


class UpstreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 100.0
        self.epoch = 1_800_000_000
        self.clock = lambda: self.now
        self.enterContext(patch("umabot.game.feeds.monotonic", self.clock))
        self.enterContext(patch("umabot.game.upstream.monotonic", self.clock))
        self.enterContext(patch("umabot.game.upstream.time", lambda: self.epoch + self.now))
        self.enterContext(patch("aiohttp.ClientSession", side_effect=AssertionError("Network unavailable in tests")))
        self.session = MockSession()
        self.feeds = UmaFeeds()
        self.feeds.session = self.session
        self.addAsyncCleanup(self.feeds.close)

    async def test_news_refresh_is_shared_and_expires_in_fifteen_minutes(self):
        self.session.add(NEWS, MockResponse([{"id": 1}]), MockResponse([{"id": 2}]))
        first, second = await asyncio.gather(*(self.feeds.catalog(NEWS_ENDPOINT) for _ in range(2)))
        self.assertIs(first, second)
        self.now += NEWS_TTL - 1
        self.assertIs(await self.feeds.catalog(NEWS_ENDPOINT), first)
        self.now += 1
        self.assertEqual(await self.feeds.catalog(NEWS_ENDPOINT), [{"id": 2}])
        self.assertEqual(self.session.calls[NEWS], 2)

    async def test_catalog_reuses_version_and_keeps_old_data_until_replacement_succeeds(self):
        self.session.add(MANIFEST, MockResponse({"skills": "aa"}), MockResponse({"skills": "aa"}), MockResponse({"skills": "bb"}))
        self.session.add(SKILLS + "aa.json", MockResponse([{"id": 1}]))
        self.session.add(SKILLS + "bb.json", MockResponse(status=502), MockResponse([{"id": 2}]))
        first, shared = await asyncio.gather(self.feeds.catalog("skills"), self.feeds.catalog("skills"))
        self.assertIs(first, shared)
        self.now += FEED_TTL - 1
        self.assertIs(await self.feeds.catalog("skills"), first)
        self.now += 1
        self.assertIs(await self.feeds.catalog("skills"), first)
        self.assertEqual(self.session.calls[MANIFEST], 2)
        self.assertEqual(self.session.calls[SKILLS + "aa.json"], 1)
        self.now += FEED_TTL
        with self.assertLogs("umabot.game.feeds"):
            self.assertIs(await self.feeds.catalog("skills"), first)
        self.assertIs(await self.feeds.catalog("skills"), first)
        self.assertEqual(self.session.calls[SKILLS + "bb.json"], 1)
        self.now += 60
        self.assertEqual(await self.feeds.catalog("skills"), [{"id": 2}])
        self.assertEqual(self.session.calls[MANIFEST], 3)

    async def test_cooldown_is_shared_by_command_and_news_but_not_gametora(self):
        record = build_url("character/1")
        uncached = build_url("character/2")
        self.session.add(record, MockResponse({"id": 1}), MockResponse(status=429, headers={"Retry-After": "120"}))
        self.session.add(NEWS, MockResponse([{"id": 1}]))
        self.session.add(MANIFEST, MockResponse({"skills": "aa"}))
        self.session.add(SKILLS + "aa.json", MockResponse([{"id": 3}]))
        self.assertEqual(await self.feeds.record(record), (200, {"id": 1}))
        self.assertEqual(self.session.options[record]["timeout"].total, 30)
        self.assertEqual(await self.feeds.record(record), (200, {"id": 1}))
        self.assertEqual(await self.feeds.record(uncached, timeout=5), (429, None))
        self.assertEqual(await self.feeds.catalog(NEWS_ENDPOINT), [])
        self.assertEqual(await self.feeds.catalog("skills"), [{"id": 3}])
        self.assertEqual(self.session.calls[NEWS], 0)
        self.assertEqual(self.session.calls[uncached], 0)
        self.now += 120
        self.assertEqual(await self.feeds.catalog(NEWS_ENDPOINT), [{"id": 1}])
        self.session.add(uncached, MockResponse({"id": 2}))
        self.assertEqual(await self.feeds.record(uncached, timeout=5, allow_redirects=True), (200, {"id": 2}))
        self.assertEqual(self.session.options[uncached]["timeout"].total, 5)
        self.assertTrue(self.session.options[uncached]["allow_redirects"])

    async def test_http_date_and_exponential_backoff_with_cap_and_reset(self):
        record = build_url("character/1")
        date = formatdate(self.epoch + self.now + 90, usegmt=True)
        responses = [MockResponse(status=429, headers={"Retry-After": date})]
        responses += [MockResponse(status=429, headers={"Retry-After": "invalid"}) for _ in range(8)]
        responses += [MockResponse({"id": 1}), MockResponse(status=429)]
        self.session.add(record, *responses)
        for index, delay in enumerate((90, 60, 120, 240, 480, 960, 1920, 3600, 3600), 1):
            self.assertEqual(await self.feeds.record(record), (429, None))
            self.now += delay - 1
            self.assertEqual(await self.feeds.record(record), (429, None))
            self.assertEqual(self.session.calls[record], index)
            self.now += 1
        self.assertEqual(await self.feeds.record(record), (200, {"id": 1}))
        await self.feeds.record(record)
        self.assertEqual(self.feeds.http().hosts["umapyoi.net"].until, self.now + 60)

    async def test_cooldown_does_not_wait_for_a_request_already_in_flight(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowResponse(MockResponse):
            async def json(self, **kwargs):
                entered.set()
                await release.wait()
                return [{"id": 1}]

        self.session.add(NEWS, SlowResponse())
        record = build_url("character/1")
        self.session.add(record, MockResponse(status=429, headers={"Retry-After": "120"}))
        pending = asyncio.create_task(self.feeds.catalog(NEWS_ENDPOINT))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            self.assertEqual(await self.feeds.record(record), (429, None))
            self.assertEqual(await asyncio.wait_for(self.feeds.catalog(NEWS_ENDPOINT), timeout=0.5), [])
        finally:
            release.set()
            await pending
        self.assertEqual(self.session.calls[NEWS], 1)

    async def test_gametora_cooldown_keeps_catalog_and_does_not_block_news(self):
        self.session.add(MANIFEST, MockResponse({"skills": "aa"}), MockResponse({"skills": "bb", "support-cards": "cc"}))
        self.session.add(SKILLS + "aa.json", MockResponse([{"id": 1}]))
        self.session.add(SKILLS + "bb.json", MockResponse(status=429, headers={"Retry-After": "90"}), MockResponse([{"id": 2}]))
        self.session.add(NEWS, MockResponse([{"id": 3}]))
        original = await self.feeds.catalog("skills")
        self.now += FEED_TTL
        self.assertIs(await self.feeds.catalog("skills"), original)
        self.assertEqual(await self.feeds.catalog("support-cards", timeout=0.5), [])
        self.assertEqual(self.session.calls["https://gametora.com/data/umamusume/support-cards.cc.json"], 0)
        self.assertEqual(await self.feeds.catalog(NEWS_ENDPOINT), [{"id": 3}])
        self.now += 90
        self.assertEqual(await self.feeds.catalog("skills"), [{"id": 2}])

    def test_documented_rolling_windows_reject_without_sleeping(self):
        for seconds, limit in UMAPYOI_LIMITS:
            with self.subTest(window=seconds):
                budget = HostBudget(((seconds, limit),))
                for _ in range(limit):
                    budget.admit("umapyoi.net", self.now)
                with self.assertRaises(UpstreamCooldown) as blocked:
                    budget.admit("umapyoi.net", self.now)
                self.assertEqual(blocked.exception.retry_at, self.now + seconds)
                budget.admit("umapyoi.net", self.now + seconds)


class NewsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.queries = []
        self.send = AsyncMock()

        class TracedAlerts(UmaAlerts):
            @contextmanager
            def database(alerts):
                with super().database() as db:
                    db.set_trace_callback(self.queries.append)
                    yield db

        channel = SimpleNamespace(send=self.send)
        client = SimpleNamespace(get_partial_messageable=lambda _: channel)
        self.alerts = TracedAlerts(client, 1, Path(temp) / "alerts.sqlite3", SimpleNamespace())
        with patch.object(type(self.alerts.poll), "start"):
            self.alerts.start()

    async def test_scheduler_checks_news_at_fifteen_minutes_and_releases_hourly(self):
        alerts = self.alerts
        alerts.posting_setting = lambda: (2, 0)
        alerts.check_announcements = AsyncMock()
        alerts.check_cards = AsyncMock()
        alerts.check_trainees = AsyncMock()
        with patch("umabot.alerts.monotonic") as clock:
            for now in (100, 100 + NEWS_TTL, 100 + 2 * NEWS_TTL, 100 + FEED_TTL):
                clock.return_value = now
                await alerts.poll.coro(alerts)
        self.assertEqual(alerts.check_announcements.await_count, 4)
        self.assertEqual(alerts.check_cards.await_count, 2)
        self.assertEqual(alerts.check_trainees.await_count, 2)

    async def test_news_posts_once_with_original_embed_and_queries_candidate_keys_only(self):
        setting = (2, 100)
        self.alerts.posting_setting = lambda: setting
        self.alerts.feeds.catalog = AsyncMock(return_value=[
            {"id": "old", "title": "Old", "post_at": 100},
            {"id": "ready", "title": "New EN event", "post_at": 120},
            {"id": "ready", "title": "New EN event", "post_at": 120},
            {"id": "future", "title": "Future", "post_at": 180},
        ])
        with patch("umabot.alerts.time", return_value=150):
            await self.alerts.check_announcements(setting)
            restarted = UmaAlerts(self.alerts.client, 1, self.alerts.path, self.alerts.feeds)
            restarted.posting_setting = lambda: setting
            await restarted.check_announcements(setting)
        self.send.assert_awaited_once()
        self.assertEqual(self.send.await_args.kwargs["embed"].title, "New EN event")
        selects = [query for query in self.queries if "SELECT news_key FROM news_deliveries" in query]
        self.assertEqual(len(selects), 1)
        self.assertIn("news_key IN ('ready')", selects[0])
