import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import discord
from discord import app_commands

from umabot.alerts import UmaAlerts, scheduled_check_time, send_user_ping
from umabot.commands.lookup import register_uma
from umabot.game.calendar import CM_CATALOG, CM_FORECAST_CATALOG
from umabot.game.feeds import UmaFeeds
from umabot.game.racetracks import track_search
from umabot.game.search import NameSearch
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

    def test_name_resolution_accepts_ascii_ids_and_rejects_empty_or_unmatched_names(self):
        records = [{"id": 123, "title_en": "Training for Victory", "name_en": "Test Trainee"}]
        index = NameSearch(records, support=True)
        self.assertEqual(index.resolve_id(" 00456 "), 456)
        self.assertEqual(index.resolve_id("training for victory"), 123)
        for query in ("", "  ", "???", "zzz", "１２３"):
            with self.subTest(query=query):
                self.assertIsNone(index.resolve_id(query))


class CatalogLookupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.feeds = UmaFeeds()
        client = await self.enterAsyncContext(discord.Client(intents=discord.Intents.none()))
        tree = app_commands.CommandTree(client)
        guild = discord.Object(id=1)
        register_uma(tree, guild, feeds=self.feeds)
        self.group = tree.get_command("uma", guild=guild)

    def interaction(self, *, track=None):
        return SimpleNamespace(
            user=SimpleNamespace(id=10),
            namespace=SimpleNamespace(track=track),
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_support_autocomplete_and_compare_share_index_and_select_both_cards(self):
        rows = {
            "support-cards": [
                {"support_id": 1, "char_name": "Special Week", "title_en": "Early Start", "rarity": 3, "effects": [[1, 5]]},
                {"support_id": 2, "char_name": "Silence Suzuka", "title_en": "Final Stretch", "rarity": 2, "effects": [[1, 8]]},
            ],
            "support_effects": [{"id": 1, "name_en": "Training Effect", "symbol": "percent"}],
        }
        self.feeds.versioned_catalog = AsyncMock(side_effect=lambda endpoint, empty, **kwargs: rows[endpoint])
        autocomplete = self.group.get_command("support")._params["name"].autocomplete
        self.assertEqual([choice.value for choice in await autocomplete(None, "Early Start")], ["1"])
        index = self.feeds.autocomplete_indexes["gametora"][1]
        compare = self.group.get_command("compare")
        interaction = self.interaction()
        with (
            patch("umabot.game.feeds.NameSearch", side_effect=AssertionError("Already indexed")),
            patch.object(index, "resolve_id", wraps=index.resolve_id) as resolve,
        ):
            await compare.callback(interaction, "Final Stretch", 2, "Early Start")
            self.assertEqual(resolve.call_args_list, [call("Final Stretch"), call("Early Start")])
        view = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertEqual([card["support_id"] for card in view.cards], [2, 1])
        self.assertEqual(view.limit_breaks, (2, 4))
        await compare.callback(interaction, "Final Stretch", 2, "???")
        self.assertIn("No match for the second support card", interaction.edit_original_response.await_args.kwargs["content"])

    async def test_numeric_character_lookup_skips_catalog(self):
        self.feeds.catalog = AsyncMock()
        command = self.group.get_command("character")
        interaction = self.interaction()
        with patch("umabot.commands.lookup.lookup", new_callable=AsyncMock) as lookup:
            await command.callback(interaction, " 00123 ")
        self.feeds.catalog.assert_not_awaited()
        lookup.assert_awaited_once_with(interaction, "character/00123", "Character details", character_profile=True, alerts=None)

    async def test_support_lookup_falls_back_to_umapyoi_without_using_first_suggestion(self):
        self.feeds.catalog = AsyncMock(side_effect=lambda endpoint, **kwargs: {
            "gametora": [], "support": [{"id": 2, "title_en": "Final Stretch"}],
        }[endpoint])
        command = self.group.get_command("support")
        interaction = self.interaction()
        with patch("umabot.commands.lookup.lookup", new_callable=AsyncMock) as lookup:
            for query in ("   ", "???"):
                await command.callback(interaction, query)
                self.assertEqual(interaction.edit_original_response.await_args.kwargs["content"], "No matching support card found.")
            lookup.assert_not_awaited()
            await command.callback(interaction, "Final Stretch")
            lookup.assert_awaited_once_with(interaction, "support/2", "Support card details", support_metadata={}, alerts=None)

    async def test_track_commands_reuse_join_and_refresh_after_either_source_changes(self):
        rows = {
            "racetracks": [{"id": "7", "courses": [
                {"id": 20, "length": 1800, "terrain": 99, "turn": 99, "corners": [{}, {}],
                 "straights": [{"start": 1500, "end": 1800}], "spurtStart": {"meters": 1200}},
                {"id": 10, "length": 1200, "terrain": 2, "turn": 4},
            ]}],
            "racetracks_extended": [{"id": 7, "name_en": "Example Park"}],
            CM_CATALOG: [{"id": 1, "name_en": "Example Cup", "start": 90, "end": 110,
                          "race": {"track": 7, "distance": 1800, "ground": 99, "turn": 99}}],
        }
        self.feeds.catalog = AsyncMock(side_effect=lambda endpoint, **kwargs: rows[endpoint])
        track = self.group.get_command("track")
        names = track._params["track"].autocomplete
        distances = track._params["distance"].autocomplete
        interaction = self.interaction(track="Example")
        with patch("umabot.game.feeds.track_search", wraps=track_search) as build:
            await track.callback(interaction, "Example")
            embed = interaction.edit_original_response.await_args.kwargs["embed"]
            self.assertLess(embed.description.index("1200 m"), embed.description.index("1800 m"))
            self.assertIn("Dirt · Straight", embed.description)
            self.assertIn("Unknown surface · Unknown direction", embed.description)
            self.assertEqual([choice.value for choice in await names(interaction, "example")], ["7"])
            self.assertEqual([choice.value for choice in await distances(interaction, 1)], [1200, 1800])
            await track.callback(interaction, "7", 1800)
            field = interaction.edit_original_response.await_args.kwargs["embed"].fields[0]
            self.assertEqual(field.value, "Corners: 2\nFinal straight: 300 m\nLast spurt starts at 1,200 m")
            build.assert_called_once()

            with patch("umabot.commands.community.time", return_value=100):
                await self.group.get_command("cm").callback(interaction)
            fields = interaction.edit_original_response.await_args.kwargs["embed"].fields
            self.assertEqual(fields[0].value, "Example Park · 1,800 m")

            rows["racetracks"] = [{"id": "7", "courses": [{"id": 30, "length": 2200, "terrain": 1, "turn": 1}]}]
            self.assertEqual([choice.value for choice in await distances(interaction, 2)], [2200])
            self.assertEqual(build.call_count, 2)
            rows["racetracks_extended"] = [{"id": 7, "name_en": "New Park"}]
            self.assertEqual([choice.name for choice in await names(interaction, "new")], ["New Park"])
            self.assertEqual(build.call_count, 3)
