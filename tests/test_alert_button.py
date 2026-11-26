import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from umabot.alert_settings import AlertButton, AlertPreferences
from umabot.alerts import UmaAlerts


class AlertButtonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.alerts = UmaAlerts(SimpleNamespace(), 1, Path(directory) / "alerts.sqlite3", SimpleNamespace())
        with patch.object(type(self.alerts.poll), "start"):
            self.alerts.start()
        self.alerts.set_posting_channel(20)
        permissions = discord.Permissions(view_channel=True, send_messages=True)
        channel = SimpleNamespace(id=20, mention="<#20>", permissions_for=lambda _: permissions)
        self.interaction = SimpleNamespace(
            client=SimpleNamespace(alerts_by_guild={1: self.alerts}),
            guild_id=1,
            channel_id=20,
            guild=SimpleNamespace(me=object(), get_channel=lambda _: channel),
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_saved_schedule_subscribes_once_and_confirms_without_view(self):
        self.alerts.set_schedule(42, 540)
        button = AlertButton("events")
        with patch.object(self.alerts, "subscribe", wraps=self.alerts.subscribe) as subscribe:
            await button.callback(self.interaction)
        subscribe.assert_called_once_with("events", 42, 20, 540)
        self.assertTrue(self.alerts.is_subscribed("events", 42))
        reply = self.interaction.response.send_message
        reply.assert_awaited_once()
        self.assertEqual(reply.await_args.kwargs, {"ephemeral": True})
        self.assertIn("daily at 09:00", reply.await_args.args[0])

        reply.reset_mock()
        await button.callback(self.interaction)
        self.assertFalse(self.alerts.is_subscribed("events", 42))
        reply.assert_awaited_once()
        self.assertIn("Unsubscribed", reply.await_args.args[0])

    async def test_first_subscription_shows_preferences_and_saves_on_submit(self):
        await AlertButton("events").callback(self.interaction)
        reply = self.interaction.response.send_message
        reply.assert_awaited_once()
        self.assertTrue(reply.await_args.kwargs["ephemeral"])
        view = reply.await_args.kwargs["view"]
        self.assertIsInstance(view, AlertPreferences)
        self.assertFalse(self.alerts.is_subscribed("events", 42))

        await view.save.callback(self.interaction)
        self.assertTrue(self.alerts.is_subscribed("events", 42))
        self.assertEqual(self.alerts.schedule(42), (None,))
        edit = self.interaction.response.edit_message
        edit.assert_awaited_once()
        self.assertIsNone(edit.await_args.kwargs["view"])

    async def test_saved_trainee_schedule_confirms_via_deferred_edit(self):
        self.alerts.set_schedule(42, None)
        with patch.object(self.alerts, "trainee_status", return_value="Upcoming") as status:
            await AlertButton("trainee:123").callback(self.interaction)
        status.assert_awaited_once_with("trainee:123")
        self.assertTrue(self.alerts.is_subscribed("trainee:123", 42))
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.interaction.response.send_message.assert_not_awaited()
        edit = self.interaction.edit_original_response
        edit.assert_awaited_once()
        self.assertIsNone(edit.await_args.kwargs["view"])
        self.assertIn("Subscribed", edit.await_args.kwargs["content"])
