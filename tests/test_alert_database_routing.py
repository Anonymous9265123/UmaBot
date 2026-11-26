import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import tasks

from umabot.alerts import UmaAlerts
from umabot.bot import UmaBot


class AlertDatabaseRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.storage = self.root / "storage"
        self.storage.mkdir()
        self.enterContext(patch("umabot.paths.PROJECT_ROOT", self.root))
        self.enterContext(patch.dict(os.environ, {
            "UMABOT_DATA_DIR": str(self.storage),
            "DISCORD_GUILD_ID": "101",
        }))
        self.enterContext(patch.object(tasks.Loop, "start"))

    def seed_subscription(self, path, guild_id, topic):
        alerts = UmaAlerts(None, guild_id, path, None)
        alerts.start()
        alerts.subscribe(topic, 42, 20, None)

    async def test_existing_numbered_databases_are_authoritative_and_separate(self):
        first = self.storage / "uma-alerts-101.sqlite3"
        second = self.storage / "uma-alerts-202.sqlite3"
        unnumbered = self.storage / "uma-alerts.sqlite3"
        old_local = self.root / "uma-alerts.sqlite3"
        self.seed_subscription(first, 101, "111")
        self.seed_subscription(second, 202, "222")
        self.seed_subscription(unnumbered, 101, "333")
        self.seed_subscription(old_local, 101, "444")
        original_files = {path: path.read_bytes() for path in (unnumbered, old_local)}

        async with UmaBot() as bot:
            with (
                patch.object(bot.tree, "sync", new_callable=AsyncMock) as sync,
                self.assertLogs("umabot.bot", level="WARNING") as logs,
            ):
                await bot.setup_hook()
                await bot.setup_guild(discord.Object(id=101))
                await bot.setup_guild(discord.Object(id=202))
            self.assertEqual(sync.await_count, 2)
            self.assertEqual(bot.alerts_by_guild[101].path, first)
            self.assertEqual(bot.alerts_by_guild[202].path, second)
            self.assertEqual(bot.alerts_by_guild[101].subscription_ids(42, characters=False), [111])
            self.assertEqual(bot.alerts_by_guild[202].subscription_ids(42, characters=False), [222])
            self.assertEqual(bot.feedback.path, self.storage / "uma-feedback.sqlite3")
            self.assertTrue(bot.feedback.path.is_file())

        self.assertEqual(len(logs.output), 1)
        self.assertIn("Migrate an older alert database", logs.output[0])
        for path, content in original_files.items():
            self.assertIn(str(path), logs.output[0])
            self.assertEqual(path.read_bytes(), content)
