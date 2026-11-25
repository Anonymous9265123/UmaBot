"""Store alert preferences, check releases, and deliver Discord notifications."""

import asyncio
import hashlib
import logging
import re
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from time import monotonic, time

import discord
from discord.ext import tasks

from .embeds import news_embeds, weekly_digest_embed
from .game.api import build_url, en_release_date, en_release_status, support_name
from .game.feeds import FEED_TTL


log = logging.getLogger(__name__)
EVENT_TITLE = re.compile(
    r"\b(?:event|champions meeting|league of heroes|legend races|trainers?' cup)\b"
    r".*\b(?:now available|now live|is here|has begun|will begin|begins|starts|coming soon|announcement)\b"
    r"|^\s*(?:new|upcoming)\s+(?:story|racing|training)\s+event\b",
    re.IGNORECASE,
)


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
        "as soon as an hourly check detects an update"
        if delivery_minute is None else f"daily at {clock} (bot host's local time)"
    )


def scheduled_check_time(now, delivery_minute):
    if delivery_minute is None:
        return now
    hour, minute = divmod(delivery_minute, 60)
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
    if scheduled > now:
        scheduled -= timedelta(days=1)
    timestamp = scheduled.timestamp()
    # A clock change can skip a scheduled time or repeat it an hour later.
    if datetime.fromtimestamp(timestamp) != scheduled:
        return None
    return scheduled if 0 <= now.timestamp() - timestamp < 300 else None


def subscription_confirmation(topic, channel, delivery_minute):
    unsubscribe = "Click the alert button again to unsubscribe."
    if topic != "events":
        unsubscribe = "Use `/uma subscriptions` or click the alert button again to unsubscribe."
    return (
        f"Subscribed. I’ll ping you in {channel.mention} {schedule_text(delivery_minute)}. "
        f"Use `/uma alert` to change your alert schedule. {unsubscribe}"
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
            message = subscription_confirmation(self.topic, channel, delivery_minute)
        if trainee:
            await interaction.edit_original_response(content=message, view=view)
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
            message = subscription_confirmation(self.topic, channel, delivery_minute)
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
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            await interaction.response.send_message(
                "Choose a channel where UmaBot can view the channel, send messages, and embed links.",
                ephemeral=True,
            )
            return
        self.alerts.set_posting_channel(channel.id)
        await interaction.response.edit_message(
            content=f"I’ll post EN news, new characters and support cards, and subscription pings in {channel.mention}. I check hourly.",
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Turn off UmaBot posts")
    async def disable(self, interaction, button):
        self.alerts.set_posting_channel(None)
        await interaction.response.edit_message(content="Automatic posts and pings are off. Subscriptions are saved.", view=None)
        self.stop()


class UmaAlerts:
    def __init__(self, client, guild_id, path, feeds):
        self.client = client
        self.guild_id = guild_id
        self.path = path
        self.feeds = feeds
        self.released_cards = {}
        self.released_trainees = {}
        self.refresh_at = 0

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self):
        with self.database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    topic TEXT, user_id INTEGER, channel_id INTEGER, subscribed_at REAL,
                    PRIMARY KEY (topic, user_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS alert_schedules (
                    user_id INTEGER PRIMARY KEY, delivery_minute INTEGER
                );
                CREATE TABLE IF NOT EXISTS reminder_preferences (
                    kind TEXT, user_id INTEGER, lead_hours INTEGER NOT NULL DEFAULT 24,
                    PRIMARY KEY (kind, user_id)
                );
                CREATE TABLE IF NOT EXISTS reminder_deliveries (
                    kind TEXT, occurrence TEXT, user_id INTEGER,
                    PRIMARY KEY (kind, occurrence, user_id)
                );
                CREATE TABLE IF NOT EXISTS event_deliveries (
                    event_key TEXT, user_id INTEGER, channel_id INTEGER, delivered_at REAL,
                    PRIMARY KEY (event_key, user_id, channel_id)
                );
                CREATE TABLE IF NOT EXISTS event_announcements (
                    event_key TEXT PRIMARY KEY, title TEXT, posted_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS news_settings (
                    guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, enabled_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_deliveries (
                    guild_id INTEGER, news_key TEXT, PRIMARY KEY (guild_id, news_key)
                );
                CREATE TABLE IF NOT EXISTS weekly_digest (
                    guild_id INTEGER PRIMARY KEY, channel_id INTEGER, weekday INTEGER NOT NULL,
                    delivery_minute INTEGER NOT NULL, last_sent_week TEXT
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(subscriptions)")}
            if "delivery_hour" not in columns:
                db.execute("ALTER TABLE subscriptions ADD COLUMN delivery_hour INTEGER")
            schedule_columns = {row[1] for row in db.execute("PRAGMA table_info(alert_schedules)")}
            if "delivery_minute" not in schedule_columns:
                db.execute("ALTER TABLE alert_schedules ADD COLUMN delivery_minute INTEGER")
                db.execute("UPDATE alert_schedules SET delivery_minute = delivery_hour * 60")
            reminder_columns = {row[1] for row in db.execute("PRAGMA table_info(reminder_preferences)")}
            if "lead_hours" not in reminder_columns:
                db.execute("ALTER TABLE reminder_preferences ADD COLUMN lead_hours INTEGER NOT NULL DEFAULT 24")
            db.execute("""
                INSERT OR IGNORE INTO alert_schedules (user_id, delivery_minute)
                SELECT user_id, delivery_hour * 60 FROM subscriptions
                ORDER BY subscribed_at DESC, rowid DESC
            """)
        self.poll.start()

    @staticmethod
    def view(topic):
        view = discord.ui.View(timeout=None)
        view.add_item(AlertButton(str(topic)))
        return view

    def is_subscribed(self, topic, user_id):
        with self.database() as db:
            return db.execute(
                "SELECT 1 FROM subscriptions WHERE topic = ? AND user_id = ? LIMIT 1",
                (topic, user_id),
            ).fetchone() is not None

    def subscription_ids(self, user_id, *, characters):
        prefix = "trainee:" if characters else ""
        with self.database() as db:
            topics = db.execute(
                "SELECT DISTINCT topic FROM subscriptions WHERE user_id = ? AND topic GLOB ?",
                (user_id, f"{prefix}[0-9]*"),
            ).fetchall()
        ids = (topic.removeprefix(prefix) for (topic,) in topics)
        return [int(value) for value in ids if value.isascii() and value.isdecimal()]

    def schedule(self, user_id):
        with self.database() as db:
            return db.execute(
                "SELECT delivery_minute FROM alert_schedules WHERE user_id = ?", (user_id,)
            ).fetchone()

    def set_schedule(self, user_id, delivery_minute):
        with self.database() as db:
            db.execute("""
                INSERT INTO alert_schedules (user_id, delivery_minute) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET delivery_minute = excluded.delivery_minute
            """, (user_id, delivery_minute))

    def subscribe(self, topic, user_id, channel_id, delivery_minute):
        with self.database() as db:
            db.execute(
                "INSERT OR IGNORE INTO alert_schedules (user_id, delivery_minute) VALUES (?, ?)",
                (user_id, delivery_minute),
            )
            db.execute("""
                INSERT INTO subscriptions (topic, user_id, channel_id, subscribed_at)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM subscriptions WHERE topic = ? AND user_id = ?)
            """, (topic, user_id, channel_id, time(), topic, user_id))
            return db.execute(
                "SELECT delivery_minute FROM alert_schedules WHERE user_id = ?", (user_id,)
            ).fetchone()[0]

    def remove_preference(self, topic, user_id):
        with self.database() as db:
            db.execute("DELETE FROM subscriptions WHERE topic = ? AND user_id = ?", (topic, user_id))

    def posting_setting(self):
        with self.database() as db:
            return db.execute(
                "SELECT channel_id, enabled_at FROM news_settings WHERE guild_id = ?", (self.guild_id,)
            ).fetchone()

    def set_posting_channel(self, channel_id):
        with self.database() as db:
            if channel_id is None:
                db.execute("DELETE FROM news_settings WHERE guild_id = ?", (self.guild_id,))
            else:
                db.execute(
                    "INSERT INTO news_settings VALUES (?, ?, ?) "
                    "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
                    (self.guild_id, channel_id, time()),
                )

    def event_reminder_hours(self, user_id):
        with self.database() as db:
            row = db.execute(
                "SELECT lead_hours FROM reminder_preferences WHERE kind = 'events' AND user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def set_event_reminders(self, user_id, enabled, *, lead_hours=None):
        with self.database() as db:
            if enabled:
                db.execute("""
                    INSERT INTO reminder_preferences (kind, user_id, lead_hours)
                    VALUES ('events', ?, COALESCE(?, 24))
                    ON CONFLICT(kind, user_id) DO UPDATE
                    SET lead_hours = COALESCE(?, reminder_preferences.lead_hours)
                """, (user_id, lead_hours, lead_hours))
            else:
                db.execute("DELETE FROM reminder_preferences WHERE kind = 'events' AND user_id = ?", (user_id,))

    async def check_event_reminders(self, preferences, now, setting):
        due = []
        for user_id, lead_hours, delivery_minute in preferences:
            if scheduled := scheduled_check_time(now, delivery_minute):
                due.append((user_id, lead_hours * 3600, scheduled.timestamp()))
        if not due:
            return
        timestamp = now.timestamp()
        for event in await self.feeds.events():
            if event.end <= timestamp:
                continue
            recipients = {
                user_id for user_id, window, scheduled in due
                if event.start <= scheduled and 0 < event.end - scheduled <= window
            }
            if recipients:
                await self.send_event_reminder(event, setting, recipients)

    async def send_event_reminder(self, event, setting, recipients):
        with self.database() as db:
            users = [user_id for (user_id,) in db.execute("""
                SELECT user_id FROM reminder_preferences WHERE kind = 'events'
                EXCEPT SELECT user_id FROM reminder_deliveries WHERE kind = 'events' AND occurrence = ?
                ORDER BY user_id
            """, (event.key,)) if user_id in recipients]
        title = discord.utils.escape_markdown(event.title)[:200]
        text = f"{event.category} · **{title}** ends <t:{event.end}:R>. Use `/uma events` to view the schedule."
        channel = self.client.get_partial_messageable(setting[0])
        for offset in range(0, len(users), 40):
            if self.posting_setting() != setting:
                return
            batch = users[offset:offset + 40]
            mentions = " ".join(f"<@{user_id}>" for user_id in batch)
            try:
                await channel.send(
                    f"{mentions}\n{text}",
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(id=user_id) for user_id in batch],
                        roles=False, everyone=False, replied_user=False,
                    ),
                )
            except discord.HTTPException:
                log.warning("Could not post event reminder", exc_info=True)
                return
            with self.database() as db:
                db.executemany("INSERT OR IGNORE INTO reminder_deliveries VALUES ('events', ?, ?)", [
                    (event.key, user_id) for user_id in batch
                ])

    def digest_setting(self):
        with self.database() as db:
            return db.execute(
                "SELECT channel_id, weekday, delivery_minute FROM weekly_digest WHERE guild_id = ? AND channel_id IS NOT NULL",
                (self.guild_id,),
            ).fetchone()

    def set_digest(self, channel_id, weekday=0, delivery_minute=540):
        with self.database() as db:
            db.execute("""
                INSERT INTO weekly_digest (guild_id, channel_id, weekday, delivery_minute)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id, weekday = excluded.weekday,
                    delivery_minute = excluded.delivery_minute
            """, (self.guild_id, channel_id, weekday, delivery_minute))

    async def check_digest(self, now):
        """Deliver once per local week and retry failed posts on the scheduled day."""
        setting = self.digest_setting()
        if setting is None:
            return
        channel_id, weekday, minute = setting
        if now.weekday() != weekday or now.hour * 60 + now.minute < minute:
            return
        week = (now.date() - timedelta(days=now.weekday())).isoformat()
        with self.database() as db:
            if db.execute(
                "SELECT 1 FROM weekly_digest WHERE guild_id = ? AND last_sent_week = ?", (self.guild_id, week),
            ).fetchone():
                return
        timestamp = int(now.timestamp())
        events = await self.feeds.events()
        forecasts = await self.feeds.projections(events, timestamp)
        if self.digest_setting() != setting:
            return
        nonce = hashlib.blake2s(f"digest:{self.guild_id}:{week}".encode(), digest_size=10).hexdigest()
        try:
            await self.client.get_partial_messageable(channel_id).send(
                embed=weekly_digest_embed(events, forecasts, timestamp),
                allowed_mentions=discord.AllowedMentions.none(),
                nonce=nonce,
            )
        except discord.HTTPException:
            log.warning("Could not post the weekly digest in channel %s", channel_id, exc_info=True)
            return
        with self.database() as db:
            db.execute(
                "UPDATE weekly_digest SET last_sent_week = ? WHERE guild_id = ?", (week, self.guild_id),
            )

    @tasks.loop(minutes=1)
    async def poll(self):
        await self.check_digest(datetime.now())
        setting = self.posting_setting()
        if setting is None:
            return
        with self.database() as db:
            subscriptions = db.execute("""
                SELECT s.topic, s.user_id, MAX(s.subscribed_at), a.delivery_minute
                FROM subscriptions AS s
                LEFT JOIN alert_schedules AS a ON a.user_id = s.user_id
                GROUP BY s.topic, s.user_id
            """).fetchall()
            reminder_preferences = db.execute("""
                SELECT r.user_id, r.lead_hours, a.delivery_minute
                FROM reminder_preferences AS r
                LEFT JOIN alert_schedules AS a ON a.user_id = r.user_id
                WHERE r.kind = 'events'
            """).fetchall()

        refresh = monotonic() >= self.refresh_at
        if refresh:
            await asyncio.gather(
                self.check_cards(setting),
                self.check_announcements(setting),
                self.check_trainees(setting),
            )
            self.refresh_at = monotonic() + FEED_TTL

        now = datetime.now()
        cards, events, trainees = [], [], []
        for topic, user_id, subscribed, delivery_minute in subscriptions:
            due = refresh if delivery_minute is None else scheduled_check_time(now, delivery_minute) is not None
            if not due:
                continue
            row = (topic, user_id, subscribed)
            if topic == "events":
                events.append(row)
            else:
                (trainees if topic.startswith("trainee:") else cards).append(row)
        await asyncio.gather(
            self.notify_releases(cards, self.released_cards),
            self.notify_releases(trainees, self.released_trainees),
            self.check_events(events),
            self.check_event_reminders(reminder_preferences, now, setting),
        )

    @poll.before_loop
    async def wait_for_connection(self):
        await self.client.wait_until_ready()

    async def check_cards(self, setting):
        catalog = await self.feeds.catalog("gametora")
        released = {}
        announcements = []
        for card in catalog:
            date = en_release_date(card)
            if date is None or date.timestamp() > time():
                continue
            topic = str(card["id"])
            name = support_name(card) or "Support card"
            link = build_url(f"support/{topic}/gametora")
            message = f"**{discord.utils.escape_markdown(name)[:200]}** is available on EN. <{link}>"
            released[topic] = message
            announcements.append((date.timestamp(), f"support:{topic}", {"content": f"New EN support card\n{message}"}))
        self.released_cards = released
        await self.post_updates(announcements, setting)

    async def trainee_status(self, topic):
        catalog = await self.trainee_releases()
        return catalog.get(topic, ("Unknown", None, None))[0]

    async def trainee_releases(self):
        catalog = await self.feeds.catalog("character-cards")
        trainees = {}
        priority = {"Unreleased": 0, "Unknown": 1, "Released": 2}
        for card in catalog:
            if not isinstance(card.get("char_id"), int):
                continue
            topic = f"trainee:{card['char_id']}"
            status = en_release_status(card)
            date = en_release_date(card)
            previous = trainees.get(topic)
            if (
                previous is None
                or priority[status] > priority[previous[0]]
                or (status == previous[0] and date is not None and (previous[2] is None or date < previous[2]))
            ):
                name = card.get("name_en") or card.get("name_jp") or "Your trainee"
                trainees[topic] = (status, name, date)
        return trainees

    async def check_trainees(self, setting):
        catalog = await self.trainee_releases()
        self.released_trainees = {
            topic: f"**{discord.utils.escape_markdown(name)[:200]}** is now playable on EN."
            for topic, (status, name, _) in catalog.items()
            if status == "Released"
        }
        await self.post_updates([
            (catalog[topic][2].timestamp(), topic, {"content": f"New EN character\n{message}"})
            for topic, message in self.released_trainees.items()
        ], setting)

    async def notify_releases(self, subscriptions, messages):
        by_topic = defaultdict(list)
        for topic, user_id, subscribed in subscriptions:
            if topic in messages:
                by_topic[topic].append((user_id, subscribed))

        for topic, recipients in by_topic.items():
            await self.deliver(recipients, messages[topic], topic=topic)

    async def check_announcements(self, setting):
        posts = [
            post for post in await self.feeds.catalog("en/news/latest/20")
            if isinstance(post.get("title"), str) and isinstance(post.get("post_at"), int)
        ]
        self.store_event_announcements(posts)
        await self.post_news(posts, setting)

    async def post_news(self, posts, setting):
        updates = []
        for post, embed in zip(posts, news_embeds(posts)):
            posted, title = post["post_at"], post["title"]
            message = {"embed": embed}
            if EVENT_TITLE.search(title):
                message["view"] = self.view("events")
            updates.append((posted, str(post.get("id") or f"{posted}:{title}"), message))
        await self.post_updates(updates, setting)

    async def post_updates(self, updates, setting):
        channel_id, enabled_at = setting
        with self.database() as db:
            delivered = {
                key for (key,) in db.execute("SELECT news_key FROM news_deliveries WHERE guild_id = ?", (self.guild_id,))
            }
        channel = self.client.get_partial_messageable(channel_id)
        now = time()
        for posted, key, message in sorted(updates, key=lambda update: update[0]):
            if not enabled_at < posted <= now:
                continue
            if key in delivered:
                continue
            if self.posting_setting() != setting:
                return
            try:
                await channel.send(**message)
            except discord.HTTPException:
                log.warning("Could not post an UmaBot update to channel %s", channel_id, exc_info=True)
                return
            delivered.add(key)
            with self.database() as db:
                db.execute("INSERT OR IGNORE INTO news_deliveries VALUES (?, ?)", (self.guild_id, key))

    def store_event_announcements(self, posts):
        announcements = []
        for post in posts:
            title, posted = post["title"], post["post_at"]
            if not EVENT_TITLE.search(title):
                continue
            identity = post.get("id") or f"{posted}:{title}"
            event_key = hashlib.sha256(str(identity).encode()).hexdigest()
            announcements.append((event_key, title, posted))

        with self.database() as db:
            db.executemany("INSERT OR IGNORE INTO event_announcements VALUES (?, ?, ?)", announcements)

    async def check_events(self, due):
        if not due:
            return
        with self.database() as db:
            pending = db.execute(
                "SELECT event_key, title, posted_at FROM event_announcements "
                "WHERE posted_at > ? AND posted_at <= ? ORDER BY posted_at",
                (min(row[2] for row in due), time()),
            ).fetchall()

        for event_key, title, posted in pending:
            with self.database() as db:
                delivered = {user_id for (user_id,) in db.execute(
                    "SELECT user_id FROM event_deliveries WHERE event_key = ?", (event_key,)
                )}
            recipients = [
                (user_id, subscribed) for _, user_id, subscribed in due
                if posted > subscribed and user_id not in delivered
            ]
            message = f"New EN event announcement\n**{discord.utils.escape_markdown(title)[:200]}**"
            await self.deliver(recipients, message, event_key=event_key)

    async def deliver(self, recipients, message, *, topic=None, event_key=None):
        for offset in range(0, len(recipients), 40):
            setting = self.posting_setting()
            if setting is None:
                return
            channel_id = setting[0]
            channel = self.client.get_partial_messageable(channel_id)
            with self.database() as db:
                current = set(db.execute(
                    "SELECT user_id, MAX(subscribed_at) FROM subscriptions WHERE topic = ? GROUP BY user_id",
                    (topic if topic is not None else "events",),
                ))
            batch = [row for row in recipients[offset:offset + 40] if row in current]
            if not batch:
                continue
            users = [user_id for user_id, _ in batch]
            mentions = " ".join(f"<@{user_id}>" for user_id in users)
            try:
                await channel.send(
                    f"{mentions}\n{message}",
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(id=user_id) for user_id in users],
                        roles=False, everyone=False, replied_user=False,
                    ),
                )
            except discord.HTTPException:
                log.warning("Could not send UmaBot alert to channel %s", channel_id, exc_info=True)
                return
            with self.database() as db:
                if topic is not None:
                    db.executemany(
                        "DELETE FROM subscriptions WHERE topic = ? AND user_id = ? AND subscribed_at <= ?",
                        [(topic, user_id, subscribed) for user_id, subscribed in batch],
                    )
                else:
                    db.executemany(
                        "INSERT OR IGNORE INTO event_deliveries VALUES (?, ?, ?, ?)",
                        [(event_key, user_id, channel_id, time()) for user_id in users],
                    )
