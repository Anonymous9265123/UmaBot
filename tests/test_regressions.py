import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from umabot.alerts import UmaAlerts, scheduled_check_time, send_user_ping
from umabot.commands.lookup import resolve_support_id
from umabot.stats import StatsButton, SupportComparisonView


class AlertScheduleTests(unittest.TestCase):
    def test_window_crosses_midnight(self):
        scheduled = datetime(2026, 11, 23, 23, 59)
        self.assertEqual(scheduled_check_time(scheduled + timedelta(minutes=1), 1439), scheduled)
        self.assertIsNone(scheduled_check_time(scheduled + timedelta(minutes=5), 1439))


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
