import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands

from umabot.alert_settings import PostingDestination
from umabot.alerts import UmaAlerts
from umabot.commands.lookup import register_uma


class PostingDestinationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.feeds = SimpleNamespace(
            catalog=AsyncMock(side_effect=AssertionError("Unexpected catalog request")),
            events=AsyncMock(side_effect=AssertionError("Unexpected event request")),
            projections=AsyncMock(side_effect=AssertionError("Unexpected forecast request")),
        )
        self.alerts = UmaAlerts(SimpleNamespace(), 1, Path(directory) / "alerts.sqlite3", self.feeds)
        with patch.object(type(self.alerts.poll), "start"):
            self.alerts.start()
        self.channel = SimpleNamespace(
            id=20,
            mention="<#20>",
            permissions_for=Mock(return_value=discord.Permissions(
                view_channel=True, send_messages=True, embed_links=True,
            )),
            send=AsyncMock(),
        )
        self.interaction = SimpleNamespace(
            guild=SimpleNamespace(me=object(), get_channel=Mock(return_value=self.channel)),
            user=object(),
            permissions=SimpleNamespace(manage_guild=True),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def click_test_post(self, view):
        if await view.interaction_check(self.interaction):
            await view.test_post.callback(self.interaction)

    def saved_alert_state(self):
        with self.alerts.database() as db:
            return (
                db.execute("SELECT * FROM news_settings").fetchall(),
                db.execute("SELECT * FROM news_deliveries").fetchall(),
                db.execute("SELECT * FROM event_deliveries").fetchall(),
                db.execute("SELECT * FROM reminder_deliveries").fetchall(),
                db.execute("SELECT * FROM alert_schedules").fetchall(),
                db.execute("SELECT * FROM subscriptions").fetchall(),
            )

    async def test_setup_and_news_keep_the_test_button_after_saving(self):
        async with discord.Client(intents=discord.Intents.none()) as client:
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=1)
            register_uma(tree, guild, feeds=self.feeds, alerts=self.alerts)
            group = tree.get_command("uma", guild=guild)
            for command in ("setup", "news"):
                with self.subTest(command=command):
                    await group.get_command(command).callback(self.interaction)
                    reply = self.interaction.response.send_message.await_args.kwargs
                    view = reply["view"]
                    self.assertTrue(reply["ephemeral"])
                    self.assertIsInstance(view.destination, discord.ui.ChannelSelect)
                    self.assertEqual(view.test_post.label, "Send test post")
                    selection = SimpleNamespace(values=[SimpleNamespace(id=self.channel.id)])
                    await PostingDestination.destination(view, self.interaction, selection)
                    self.assertIs(self.interaction.response.edit_message.await_args.kwargs["view"], view)
                    self.assertFalse(view.disable.disabled)
            self.channel.send.assert_not_awaited()

    async def test_success_posts_one_embed_without_mentions_or_alert_state_changes(self):
        self.alerts.set_posting_channel(10)
        view = PostingDestination(self.alerts)
        self.alerts.set_posting_channel(self.channel.id)
        self.alerts.subscribe("123", 42, self.channel.id, 540)
        with self.alerts.database() as db:
            db.execute("INSERT INTO news_deliveries VALUES (1, 'previous-news')")
        before = self.saved_alert_state()

        async def send(**kwargs):
            self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            self.interaction.edit_original_response.assert_not_awaited()

        self.channel.send.side_effect = send
        await self.click_test_post(view)

        self.interaction.guild.get_channel.assert_called_once_with(self.channel.id)
        self.channel.send.assert_awaited_once()
        message = self.channel.send.await_args
        self.assertEqual(message.args, ())
        self.assertEqual(set(message.kwargs), {"embed", "allowed_mentions"})
        self.assertEqual(message.kwargs["embed"].title, "UmaBot test alert")
        self.assertIn("sent manually to verify alert delivery", message.kwargs["embed"].description)
        self.assertEqual(message.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.interaction.edit_original_response.assert_awaited_once_with(
            content="UmaBot successfully posted an embed in <#20>.",
        )
        self.assertEqual(self.saved_alert_state(), before)
        self.feeds.catalog.assert_not_awaited()
        self.feeds.events.assert_not_awaited()
        self.feeds.projections.assert_not_awaited()

    async def test_missing_configuration_prompts_manager_to_save_a_channel(self):
        await self.click_test_post(PostingDestination(self.alerts))
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.interaction.edit_original_response.assert_awaited_once_with(
            content="Select and save a posting channel first.",
        )
        self.interaction.guild.get_channel.assert_not_called()
        self.channel.send.assert_not_awaited()

    async def test_unauthorized_click_is_rejected_privately(self):
        self.alerts.set_posting_channel(self.channel.id)
        view = PostingDestination(self.alerts)
        self.interaction.permissions.manage_guild = False
        await self.click_test_post(view)
        self.interaction.response.send_message.assert_awaited_once_with(
            "You need Manage Server to configure UmaBot posts.", ephemeral=True,
        )
        self.interaction.response.defer.assert_not_awaited()
        self.interaction.guild.get_channel.assert_not_called()
        self.channel.send.assert_not_awaited()

    async def test_deleted_channel_prompts_manager_to_choose_another(self):
        self.alerts.set_posting_channel(self.channel.id)
        self.interaction.guild.get_channel.return_value = None
        await self.click_test_post(PostingDestination(self.alerts))
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertIn("Select and save another", self.interaction.edit_original_response.await_args.kwargs["content"])
        self.channel.send.assert_not_awaited()

    async def test_send_failure_gives_permission_guidance(self):
        self.alerts.set_posting_channel(self.channel.id)
        self.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "fixture")
        await self.click_test_post(PostingDestination(self.alerts))
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.channel.send.assert_awaited_once()
        reply = self.interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("View Channel, Send Messages, and Embed Links", reply)
        self.assertEqual(self.saved_alert_state()[1], [])
