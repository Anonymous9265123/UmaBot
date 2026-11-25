import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import discord
from discord import app_commands

from umabot.alerts import UmaAlerts, scheduled_check_time, send_user_ping
from umabot.commands.lookup import register_uma, resolve_support_id
from umabot.game.calendar import CM_FORECAST_CATALOG
from umabot.game.feeds import UmaFeeds
from umabot.stats import StatsButton, SupportComparisonView


class AlertScheduleTests(unittest.TestCase):
    def test_window_crosses_midnight(self):
        scheduled = datetime(2026, 11, 23, 23, 59)
        self.assertEqual(scheduled_check_time(scheduled + timedelta(minutes=1), 1439), scheduled)
        self.assertIsNone(scheduled_check_time(scheduled + timedelta(minutes=5), 1439))


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_snapshots_reuse_data_and_update_at_release_time(self):
        release = datetime(2026, 11, 25, tzinfo=timezone.utc)
        records = {
            "support-cards": [{"support_id": 123, "title_en": "Test Card", "release_en": release.isoformat()}],
            "character-cards": [{"char_id": 10, "name_en": "Test Trainee", "release_en": release.isoformat()}],
        }
        feeds = UmaFeeds()
        feeds.catalog = AsyncMock(side_effect=lambda endpoint: records[endpoint])
        timestamp = release.timestamp()
        with patch("umabot.game.feeds.time", return_value=timestamp - 1):
            support = await feeds.support_releases()
            trainee = await feeds.trainee_releases()
            self.assertIs(await feeds.support_releases(), support)
            self.assertIs(await feeds.trainee_releases(), trainee)
            self.assertEqual(support[0], {})
            self.assertEqual(trainee[0]["trainee:10"], "Unreleased")
        with patch("umabot.game.feeds.time", return_value=timestamp):
            self.assertIn("123", (await feeds.support_releases())[0])
            self.assertEqual((await feeds.trainee_releases())[0]["trainee:10"], "Released")
            records["support-cards"] = []
            self.assertEqual((await feeds.support_releases())[0], {})

    async def test_events_and_champions_meeting_forecast_use_separate_feeds(self):
        feeds = UmaFeeds()
        feeds.load_events = AsyncMock(return_value=[])
        forecast = {"id": 23}
        feeds.versioned_catalog = AsyncMock(return_value={"future_cm": [forecast]})
        self.assertEqual(await feeds.events(), [])
        self.assertEqual(await feeds.catalog(CM_FORECAST_CATALOG), [forecast])


class SupportAutocompleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_data_is_preferred_and_fallback_is_lazy(self):
        primary = {"id": 1, "title_en": "First Card"}
        fallback = {"id": 2, "title_en": "Second Card"}
        catalog = AsyncMock(return_value=[primary])
        feeds = UmaFeeds()
        feeds.catalog = catalog
        async with discord.Client(intents=discord.Intents.none()) as client:
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=1)
            register_uma(tree, guild, feeds=feeds)
            command = tree.get_command("uma", guild=guild).get_command("support")
            autocomplete = command._params["name"].autocomplete
            self.assertEqual([choice.value for choice in await autocomplete(None, "")], ["1"])
            catalog.assert_awaited_once_with("gametora", timeout=1.0)
            catalog.reset_mock()
            catalog.side_effect = [[], [fallback]]
            self.assertEqual([choice.value for choice in await autocomplete(None, "")], ["2"])
            self.assertEqual(catalog.await_args_list, [call("gametora", timeout=1.0), call("support", timeout=1.2)])

    async def test_character_autocomplete_reuses_normalized_queries_until_refresh(self):
        records = [{"id": 1, "name_en": "Special Week"}]
        feeds = UmaFeeds()
        feeds.catalog = AsyncMock(return_value=records)
        async with discord.Client(intents=discord.Intents.none()) as client:
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=1)
            register_uma(tree, guild, feeds=feeds)
            command = tree.get_command("uma", guild=guild).get_command("character")
            autocomplete = command._params["name"].autocomplete
            self.assertEqual([choice.value for choice in await autocomplete(None, "special")], ["1"])
            index = feeds.autocomplete_index("character/list", records)
            with patch.object(index, "rank", wraps=index.rank) as rank:
                choices = await autocomplete(None, "SPECIAL!")
                self.assertEqual([choice.value for choice in choices], ["1"])
                rank.assert_not_called()
            feeds.catalog.return_value = [{"id": 2, "name_en": "Special Week"}]
            self.assertEqual([choice.value for choice in await autocomplete(None, "special")], ["2"])

    async def test_support_normalization_stays_stable_for_the_same_raw_catalog(self):
        feeds = UmaFeeds()
        feeds.versioned_catalog = AsyncMock(return_value=[{"support_id": 1, "char_name": "Special Week"}])
        first = await feeds.catalog("gametora")
        self.assertIs(await feeds.catalog("gametora"), first)
        index = feeds.autocomplete_index("gametora", first)
        self.assertIs(index.suggestions("Special"), index.suggestions("special!"))
        for query in range(129):
            index.suggestions(f"query{query}")
        self.assertLessEqual(index._cached_suggestions.cache_info().currsize, 128)
        feeds.versioned_catalog.return_value = [{"support_id": 2, "char_name": "Special Week"}]
        refreshed = await feeds.catalog("gametora")
        self.assertIsNot(refreshed, first)
        self.assertEqual(feeds.autocomplete_index("gametora", refreshed).suggestions("Special")[0][2], 2)


class SkillAutocompleteTests(unittest.IsolatedAsyncioTestCase):
    async def test_suggestions_preserve_owner_aliases_and_rebuild_only_affected_indexes(self):
        rows = {
            "skills": [
                {"id": 1, "rarity": 1, "loc": {"en": {"name_en": "Corner Bonus"}}},
                {"id": 2, "rarity": 3, "char": [10, 20], "loc": {"en": {"name_en": "Racing Star"}}},
                {"id": 3, "rarity": 3, "name_en": "Inherited", "inherited": True},
            ],
            "character-cards": [
                {"card_id": 10, "name_en": "Alpha", "title_en_gl": "[Classic]"},
                {"card_id": 20, "name_en": "Beta", "title_en_gl": "[Festival]"},
            ],
        }
        feeds = UmaFeeds()
        feeds.catalog = AsyncMock(side_effect=lambda endpoint, **kwargs: rows[endpoint])
        async with discord.Client(intents=discord.Intents.none()) as client:
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=1)
            register_uma(tree, guild, feeds=feeds)
            group = tree.get_command("uma", guild=guild)
            shared_choices = group.get_command("skill")._params["name"].autocomplete
            unique_choices = group.get_command("unique")._params["name"].autocomplete
            shared = await feeds.skills(unique=False)
            unique = await feeds.skills(unique=True)
            self.assertEqual([choice.value for choice in await shared_choices(None, "corner")], ["1"])
            matching = await unique_choices(None, "Beta [Festival]")
            self.assertEqual([(choice.name, choice.value) for choice in matching], [
                ("Racing Star — Beta [Festival] +1", "2"),
            ])
            self.assertEqual([choice.value for choice in await unique_choices(None, "Alpha")], ["2"])
            self.assertEqual(list(unique.records_by_id), [2])
            self.assertIs(await feeds.skills(unique=True), unique)

            rows["character-cards"] = [{"card_id": 20, "name_en": "Beta", "title_en_gl": "[Parade]"}]
            refreshed = await unique_choices(None, "Beta [Parade]")
            self.assertEqual(refreshed[0].name, "Racing Star — Beta [Parade]")
            self.assertIsNot(await feeds.skills(unique=True), unique)
            self.assertIs(await feeds.skills(unique=False), shared)

            rows["skills"] = [{"id": 4, "rarity": 1, "name_en": "Corner Bonus"}]
            self.assertEqual([choice.value for choice in await shared_choices(None, "corner")], ["4"])
            self.assertEqual(await unique_choices(None, "Beta"), [])


class DigestTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_and_deduplicate_after_restart(self):
        channel = SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(get_partial_messageable=lambda _: channel)
        feeds = SimpleNamespace(events=AsyncMock(return_value=[]), projections=AsyncMock(return_value=[]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.sqlite3"
            alerts = UmaAlerts(client, 1, path, feeds)
            with patch.object(type(alerts.poll), "start"):
                alerts.start()
            alerts.set_digest(10)
            due = datetime(2026, 11, 23, 9)
            await alerts.check_digest(due - timedelta(minutes=1))
            channel.send.assert_not_awaited()
            response = SimpleNamespace(status=500, reason="fixture")
            channel.send.side_effect = discord.HTTPException(response, "retry")
            with self.assertLogs("umabot.alerts", level="WARNING"):
                await alerts.check_digest(due)
            channel.send.side_effect = None
            await alerts.check_digest(due + timedelta(minutes=1))
            restarted = UmaAlerts(client, 1, path, feeds)
            await restarted.check_digest(due + timedelta(hours=1))
            self.assertEqual(channel.send.await_count, 2)
            self.assertEqual(
                channel.send.await_args_list[0].kwargs["nonce"], channel.send.await_args_list[1].kwargs["nonce"],
            )
            await restarted.check_digest(due + timedelta(days=7))
            self.assertEqual(channel.send.await_count, 3)


class AlertDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_only_allows_selected_users(self):
        channel = SimpleNamespace(send=AsyncMock())
        await send_user_ping(channel, [12, 34], "Reminder @everyone <@56>")
        args, kwargs = channel.send.await_args
        self.assertEqual(args[0], "<@12> <@34>\nReminder @everyone <@56>")
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"users": [12, 34], "parse": []})


class ComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_stats_first_and_exclusive_stats_sorted(self):
        cards = [
            {"char_name": "First", "rarity": 3, "effects": [[4, 5], [2, 15], [1, 0]]},
            {"char_name": "Second", "rarity": 3, "effects": [[2, 5], [3, 10], [1, 0]]},
        ]
        names = {1: "Beta shared", 2: "Alpha shared", 3: "alpha exclusive", 4: "Zulu exclusive"}
        view = SupportComparisonView(cards, (4, 4), {key: {"name_en": name} for key, name in names.items()}, 1)
        description = view.effects_embed().description
        offsets = [description.index(names[key]) for key in (2, 1, 3, 4)]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("A **15** | B 5", description)
        self.assertIn("A 0 | B 0", description)

    async def test_stats_button_uses_card_level_and_effect_definitions(self):
        card = {"support_id": 123, "char_name": "Test Trainee", "rarity": 2, "effects": [[1, 5]]}
        definitions = [{"id": 1, "name_en": "Training Effect", "symbol": "percent"}]
        feeds = SimpleNamespace(catalog=AsyncMock(side_effect=[[card], definitions]))
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            client=SimpleNamespace(feeds=feeds),
            edit_original_response=AsyncMock(),
        )
        await StatsButton(123).callback(interaction)
        embed = interaction.edit_original_response.await_args.kwargs["embed"]
        self.assertIn("Lv 45", embed.description)
        self.assertIn("5%", embed.fields[0].value)

    def test_support_lookup_accepts_id_or_card_title(self):
        records = [{"id": 123, "title_en": "Training for Victory", "name_en": "Test Trainee"}]
        self.assertEqual(resolve_support_id(records, "123"), 123)
        self.assertEqual(resolve_support_id(records, "training for victory"), 123)
        self.assertIsNone(resolve_support_id(records, "???"))
