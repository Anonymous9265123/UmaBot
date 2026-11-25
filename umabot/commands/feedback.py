"""Keep a private, bot-owner-only feedback inbox."""

import sqlite3
from contextlib import closing
from time import time

import discord
from discord import app_commands


PAGE_SIZE = 5


class FeedbackInbox:
    def __init__(self, path):
        self.path = path

    def initialize(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    submitted_at INTEGER NOT NULL,
                    message TEXT NOT NULL
                )
            """)

    def submit(self, guild_id, user_id, message):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO feedback (guild_id, user_id, submitted_at, message) VALUES (?, ?, ?, ?)",
                (guild_id, user_id, int(time()), message),
            )

    def page(self, number):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("""
                SELECT id, guild_id, user_id, submitted_at, message
                FROM feedback ORDER BY id DESC LIMIT ? OFFSET ?
            """, (PAGE_SIZE + 1, (number - 1) * PAGE_SIZE)).fetchall()


def register_feedback(group, inbox):
    @group.command(name="feedback", description="Send private feedback to the bot owner or open your owner inbox")
    @app_commands.describe(message="Feedback for the bot owner", page="Inbox page for the bot owner")
    async def feedback(
        interaction: discord.Interaction,
        message: app_commands.Range[str, 1, 500] | None = None,
        page: app_commands.Range[int, 1, 100_000] = 1,
    ):
        if message is not None:
            message = message.strip()
            if not message:
                await interaction.response.send_message("Enter a feedback message first.", ephemeral=True)
                return
            inbox.submit(interaction.guild_id, interaction.user.id, message)
            await interaction.response.send_message("Feedback sent privately to the bot owner.", ephemeral=True)
            return

        application = interaction.client.application
        owner_id = None
        if application is not None:
            owner_id = application.team.owner_id if application.team else application.owner.id
        if interaction.user.id != owner_id:
            await interaction.response.send_message(
                "To send feedback privately, enter your message in `/uma feedback`.",
                ephemeral=True,
            )
            return

        rows = inbox.page(page)
        if not rows:
            await interaction.response.send_message("There’s no feedback on this page.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Private feedback inbox · Page {page}", color=0x58A65C)
        for feedback_id, guild_id, user_id, submitted_at, text in rows[:PAGE_SIZE]:
            message_text = discord.utils.escape_mentions(discord.utils.escape_markdown(text))
            embed.add_field(
                name=f"#{feedback_id} · <t:{submitted_at}:f>",
                value=f"{message_text}\n\nUser `{user_id}` · Server `{guild_id}`",
                inline=False,
            )
        if len(rows) > PAGE_SIZE:
            embed.set_footer(text=f"Run /uma feedback with page {page + 1} for older messages.")
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
