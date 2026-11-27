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

from .alert_settings import AlertButton
from .embeds import news_embed, weekly_digest_embed
from .game.feeds import FEED_TTL, NEWS_ENDPOINT, NEWS_TTL


log = logging.getLogger(__name__)
MENTION_BATCH_SIZE = 40
EVENT_TITLE = re.compile(
    r"\b(?:event|champions meeting|league of heroes|legend races|trainers?' cup)\b"
    r".*\b(?:now available|now live|is here|has begun|will begin|begins|starts|coming soon|announcement)\b"
    r"|^\s*(?:new|upcoming)\s+(?:story|racing|training)\s+event\b",
    re.IGNORECASE,
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


async def send_user_ping(channel, users, message):
    mentions = " ".join(f"<@{user_id}>" for user_id in users)
    await channel.send(
        f"{mentions}\n{message}",
        allowed_mentions=discord.AllowedMentions(
            users=[discord.Object(id=user_id) for user_id in users],
            roles=False, everyone=False, replied_user=False,
        ),
    )


class UmaAlerts:
    """Store one server's alert preferences and deliver its automated posts."""

    def __init__(self, client, guild_id, path, feeds):
        self.client = client
        self.guild_id = guild_id
        self.path = path
        self.feeds = feeds
        self.released_cards = {}
        self.released_trainees = {}
        self.refresh_at = 0
        self.news_refresh_at = 0

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
        if channel_id is None:
            log.info("Automatic news and subscription pings disabled in server %s", self.guild_id)
        else:
            log.info("Automatic posting configured in server %s for channel %s", self.guild_id, channel_id)

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
        """Find active events that end within each recipient's reminder window."""
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
        for offset in range(0, len(users), MENTION_BATCH_SIZE):
            if self.posting_setting() != setting:
                return
            batch = users[offset:offset + MENTION_BATCH_SIZE]
            try:
                await send_user_ping(channel, batch, text)
            except discord.HTTPException:
                log.warning(
                    "Could not send an event-ending reminder in server %s to channel %s",
                    self.guild_id, setting[0], exc_info=True,
                )
                return
            with self.database() as db:
                db.executemany("INSERT OR IGNORE INTO reminder_deliveries VALUES ('events', ?, ?)", [
                    (event.key, user_id) for user_id in batch
                ])
            log.info(
                "Sent an event-ending reminder in server %s to channel %s (%d recipients)",
                self.guild_id, setting[0], len(batch),
            )

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
            log.warning(
                "Could not post the weekly digest in server %s to channel %s",
                self.guild_id, channel_id, exc_info=True,
            )
            return
        with self.database() as db:
            db.execute(
                "UPDATE weekly_digest SET last_sent_week = ? WHERE guild_id = ?", (week, self.guild_id),
            )
        log.info("Posted the weekly digest in server %s to channel %s", self.guild_id, channel_id)

    @tasks.loop(minutes=1)
    async def poll(self):
        """Check delivery eligibility every minute and refresh releases periodically."""
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
        refresh_news = monotonic() >= self.news_refresh_at
        checks = []
        if refresh:
            checks.extend((self.check_cards(setting), self.check_trainees(setting)))
        if refresh_news:
            checks.append(self.check_announcements(setting))
        if checks:
            await asyncio.gather(*checks)
        if refresh:
            self.refresh_at = monotonic() + FEED_TTL
        if refresh_news:
            self.news_refresh_at = monotonic() + NEWS_TTL

        now = datetime.now()
        cards, events, trainees = [], [], []
        for topic, user_id, subscribed, delivery_minute in subscriptions:
            if delivery_minute is None:
                due = refresh_news if topic == "events" else refresh
            else:
                due = scheduled_check_time(now, delivery_minute) is not None
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

    @poll.error
    async def log_poll_failure(self, error):
        log.error("Alert polling stopped in server %s", self.guild_id, exc_info=error)

    async def check_cards(self, setting):
        self.released_cards, announcements = await self.feeds.support_releases()
        await self.post_updates(announcements, setting)

    async def trainee_status(self, topic):
        statuses, _, _ = await self.feeds.trainee_releases()
        return statuses.get(topic, "Unknown")

    async def check_trainees(self, setting):
        _, self.released_trainees, announcements = await self.feeds.trainee_releases()
        await self.post_updates(announcements, setting)

    async def notify_releases(self, subscriptions, messages):
        by_topic = defaultdict(list)
        for topic, user_id, subscribed in subscriptions:
            if topic in messages:
                by_topic[topic].append((user_id, subscribed))

        for topic, recipients in by_topic.items():
            await self.deliver(recipients, messages[topic], topic=topic)

    async def check_announcements(self, setting):
        announcements = []
        prepared = []
        for post in await self.feeds.catalog(NEWS_ENDPOINT):
            title, posted = post.get("title"), post.get("post_at")
            if not isinstance(title, str) or not isinstance(posted, int):
                continue
            key = str(post.get("id") or f"{posted}:{title}")
            is_event = EVENT_TITLE.search(title) is not None
            if is_event:
                event_key = hashlib.sha256(key.encode()).hexdigest()
                announcements.append((event_key, title, posted))
            prepared.append((posted, key, post, is_event))

        # Record events independently of news delivery eligibility.
        with self.database() as db:
            db.executemany("INSERT OR IGNORE INTO event_announcements VALUES (?, ?, ?)", announcements)

        updates = []
        for posted, key, post, is_event in prepared:
            message = {"embed": news_embed(post)}
            if is_event:
                message["view"] = self.view("events")
            updates.append((posted, key, message))
        await self.post_updates(updates, setting)

    async def post_updates(self, updates, setting):
        channel_id, enabled_at = setting
        now = time()
        candidates = sorted(
            (entry for entry in updates if enabled_at < entry[0] <= now), key=lambda entry: entry[0],
        )
        if not candidates:
            return
        keys = list(dict.fromkeys(key for _, key, _ in candidates))
        delivered = set()
        with self.database() as db:
            for offset in range(0, len(keys), 900):
                batch = keys[offset:offset + 900]
                placeholders = ", ".join("?" for _ in batch)
                delivered.update(key for (key,) in db.execute(
                    f"SELECT news_key FROM news_deliveries WHERE guild_id = ? AND news_key IN ({placeholders})",
                    (self.guild_id, *batch),
                ))
        channel = self.client.get_partial_messageable(channel_id)
        for _, key, message in candidates:
            if key in delivered:
                continue
            if self.posting_setting() != setting:
                log.info("Skipped an update in server %s because its posting channel changed", self.guild_id)
                return
            try:
                await channel.send(**message)
            except discord.HTTPException:
                log.warning(
                    "Could not post an UmaBot update in server %s to channel %s",
                    self.guild_id, channel_id, exc_info=True,
                )
                return
            delivered.add(key)
            with self.database() as db:
                db.execute("INSERT OR IGNORE INTO news_deliveries VALUES (?, ?)", (self.guild_id, key))
            log.info("Posted an UmaBot update in server %s to channel %s", self.guild_id, channel_id)

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
        """Send release or event pings and record each successful batch."""
        alert_kind = "event announcement" if topic is None else "release"
        for offset in range(0, len(recipients), MENTION_BATCH_SIZE):
            setting = self.posting_setting()
            if setting is None:
                log.info("Skipped alert (%s) in server %s because posting is disabled", alert_kind, self.guild_id)
                return
            channel_id = setting[0]
            channel = self.client.get_partial_messageable(channel_id)
            # A user may have unsubscribed while the alert was being prepared.
            with self.database() as db:
                current = set(db.execute(
                    "SELECT user_id, MAX(subscribed_at) FROM subscriptions WHERE topic = ? GROUP BY user_id",
                    (topic if topic is not None else "events",),
                ))
            batch = [row for row in recipients[offset:offset + MENTION_BATCH_SIZE] if row in current]
            if not batch:
                continue
            users = [user_id for user_id, _ in batch]
            try:
                await send_user_ping(channel, users, message)
            except discord.HTTPException:
                log.warning(
                    "Could not send alert (%s) in server %s to channel %s",
                    alert_kind, self.guild_id, channel_id, exc_info=True,
                )
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
            log.info(
                "Sent alert (%s) in server %s to channel %s (%d recipients)",
                alert_kind, self.guild_id, channel_id, len(users),
            )
