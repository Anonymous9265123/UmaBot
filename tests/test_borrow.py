import json
import unittest
from base64 import b64decode
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import discord
from discord import app_commands

from umabot.commands.borrow import trainer_search_url
from umabot.commands.lookup import register_uma
from umabot.game.feeds import UmaFeeds


NOW = 1_800_000_000


def release_at(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class BorrowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch("aiohttp.ClientSession", side_effect=AssertionError("Unexpected live HTTP request")))
        self.enterContext(patch("umabot.commands.borrow.time", return_value=NOW))
        self.feeds = UmaFeeds()
        self.feeds.umamoe_api_key = ""
        self.feeds.versioned_catalog = AsyncMock(return_value=[
            {"support_id": 30014, "char_name": "Same Trainee", "title_en": "First Card", "release_en": release_at(NOW)},
            {"support_id": 30015, "char_name": "Same Trainee", "title_en": "Second Card", "release_en": release_at(NOW - 1)},
            {"support_id": 30016, "title_en": "Future Card", "release_en": release_at(NOW + 3600)},
            {"support_id": 30017, "title_en": "Unreleased Card", "release_en": None},
            {"support_id": 30018, "title_en": "Invalid Date", "release_en": "not a date"},
            {"support_id": 30019, "title_en": "Missing Date"},
        ])
        client = await self.enterAsyncContext(discord.Client(intents=discord.Intents.none()))
        tree = app_commands.CommandTree(client)
        guild = discord.Object(id=1)
        register_uma(tree, guild, feeds=self.feeds)
        group = tree.get_command("uma", guild=guild)
        self.find = group.get_command("borrow").get_command("find")
        self.support = group.get_command("support")
        self.interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def test_link_matches_the_public_example(self):
        self.assertEqual(
            trainer_search_url(30014, 4),
            "https://uma.moe/database?filters=eyJzYyI6IjMwMDE0IiwibGIiOjR9",
        )

    async def test_autocomplete_keeps_cards_distinct_and_links_use_exact_id_and_min_lb(self):
        autocomplete = self.find._params["card"].autocomplete
        self.assertIs(autocomplete, self.support._params["name"].autocomplete)
        choices = await autocomplete(None, "Same Trainee")
        self.assertEqual({choice.value for choice in choices}, {"30014", "30015"})
        for card, limit_break in (("30014", None), ("30015", 3)):
            with self.subTest(card=card):
                if limit_break is None:
                    await self.find.callback(self.interaction, card)
                else:
                    await self.find.callback(self.interaction, card, min_lb=limit_break)
                reply = self.interaction.edit_original_response.await_args.kwargs
                button, = reply["view"].children
                self.assertEqual(button.label, "Find trainers on uma.moe")
                link = urlsplit(button.url)
                self.assertEqual((link.scheme, link.netloc, link.path), ("https", "uma.moe", "/database"))
                filters = json.loads(b64decode(parse_qs(link.query)["filters"][0]))
                self.assertEqual(filters, {"sc": card, "lb": limit_break or 0})
                self.assertIn(f"{limit_break or 0}LB or higher", reply["embed"].description)

    async def test_future_unreleased_and_unknown_ids_get_no_search_link(self):
        self.feeds.borrow_trainers = AsyncMock()
        for card, message in (
            ("30016", f"This support card releases on EN on <t:{NOW + 3600}:D>."),
            ("30017", "This support card is unreleased on EN."),
            ("30018", "I couldn’t confirm this card’s EN release."),
            ("30019", "I couldn’t confirm this card’s EN release."),
            ("99999", "I couldn’t confirm this card’s EN release."),
        ):
            with self.subTest(card=card):
                await self.find.callback(self.interaction, card)
                self.assertEqual(self.interaction.edit_original_response.await_args.kwargs, {"content": message})
        self.assertEqual(
            {call.args[0] for call in self.feeds.versioned_catalog.await_args_list}, {"support-cards"},
        )
        self.feeds.borrow_trainers.assert_not_awaited()

    async def test_direct_results_and_empty_or_unavailable_responses(self):
        self.feeds.umamoe_api_key = "test-key"
        self.feeds.borrow_trainers = AsyncMock(side_effect=[
            [("001234567", 4, "Trainer", "2026-11-25")], [], None,
        ])
        for expected in ("`001234567` · 4LB · Trainer · Updated 2026-11-25",
                         "No matching trainer listings found", "Direct listings are temporarily unavailable"):
            await self.find.callback(self.interaction, "30014", min_lb=3)
            reply = self.interaction.edit_original_response.await_args.kwargs
            self.assertIn(expected, reply["embed"].description)
            self.assertEqual(reply["view"].children[0].label, "Find trainers on uma.moe")
        self.assertEqual(self.feeds.borrow_trainers.await_count, 3)

    async def test_character_name_without_an_exact_selection_does_not_choose_a_variant(self):
        await self.find.callback(self.interaction, "Same Trainee")
        self.assertIn("Choose a support card", self.interaction.edit_original_response.await_args.kwargs["content"])
        self.feeds.versioned_catalog.assert_not_awaited()
