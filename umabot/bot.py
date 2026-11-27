"""Run UmaBot and register its commands in joined Discord servers."""

import asyncio
import logging
import sqlite3

import discord
from discord import app_commands

from .alerts import AlertButton, UmaAlerts
from .commands.feedback import FeedbackInbox
from .commands.lookup import register_uma
from .config import discord_token
from .game.feeds import UmaFeeds
from .paths import runtime_database_path, unnumbered_alert_databases
from .stats import StatsButton


log = logging.getLogger(__name__)


class UmaBot(discord.Client):
    """Share game feeds across servers and manage each server's alert loop."""

    def __init__(self):
        super().__init__(
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.feeds = UmaFeeds()
        self.feedback = FeedbackInbox(runtime_database_path("uma-feedback.sqlite3"))
        self.alerts_by_guild = {}
        self.syncing_guilds = set()
        self.synced_guilds = set()
        self.search_preload = None
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        unnumbered = unnumbered_alert_databases()
        if unnumbered:
            log.warning(
                "UmaBot found unused unnumbered alert databases at %s. "
                "See README.md under 'Migrate an older alert database' before copying any data.",
                ", ".join(str(path) for path in unnumbered),
            )
        self.add_dynamic_items(AlertButton, StatsButton)
        self.feedback.path.parent.mkdir(parents=True, exist_ok=True)
        self.feedback.initialize()

    async def setup_guild(self, guild):
        """Initialize the server's alert state and sync its slash commands."""
        guild_id = guild.id
        if guild_id in self.synced_guilds or guild_id in self.syncing_guilds:
            return
        self.syncing_guilds.add(guild_id)
        try:
            if guild_id not in self.alerts_by_guild:
                path = runtime_database_path(f"uma-alerts-{guild_id}.sqlite3")
                path.parent.mkdir(parents=True, exist_ok=True)
                alerts = UmaAlerts(self, guild_id, path, self.feeds)
                alerts.start()
                register_uma(self.tree, guild, feeds=self.feeds, alerts=alerts, feedback=self.feedback)
                self.alerts_by_guild[guild_id] = alerts
            await self.tree.sync(guild=guild)
            if guild_id in self.alerts_by_guild:
                self.synced_guilds.add(guild_id)
        except (discord.HTTPException, sqlite3.Error, OSError):
            log.exception("Could not finish UmaBot setup in server %s", guild_id)
        finally:
            self.syncing_guilds.discard(guild_id)

    async def on_guild_join(self, guild):
        await self.setup_guild(guild)

    async def on_guild_remove(self, guild):
        alerts = self.alerts_by_guild.pop(guild.id, None)
        if alerts is not None:
            alerts.poll.cancel()
        self.synced_guilds.discard(guild.id)
        self.tree.remove_command("uma", guild=guild)

    async def close(self):
        for alerts in self.alerts_by_guild.values():
            alerts.poll.cancel()
        if self.search_preload is not None:
            self.search_preload.cancel()
            await asyncio.gather(self.search_preload, return_exceptions=True)
        await self.feeds.close()
        await super().close()

    async def on_ready(self):
        for guild in self.guilds:
            await self.setup_guild(guild)
        print(
            f"UmaBot is online as {self.user}. "
            "Use /uma setup to choose the channel for automatic updates and pings."
        )
        if self.search_preload is None and not self.feeds.closed:
            self.search_preload = asyncio.create_task(self.feeds.preload_search(), name="umabot-search-preload")


async def run_bot():
    """Start the Discord client with the configured token."""
    token = discord_token()
    async with UmaBot() as bot:
        await bot.start(token)


def main():
    discord.utils.setup_logging()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
