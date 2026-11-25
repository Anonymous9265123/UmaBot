"""Register EN calendar and event reminder commands."""

import asyncio
from time import time
from urllib.parse import quote

import discord
from discord import app_commands

from ..alerts import alert_channel
from ..game.calendar import CM_CATALOG, CM_FORECAST_CATALOG, CM_REFERENCE_CATALOG, CM_URL


def register_community(group, alerts, get_catalog, get_events):
    async def next_meeting_preview(meetings, now):
        forecasts, reference = await asyncio.gather(
            get_catalog(CM_FORECAST_CATALOG), get_catalog(CM_REFERENCE_CATALOG),
        )
        last_known = max((row["id"] for row in meetings), default=0)
        courses = {
            row["id"]: row for row in reference
            if isinstance(row.get("id"), int) and row["id"] > last_known
        }
        forecast = min(
            (
                row for row in forecasts if isinstance(row.get("id"), int) and row["id"] > last_known
                and isinstance(row.get("display_start"), int) and row["display_start"] >= now
            ),
            key=lambda row: row["display_start"],
            default=None,
        )
        if forecast:
            return {**courses.get(forecast["id"], {}), **forecast}
        if last_known and courses:
            return courses[min(courses)]
        return None

    @group.command(name="cm", description="View the current, upcoming, or a past EN Champions Meeting")
    @app_commands.describe(name="Optional cup name or meeting number")
    async def champions_meeting(interaction: discord.Interaction, name: app_commands.Range[str, 1, 100] | None = None):
        await interaction.response.defer(thinking=True)
        rows, tracks = await asyncio.gather(get_catalog(CM_CATALOG), get_catalog("racetracks_extended"))
        meetings = sorted(
            (row for row in rows if all(isinstance(row.get(key), int) for key in ("start", "end", "id"))),
            key=lambda row: row["start"],
        )
        now = int(time())
        query = (name or "").strip().casefold()

        def matches(row):
            title = row.get("name_en") or row.get("name") or ""
            return query == str(row["id"]) if query.isdecimal() else query in title.casefold()

        if query:
            meeting = next((row for row in reversed(meetings) if matches(row)), None)
        else:
            meeting = next((row for row in reversed(meetings) if row["start"] <= now < row["end"]), None)
            if meeting is None:
                meeting = next((row for row in meetings if row["start"] > now), None)

        preview = meeting is None
        if preview:
            candidate = await next_meeting_preview(meetings, now)
            if candidate and (not query or matches(candidate)):
                meeting = candidate
        if meeting is None:
            message = "No matching Champions Meeting found." if query else "GameTora has no upcoming EN Champions Meeting information available."
            await interaction.edit_original_response(content=message)
            return

        if preview:
            description = "Next EN Champions Meeting preview"
            if estimated_start := meeting.get("display_start"):
                description += f" · Estimated start <t:{estimated_start}:D>"
            description += "\nEN dates and course are subject to confirmation."
        else:
            start, end = meeting["start"], meeting["end"]
            status = "Upcoming" if start > now else "Live now" if end > now else "Ended"
            description = f"{status} · <t:{start}:f> to <t:{end}:f>"
        embed = discord.Embed(
            title=meeting.get("name_en") or meeting.get("name") or f"Champions Meeting #{meeting['id']}",
            url=f"{CM_URL}?cm={meeting['id']}",
            description=description,
            color=0x58A65C,
        )
        view = None
        if race := meeting.get("race"):
            track = next((row.get("name_en") for row in tracks if row.get("id") == race.get("track")), None)
            distance = race.get("distance")
            surface = {1: "Turf", 2: "Dirt"}.get(race.get("ground"))
            direction = {1: "Right", 2: "Left", 4: "Straight"}.get(race.get("turn"))
            course = (track, f"{distance:,} m" if isinstance(distance, int) else None, surface, direction)
            if any(course):
                embed.add_field(name="Course", value=" · ".join(filter(None, course)), inline=False)
            conditions = (
                {1: "Firm", 2: "Good", 3: "Soft", 4: "Heavy"}.get(race.get("condition")),
                {1: "Spring", 2: "Summer", 3: "Autumn", 4: "Winter"}.get(race.get("season")),
                {1: "Sunny", 2: "Cloudy", 3: "Rain", 4: "Snow"}.get(race.get("weather")),
            )
            if any(conditions):
                embed.add_field(name="Race conditions", value=" · ".join(filter(None, conditions)), inline=False)
            if track and distance and surface:
                slug = quote(track.lower().replace(" ", "-"), safe="-")
                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="View course",
                    url=f"https://gametora.com/umamusume/racetracks/{slug}#{distance}-{surface.lower()}",
                ))
        footer = "GameTora preview · Course based on JP data" if preview else "EN schedule from GameTora · Upcoming dates may change"
        embed.set_footer(text=footer)
        await interaction.edit_original_response(embed=embed, view=view)

    @champions_meeting.autocomplete("name")
    async def meeting_autocomplete(interaction: discord.Interaction, current: str):
        query = (current or "").strip().casefold()
        choices = []
        try:
            async with asyncio.timeout(2.5):
                rows = [row for row in await get_catalog(CM_CATALOG) if isinstance(row.get("id"), int)]
                choices = [
                    app_commands.Choice(name=f"{row['name']} (CM {row['id']})"[:100], value=str(row["id"]))
                    for row in reversed(rows)
                    if row.get("name") and (query in row["name"].casefold() or query == str(row["id"]))
                ]
                now = int(time())
                if not any((row.get("end") or 0) > now for row in rows):
                    if preview := await next_meeting_preview(rows, now):
                        name = preview.get("name_en") or preview.get("name") or ""
                        if name and (query in name.casefold() or query == str(preview["id"])):
                            choices.insert(0, app_commands.Choice(
                                name=f"{name} (CM {preview['id']} preview)"[:100], value=str(preview["id"]),
                            ))
        except TimeoutError:
            pass
        return choices[:25]

    @group.command(name="events", description="View current and upcoming EN events, missions, and scout banners")
    async def events(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        now = int(time())
        rows = [event for event in await get_events() if event.end > now]
        if not rows:
            await interaction.edit_original_response(content="GameTora has no current or upcoming EN events available to show.")
            return
        embed = discord.Embed(
            title="EN event schedule", url="https://gametora.com/umamusume/events", color=0x58A65C,
        )
        for upcoming, heading in ((False, "Active now"), (True, "Upcoming in the next 30 days")):
            scheduled = [event for event in rows if (event.start > now) == upcoming]
            lines = []
            for event in scheduled[:6]:
                title = discord.utils.escape_markdown(event.title)[:85]
                when = f"starts <t:{event.start}:R>" if upcoming else f"ends <t:{event.end}:R>"
                lines.append(f"• {event.category} · {title} · {when}")
            if len(scheduled) > 6:
                lines.append(f"… plus {len(scheduled) - 6} more")
            if lines:
                embed.add_field(name=heading, value="\n".join(lines), inline=False)
        embed.set_footer(text="GameTora · Use /uma reminders to get ending-soon pings")
        await interaction.edit_original_response(embed=embed)

    @group.command(name="upcoming", description="See upcoming EN banners, events, and Champions Meetings in date order")
    async def upcoming(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        rows = await get_events()
        now = int(time())
        scheduled = sorted(
            (event for event in rows if now < event.start <= now + 30 * 86400),
            key=lambda event: (event.start, event.category, event.title),
        )
        if not scheduled:
            await interaction.edit_original_response(
                content="UmaBot found no confirmed EN starts in the next 30 days in the GameTora data it received. "
                "This list excludes forecasts. Use `/uma events` for active events and "
                "`/uma cm` for Champions Meeting details or a preview when available.",
            )
            return

        lines = [
            f"<t:{event.start}:f> · **{event.category}** · {discord.utils.escape_markdown(event.title[:110])}"
            for event in scheduled[:12]
        ]
        embed = discord.Embed(
            title="Upcoming EN schedule · Next 30 days",
            url="https://gametora.com/umamusume/events",
            description="\n".join(lines),
            color=0x58A65C,
        )
        embed.set_footer(text=f"GameTora · Showing {len(lines)} of {len(scheduled)} confirmed entries · Dates may change")
        await interaction.edit_original_response(embed=embed)

    if alerts is None:
        return

    @group.command(name="reminders", description="Turn ending-soon event reminders on or off")
    async def reminders(interaction: discord.Interaction, enabled: bool):
        if not enabled:
            alerts.set_event_reminders(interaction.user.id, False)
            await interaction.response.send_message("Event reminders are off.", ephemeral=True)
            return
        try:
            channel = alert_channel(alerts, interaction)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        alerts.set_event_reminders(interaction.user.id, True)
        lead_hours = alerts.event_reminder_hours(interaction.user.id)
        await interaction.response.send_message(
            f"Event reminders are on in {channel.mention}. The current window is {lead_hours} h before an event ends. "
            "Use `/uma alert` to change the window and delivery time.",
            ephemeral=True,
        )
