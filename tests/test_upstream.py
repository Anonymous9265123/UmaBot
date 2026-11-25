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
import discord

from umabot.alerts import UmaAlerts
from umabot.bot import UmaBot
from umabot.commands.lookup import lookup, register_uma
from umabot.game.api import build_url
from umabot.game.feeds import FEED_TTL, NEWS_ENDPOINT, NEWS_TTL, RETRY_DELAY, UmaFeeds
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


class BlockingResponse(MockResponse):
    def __init__(self, data):
        super().__init__(data)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def json(self, **kwargs):
        self.entered.set()
        await self.release.wait()
        return self.data


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

    def mock_search_catalogs(self, support_response=None):
        catalogs = {
            "support-cards": [{"support_id": 1, "char_name": "Example Runner", "title_en": "Example Card"}],
            "racetracks": [{"id": 2, "courses": [{"length": 1200}]}],
            "racetracks_extended": [{"id": 2, "name_en": "Example Park"}],
            "skills": [
                {"id": 3, "rarity": 1, "name_en": "Corner Speed"},
                {"id": 4, "rarity": 3, "name_en": "Example Unique", "char": [10]},
            ],
            "character-cards": [{"card_id": 10, "name_en": "Example Trainee", "title_en_gl": "[Parade]"}],
        }
        manifest = {endpoint: "aa" for endpoint in catalogs}
        urls = {endpoint: f"https://gametora.com/data/umamusume/{endpoint}.aa.json" for endpoint in catalogs}
        self.session.add(MANIFEST, MockResponse(manifest))
        for endpoint, rows in catalogs.items():
            if endpoint == "support-cards" and support_response is not None:
                self.session.add(urls[endpoint], support_response)
            else:
                self.session.add(urls[endpoint], MockResponse(rows))
        urls["character/list"] = build_url("character/list")
        self.session.add(urls["character/list"], MockResponse([{"id": 9, "name_en": "Example Runner"}]))
        return urls

    async def test_preload_shares_catalogs_and_an_arriving_command_across_reconnects(self):
        response = BlockingResponse([{"support_id": 1, "char_name": "Example Runner", "title_en": "Example Card"}])
        urls = self.mock_search_catalogs(response)
        bot = UmaBot()
        bot.feeds = self.feeds
        self.addAsyncCleanup(bot.close)
        guild = discord.Object(id=1)
        register_uma(bot.tree, guild, feeds=self.feeds)
        support = bot.tree.get_command("uma", guild=guild).get_command("support")
        with patch("builtins.print"):
            await asyncio.wait_for(bot.on_ready(), timeout=1)
            preload = bot.search_preload
            self.assertIsNotNone(preload)
            await asyncio.wait_for(response.entered.wait(), timeout=1)
            self.assertEqual(self.session.calls, Counter({MANIFEST: 1, urls["support-cards"]: 1}))
            await bot.on_ready()
            self.assertIs(bot.search_preload, preload)

        command = asyncio.create_task(support._params["name"].autocomplete(None, "example card"))
        try:
            await asyncio.sleep(0)
            self.assertFalse(command.done())
        finally:
            response.release.set()
        self.assertEqual([choice.value for choice in await command], ["1"])
        await asyncio.wait_for(preload, timeout=2)

        with patch("builtins.print"):
            await bot.on_ready()
        self.assertIs(bot.search_preload, preload)
        self.assertEqual(self.session.calls, Counter([MANIFEST, *urls.values()]))
        self.assertEqual(self.feeds.autocomplete_indexes["gametora"][1].resolve_id("example card"), 1)
        self.assertEqual(self.feeds.autocomplete_indexes["character/list"][1].resolve_id("example runner"), 9)
        self.assertEqual(self.feeds.track_index[2].resolve_id("example park"), 2)
        self.assertEqual(self.feeds.skill_indexes[False][2].resolve_id("corner speed"), 3)
        self.assertEqual(self.feeds.skill_indexes[True][2].resolve_id("example trainee [parade]"), 4)

    async def test_failed_preload_does_not_block_other_indexes_or_on_demand_recovery(self):
        urls = self.mock_search_catalogs(MockResponse(status=502))
        self.session.add(urls["support-cards"], MockResponse([{"support_id": 5, "title_en": "Recovered Card"}]))
        with self.assertLogs("umabot.game.feeds"):
            await self.feeds.preload_search()
        self.assertNotIn("gametora", self.feeds.autocomplete_indexes)
        self.assertEqual(self.feeds.skill_indexes[True][2].resolve_id("example unique"), 4)
        self.assertEqual(self.session.calls, Counter([MANIFEST, *urls.values()]))

        self.now += RETRY_DELAY
        records = await self.feeds.catalog("gametora")
        self.assertEqual(self.feeds.autocomplete_index("gametora", records).resolve_id("recovered card"), 5)
        self.assertEqual(self.session.calls[urls["support-cards"]], 2)
        self.assertEqual(self.session.calls[MANIFEST], 1)

    async def test_shutdown_cancels_preload_and_closes_pending_feed_requests(self):
        response = BlockingResponse([])
        urls = self.mock_search_catalogs(response)
        bot = UmaBot()
        bot.feeds = self.feeds
        with patch("builtins.print"):
            await bot.on_ready()
        await asyncio.wait_for(response.entered.wait(), timeout=1)
        await asyncio.wait_for(bot.close(), timeout=1)
        self.assertTrue(bot.search_preload.cancelled())
        self.assertTrue(self.feeds.closed)
        self.assertEqual(self.feeds.refreshes, {})
        self.session.close.assert_awaited_once()
        self.assertEqual(self.session.calls, Counter({MANIFEST: 1, urls["support-cards"]: 1}))

    async def test_news_refresh_is_shared_and_expires_in_fifteen_minutes(self):
        self.session.add(NEWS, MockResponse([{"id": 1}]), MockResponse([{"id": 2}]))
        first, second = await asyncio.gather(*(self.feeds.catalog(NEWS_ENDPOINT) for _ in range(2)))
        self.assertIs(first, second)
        self.now += NEWS_TTL - 1
        self.assertIs(await self.feeds.catalog(NEWS_ENDPOINT), first)
        self.now += 1
        self.assertEqual(await self.feeds.catalog(NEWS_ENDPOINT), [{"id": 2}])
        self.assertEqual(self.session.calls[NEWS], 2)

    async def test_birthdays_never_use_previous_day_during_cooldown(self):
        endpoint = "character/currentbirthdays"
        url = build_url(endpoint)
        self.session.add(
            url,
            MockResponse({"birthdays": [{"name_en": "Previous Day", "birth_month": 1, "birth_day": 16}]}),
            MockResponse(status=429, headers={"Retry-After": "120"}),
        )
        reply = AsyncMock()
        interaction = SimpleNamespace(
            client=SimpleNamespace(feeds=self.feeds),
            response=SimpleNamespace(is_done=lambda: True),
            edit_original_response=reply,
        )
        await lookup(interaction, endpoint, "Birthdays", birthdays=True)
        self.assertEqual(reply.await_args.kwargs["embeds"][0].title, "Today's birthdays")
        self.assertIn("Previous Day", reply.await_args.kwargs["embeds"][0].description)

        self.now += 86400
        await lookup(interaction, endpoint, "Birthdays", birthdays=True)
        self.assertEqual(reply.await_args.kwargs, {"content": "Too many requests. Try again later."})
        await lookup(interaction, endpoint, "Birthdays", birthdays=True)
        self.assertEqual(reply.await_args.kwargs, {"content": "Too many requests. Try again later."})
        self.assertEqual(self.session.calls[url], 2)

    async def test_overlapping_direct_lookups_share_one_request_and_later_lookup_is_fresh(self):
        url = build_url("character/1")
        self.session.add(url, MockResponse({"id": 1}), MockResponse({"id": 2}))
        results = await asyncio.gather(self.feeds.record(url), self.feeds.record(url))
        self.assertEqual(results, [(200, {"id": 1}), (200, {"id": 1})])
        self.assertEqual(self.session.calls[url], 1)
        self.assertEqual(await self.feeds.record(url), (200, {"id": 2}))
        self.assertEqual(self.session.calls[url], 2)

    async def test_cancelling_one_direct_caller_leaves_the_other_waiting(self):
        url = build_url("support/1")
        response = BlockingResponse({"id": 1})
        self.session.add(url, response)
        first = asyncio.create_task(self.feeds.record(url))
        second = asyncio.create_task(self.feeds.record(url))
        try:
            await asyncio.wait_for(response.entered.wait(), timeout=1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertFalse(second.done())
        finally:
            response.release.set()
        self.assertEqual(await asyncio.wait_for(second, timeout=1), (200, {"id": 1}))
        self.assertEqual(self.session.calls[url], 1)

    async def test_new_direct_caller_does_not_join_pending_request_during_cooldown(self):
        url = build_url("support/1")
        response = BlockingResponse({"id": 1})
        limited = build_url("character/1")
        self.session.add(url, response)
        self.session.add(limited, MockResponse(status=429, headers={"Retry-After": "120"}))
        pending = asyncio.create_task(self.feeds.record(url))
        try:
            await asyncio.wait_for(response.entered.wait(), timeout=1)
            self.assertEqual(await self.feeds.record(limited), (429, None))
            self.assertEqual(await asyncio.wait_for(self.feeds.record(url), timeout=1), (429, None))
        finally:
            response.release.set()
            await pending
        self.assertEqual(self.session.calls[url], 1)

    async def test_failed_shared_direct_lookup_can_be_retried(self):
        url = build_url("character/1")
        self.session.add(url, MockResponse(status=502), MockResponse({"id": 1}))
        results = await asyncio.gather(self.feeds.record(url), self.feeds.record(url), return_exceptions=True)
        self.assertTrue(all(isinstance(result, aiohttp.ClientError) for result in results))
        self.assertEqual(self.session.calls[url], 1)
        self.assertEqual(await self.feeds.record(url), (200, {"id": 1}))
        self.assertEqual(self.session.calls[url], 2)

    async def test_different_direct_timeouts_or_redirect_settings_do_not_share_requests(self):
        url = build_url("support/1")
        self.session.add(url, *(MockResponse({"request": index}) for index in range(3)))
        results = await asyncio.gather(
            self.feeds.record(url),
            self.feeds.record(url, timeout=5),
            self.feeds.record(url, allow_redirects=True),
        )
        self.assertEqual(results, [(200, {"request": index}) for index in range(3)])
        self.assertEqual(self.session.calls[url], 3)

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
        response = BlockingResponse([{"id": 1}])
        self.session.add(NEWS, response)
        record = build_url("character/1")
        self.session.add(record, MockResponse(status=429, headers={"Retry-After": "120"}))
        pending = asyncio.create_task(self.feeds.catalog(NEWS_ENDPOINT))
        try:
            await asyncio.wait_for(response.entered.wait(), timeout=1)
            self.assertEqual(await self.feeds.record(record), (429, None))
            self.assertEqual(await asyncio.wait_for(self.feeds.catalog(NEWS_ENDPOINT), timeout=0.5), [])
        finally:
            response.release.set()
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
