"""Discord controls for alert subscriptions and posting settings."""

import re

import discord


def alert_channel(alerts, interaction):
    setting = alerts.posting_setting()
    if setting is None:
        raise ValueError("Ask a server manager to choose a channel with /uma setup.")
    guild = interaction.guild
    channel = guild.get_channel(setting[0])
    if channel is None or not channel.permissions_for(interaction.user).view_channel:
        raise ValueError("You need access to the server's UmaBot channel to subscribe.")
    permissions = channel.permissions_for(guild.me)
    if not permissions.view_channel or not permissions.send_messages:
        raise ValueError("UmaBot needs permission to post in the setup channel.")
    return channel


def schedule_text(delivery_minute):
    clock = "" if delivery_minute is None else f"{delivery_minute // 60:02d}:{delivery_minute % 60:02d}"
    return (
        "as soon as a check detects an update"
        if delivery_minute is None else f"daily at {clock} (bot host's local time)"
    )


def subscription_confirmation(channel, delivery_minute):
    return (
        f"Subscribed. I’ll ping you in {channel.mention} {schedule_text(delivery_minute)}. "
        "Use `/uma alert` to change your schedule. Manage your subscription in `/uma subscriptions` "
        "or click the original alert button again to unsubscribe."
    )


class AlertButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"uma-alert:(?P<topic>events|(?:trainee:)?[0-9]+)",
):
    def __init__(self, topic):
        self.topic = topic
        label = "Toggle EN release alert"
        if topic == "events":
            label = "Subscribe or unsubscribe to EN event alerts"
        elif topic.startswith("trainee:"):
            label = "Toggle trainee EN wishlist"
        super().__init__(discord.ui.Button(label=label, emoji="🔔", custom_id=f"uma-alert:{topic}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["topic"])

    async def callback(self, interaction):
        alerts = interaction.client.alerts_by_guild.get(interaction.guild_id)
        if alerts is None or interaction.channel_id is None:
            await interaction.response.send_message("Alerts are available in servers where UmaBot is running.", ephemeral=True)
            return
        user_id = interaction.user.id
        if alerts.is_subscribed(self.topic, user_id):
            alerts.remove_preference(self.topic, user_id)
            await interaction.response.send_message(
                "Unsubscribed from this alert. Use `/uma alert` to change your alert schedule.",
                ephemeral=True,
            )
            return

        try:
            channel = alert_channel(alerts, interaction)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        trainee = self.topic.startswith("trainee:")
        if trainee:
            await interaction.response.defer(ephemeral=True, thinking=True)
            released = await alerts.trainee_status(self.topic) == "Released"

            if released:
                await interaction.edit_original_response(content="This trainee is already available on EN.")
                return

        schedule = alerts.schedule(user_id)
        view = None
        if schedule is None:
            view = AlertPreferences(alerts, topic=self.topic)
            message = view.prompt
        else:
            delivery_minute = alerts.subscribe(self.topic, user_id, channel.id, schedule[0])
            message = subscription_confirmation(channel, delivery_minute)
        if trainee:
            await interaction.edit_original_response(content=message, view=view)
        elif view is None:
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, view=view, ephemeral=True)


def parse_alert_time(value):
    match = re.fullmatch(r"(\d{1,2})(?::([0-5]\d))?\s*(am|pm)?", value.strip(), re.IGNORECASE | re.ASCII)
    if not match:
        raise ValueError("Enter a time like `17:30` or `5:30 PM`.")
    hour, minute, period = match.groups()
    hour = int(hour)
    if period:
        if not 1 <= hour <= 12:
            raise ValueError("Use an hour from 1 to 12 with AM or PM.")
        hour = hour % 12 + (12 if period.lower() == "pm" else 0)
    elif hour > 23 or minute is None:
        raise ValueError("Use a 24-hour time like `17:30` or include AM or PM.")
    return hour * 60 + int(minute or 0)


class CustomAlertSettings(discord.ui.Modal, title="Custom alert settings"):
    delivery_time = discord.ui.Label(
        text="Daily time (bot host's local time)",
        component=discord.ui.TextInput(placeholder="17:30 or 5:30 PM", required=False, max_length=12),
    )
    event_window = discord.ui.Label(
        text="Hours before an event ends",
        component=discord.ui.TextInput(placeholder="1–168 hours", required=False, max_length=3),
    )

    def __init__(self, preferences):
        super().__init__()
        self.preferences = preferences
        if preferences.topic is not None:
            self.remove_item(self.event_window)

    async def on_submit(self, interaction):
        delivery = self.delivery_time.component.value.strip()
        window = self.event_window.component.value.strip() if self.preferences.topic is None else ""
        try:
            if not delivery and not window:
                raise ValueError("Enter a time or an event reminder window.")
            new_time = parse_alert_time(delivery) if delivery else self.preferences.delivery_minute
            if window and (not window.isascii() or not window.isdecimal() or not 1 <= int(window) <= 168):
                raise ValueError("Enter a reminder window from 1 to 168 hours.")
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        self.preferences.delivery_minute = new_time
        if window:
            self.preferences.lead_hours = int(window)
        await self.preferences.show_settings(interaction)


class AlertPreferences(discord.ui.View):
    def __init__(self, alerts, delivery_minute=None, *, topic=None, lead_hours=None):
        super().__init__(timeout=180)
        self.alerts = alerts
        self.topic = topic
        self.delivery_minute = delivery_minute
        self.lead_hours = self.saved_lead_hours = lead_hours
        self.timing.options = [
            discord.SelectOption(label="As soon as eligible", value="hourly"),
            *(discord.SelectOption(label=f"Daily at {hour:02d}:00", value=str(hour * 60))
              for hour in range(24)),
        ]
        if topic is None:
            self.event_window.options = [
                discord.SelectOption(label="Event reminders off", value="off"),
                *(discord.SelectOption(label=f"Within {hours} {'hour' if hours == 1 else 'hours'} of ending", value=str(hours))
                  for hours in (1, 3, 6, 12, 24, 48, 72)),
            ]
        else:
            self.remove_item(self.event_window)
            self.custom.label = "Enter a custom time"
        self.update_defaults()

    @property
    def prompt(self):
        setting = self.alerts.posting_setting()
        destination = f"Alerts go to <#{setting[0]}>." if setting else "A server manager can choose the channel with /uma setup."
        timing = "As soon as eligible" if self.delivery_minute is None else schedule_text(self.delivery_minute).capitalize()
        lines = [f"**Delivery** {timing}", destination]
        if self.topic is None:
            reminder = f"Within {self.lead_hours} h of an event ending" if self.lead_hours else "Off"
            lines.append(f"**Event deadline reminders** {reminder}")
        return "\n".join(lines)

    def update_defaults(self):
        for option in self.timing.options:
            option.default = option.value == ("hourly" if self.delivery_minute is None else str(self.delivery_minute))
        for option in self.event_window.options:
            option.default = option.value == (str(self.lead_hours) if self.lead_hours else "off")

    async def show_settings(self, interaction):
        self.update_defaults()
        await interaction.response.edit_message(content=self.prompt, view=self)

    @discord.ui.select(placeholder="Choose a delivery time (bot host's local time)")
    async def timing(self, interaction, select):
        value = select.values[0]
        self.delivery_minute = None if value == "hourly" else int(value)
        await self.show_settings(interaction)

    @discord.ui.select(placeholder="Remind me when events are ending")
    async def event_window(self, interaction, select):
        value = select.values[0]
        self.lead_hours = None if value == "off" else int(value)
        await self.show_settings(interaction)

    @discord.ui.button(label="Enter custom time or window")
    async def custom(self, interaction, button):
        await interaction.response.send_modal(CustomAlertSettings(self))

    @discord.ui.button(label="Save schedule", style=discord.ButtonStyle.primary)
    async def save(self, interaction, button):
        user_id = interaction.user.id
        if self.topic is None:
            if self.lead_hours != self.saved_lead_hours:
                if self.lead_hours:
                    try:
                        alert_channel(self.alerts, interaction)
                    except ValueError as error:
                        await interaction.response.send_message(str(error), ephemeral=True)
                        return
                self.alerts.set_event_reminders(user_id, self.lead_hours is not None, lead_hours=self.lead_hours)
            self.alerts.set_schedule(user_id, self.delivery_minute)
            reminders = f"within {self.lead_hours} h of an event ending" if self.lead_hours else "off"
            message = f"Saved. Alerts go out {schedule_text(self.delivery_minute)}. Event deadline reminders are {reminders}."
        else:
            try:
                channel = alert_channel(self.alerts, interaction)
            except ValueError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
            delivery_minute = self.alerts.subscribe(self.topic, user_id, channel.id, self.delivery_minute)
            message = subscription_confirmation(channel, delivery_minute)
        await interaction.response.edit_message(
            content=message,
            view=None,
        )
        self.stop()


class PostingDestination(discord.ui.View):
    def __init__(self, alerts):
        super().__init__(timeout=180)
        self.alerts = alerts
        self.disable.disabled = alerts.posting_setting() is None

    async def interaction_check(self, interaction):
        if interaction.permissions.manage_guild:
            return True
        await interaction.response.send_message("You need Manage Server to configure UmaBot posts.", ephemeral=True)
        return False

    @staticmethod
    def can_send_embed(channel, member):
        permissions = channel.permissions_for(member)
        return permissions.view_channel and permissions.send_messages and permissions.embed_links

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Choose a text channel",
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
    )
    async def destination(self, interaction, select):
        channel = interaction.guild.get_channel(select.values[0].id)
        if channel is None or not channel.permissions_for(interaction.user).view_channel:
            await interaction.response.send_message("Choose a channel you can access.", ephemeral=True)
            return
        if not self.can_send_embed(channel, interaction.guild.me):
            await interaction.response.send_message(
                "Choose a channel where UmaBot can view the channel, send messages, and embed links.",
                ephemeral=True,
            )
            return
        self.alerts.set_posting_channel(channel.id)
        self.disable.disabled = False
        await interaction.response.edit_message(
            content=f"I’ll post updates and subscription pings in {channel.mention}. I check EN news every 15 minutes and card and trainee releases hourly.",
            view=self,
        )

    @discord.ui.button(label="Send test post", style=discord.ButtonStyle.primary)
    async def test_post(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        setting = self.alerts.posting_setting()
        if setting is None:
            await interaction.edit_original_response(content="Select and save a posting channel first.")
            return

        channel = interaction.guild.get_channel(setting[0])
        missing_channel = "The saved channel is no longer available. Select and save another posting channel."
        permission_error = "Check that UmaBot has View Channel, Send Messages, and Embed Links permissions in the saved channel."
        if channel is None:
            await interaction.edit_original_response(content=missing_channel)
            return
        if not self.can_send_embed(channel, interaction.guild.me):
            await interaction.edit_original_response(content=permission_error)
            return

        embed = discord.Embed(
            title="UmaBot test alert",
            description="This post was sent manually to verify alert delivery.",
        )
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.NotFound:
            await interaction.edit_original_response(content=missing_channel)
        except discord.HTTPException:
            await interaction.edit_original_response(content=f"Couldn't send the test post. {permission_error}")
        else:
            await interaction.edit_original_response(content=f"UmaBot successfully posted an embed in {channel.mention}.")

    @discord.ui.button(label="Turn off UmaBot posts")
    async def disable(self, interaction, button):
        self.alerts.set_posting_channel(None)
        await interaction.response.edit_message(content="Automatic posts and pings are off. Subscriptions are saved.", view=None)
        self.stop()
