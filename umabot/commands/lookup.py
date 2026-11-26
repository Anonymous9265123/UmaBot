"""Register /uma commands and handle Discord interactions."""

import asyncio

import aiohttp
import discord
from discord import app_commands

from ..alert_settings import AlertPreferences, PostingDestination
from ..game.api import (
    birthday_image,
    build_url,
    related_character_data,
    support_character_name,
    support_name,
)
from ..embeds import (
    birthday_embeds,
    birthday_entries,
    character_embed,
    skill_embed,
    support_embed,
)
from ..game.racetracks import RACE_DIRECTIONS, RACE_SURFACES
from ..game.search import normalize_name
from ..game.support_stats import has_support_stats, load_support_stats
from ..stats import StatsButton, SupportComparisonView
from .borrow import register_borrow
from .community import register_community
from .feedback import register_feedback


class EventSubscriptionView(discord.ui.View):
    def __init__(self, alerts):
        super().__init__(timeout=180)
        self.alerts = alerts

    @discord.ui.button(label="Unsubscribe from event announcements")
    async def unsubscribe(self, interaction, button):
        self.alerts.remove_preference("events", interaction.user.id)
        await interaction.response.edit_message(
            content="Unsubscribed from EN event announcements in this server.", view=None,
        )
        self.stop()


class SubscriptionPage(discord.ui.View):
    def __init__(self, alerts, kind, prefix, entries):
        super().__init__(timeout=180)
        self.alerts = alerts
        self.kind = kind
        self.prefix = prefix
        self.entries = dict(entries)
        self.unsubscribe.options = [
            discord.SelectOption(label=name[:100], value=str(item_id), description=f"ID {item_id}")
            for item_id, (name, _) in self.entries.items()
        ]

    @property
    def content(self):
        heading = f"**Your {self.kind} alert subscriptions**"
        return heading + "".join(line for _, line in self.entries.values())

    @discord.ui.select(placeholder="Choose a subscription to unsubscribe")
    async def unsubscribe(self, interaction, select):
        item_id = int(select.values[0])
        self.alerts.remove_preference(f"{self.prefix}{item_id}", interaction.user.id)
        self.entries.pop(item_id, None)
        select.options = [option for option in select.options if option.value != str(item_id)]
        if self.entries:
            await interaction.response.edit_message(content=self.content + "\n\nUnsubscribed.", view=self)
        else:
            await interaction.response.edit_message(content="Unsubscribed. This list has no remaining subscriptions.", view=None)
            self.stop()


async def show_birthdays(interaction, fetch, data):
    entries = list(birthday_entries(data))
    if not entries:
        await interaction.edit_original_response(content="No birthdays found.")
        return

    visible = entries[:10]
    images = await asyncio.gather(
        *(birthday_image(fetch, character) for _, _, character in visible)
    )
    remaining = "\n".join(f"- {heading} — {name}" for heading, name, _ in entries[10:])
    await interaction.edit_original_response(content=remaining or None, embeds=birthday_embeds(visible, images))


async def show_character(interaction, fetch, profile, alerts):
    game_id = profile.get("game_id")
    if not isinstance(game_id, int):
        await interaction.edit_original_response(embed=character_embed(profile), view=None)
        return

    topic = f"trainee:{game_id}"
    requests = [
        related_character_data(fetch, "outfit", game_id),
        related_character_data(fetch, "support", game_id),
    ]
    if alerts:
        requests.append(alerts.trainee_status(topic))

    outfits, cards, *statuses = await asyncio.gather(*requests)
    release_status = statuses[0] if statuses else "Unknown"
    view = None
    if alerts and (release_status != "Released" or alerts.is_subscribed(topic, interaction.user.id)):
        view = alerts.view(topic)
    embed = character_embed(profile, outfits, cards, release_status)
    await interaction.edit_original_response(embed=embed, view=view)


async def lookup(
    interaction, endpoint, heading, *, birthdays=False,
    character_profile=False, support_metadata=None, alerts=None,
):
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True)

    try:
        url = build_url(endpoint)
    except ValueError as error:
        await interaction.edit_original_response(content=str(error))
        return

    try:
        fetch = interaction.client.feeds.record
        status, data = await fetch(url)
        if 300 <= status < 400:
            message = f"**{heading}**\n<{data}>" if data else "This endpoint returned a redirect without a usable link."
            await interaction.edit_original_response(content=message)
            return
        if status == 404:
            await interaction.edit_original_response(content="Couldn't find that record.")
            return
        if status == 429:
            await interaction.edit_original_response(content="Too many requests. Try again later.")
            return

        if birthdays:
            await show_birthdays(interaction, fetch, data)
            return

        if character_profile and isinstance(data, dict):
            await show_character(interaction, fetch, data, alerts)
            return

    except asyncio.TimeoutError:
        await interaction.edit_original_response(content="The API took too long to respond.")
        return
    except (aiohttp.ClientError, ValueError) as error:
        print(f"Umapyoi lookup failed: {error}")
        await interaction.edit_original_response(content="Couldn't read the API response.")
        return

    if support_metadata is not None and isinstance(data, dict):
        embed = support_embed(data, support_metadata)
        card_id = data.get("id")
        view = None
        if isinstance(card_id, int):
            view = alerts.view(card_id) if alerts and embed.fields[0].value != "Released" else discord.ui.View(timeout=None)
            view.add_item(StatsButton(card_id))
        await interaction.edit_original_response(embed=embed, view=view)
        return

    await interaction.edit_original_response(content="The API returned an unexpected format.")


def register_uma(tree, server, *, feeds, alerts=None, feedback=None):
    group = app_commands.Group(
        name="uma",
        description="Look up Umamusume game data and manage alerts",
    )
    async def get_catalog(endpoint):
        return await feeds.catalog(endpoint, timeout=2)

    if alerts is not None:
        @group.command(name="subscriptions", description="View or remove character, support card, or event announcement alerts")
        @app_commands.choices(category=[
            app_commands.Choice(name="Characters", value="characters"),
            app_commands.Choice(name="Cards", value="cards"),
            app_commands.Choice(name="Events", value="events"),
        ])
        async def subscriptions(interaction: discord.Interaction, category: app_commands.Choice[str]):
            if category.value == "events":
                if alerts.is_subscribed("events", interaction.user.id):
                    await interaction.response.send_message(
                        "You are subscribed to EN event announcements in this server.",
                        view=EventSubscriptionView(alerts),
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "You are not subscribed to EN event announcements in this server.", ephemeral=True,
                    )
                return

            characters = category.value == "characters"
            ids = alerts.subscription_ids(interaction.user.id, characters=characters)
            kind = "character" if characters else "support card"
            if not ids:
                await interaction.response.send_message(f"You have no {kind} alert subscriptions.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            if characters:
                records = await get_catalog("character/list")
                names = {row.get("game_id"): row.get("name_en") or row.get("name_jp") for row in records}
            else:
                records = await get_catalog("gametora") or await get_catalog("support")
                names = {}
                for row in records:
                    title = support_name(row)
                    character = support_character_name(row)
                    names[row.get("id")] = f"{character} — {title}" if character and title and character != title else title or character

            entries = [(str(names.get(item_id) or f"{kind.title()} #{item_id}"), item_id) for item_id in ids]
            entries.sort(key=lambda entry: entry[0].casefold())
            pages = []
            page = []
            page_length = 0
            prefix = "trainee:" if characters else ""
            for name, item_id in entries:
                label = discord.utils.escape_markdown(name[:200])
                line = f"\n- {label} (`{item_id}`)"
                if len(page) == 25 or page_length + len(line) > 1900:
                    pages.append(SubscriptionPage(alerts, kind, prefix, page))
                    page = []
                    page_length = 0
                page.append((item_id, (name, line)))
                page_length += len(line)
            pages.append(SubscriptionPage(alerts, kind, prefix, page))
            await interaction.edit_original_response(content=pages[0].content, view=pages[0])
            for view in pages[1:]:
                await interaction.followup.send(view.content, view=view, ephemeral=True)

        @group.command(name="alert", description="Set your alert time and event reminder window")
        async def alert_time(interaction: discord.Interaction):
            schedule = alerts.schedule(interaction.user.id)
            view = AlertPreferences(
                alerts, schedule[0] if schedule is not None else None,
                lead_hours=alerts.event_reminder_hours(interaction.user.id),
            )
            await interaction.response.send_message(view.prompt, view=view, ephemeral=True)

        async def setup(interaction: discord.Interaction):
            if not interaction.permissions.manage_guild:
                await interaction.response.send_message("You need Manage Server to configure UmaBot posts.", ephemeral=True)
                return
            setting = alerts.posting_setting()
            prompt = "Which text channel should I use for EN news, new characters and support cards, and subscription pings?"
            if setting is not None:
                prompt += f"\nCurrent channel <#{setting[0]}>."
            await interaction.response.send_message(prompt, view=PostingDestination(alerts), ephemeral=True)

        for name in ("setup", "news"):
            group.command(name=name, description="Choose the shared channel for UmaBot updates and pings")(setup)

    @group.command(name="birthdays", description="See today's character birthdays")
    async def birthdays(interaction: discord.Interaction):
        await lookup(
            interaction,
            "character/currentbirthdays",
            "Character birthdays",
            birthdays=True,
        )

    @group.command(name="character", description="Look up a character by name or numeric ID")
    @app_commands.describe(name="Enter a character name or pick a suggestion")
    async def character(interaction: discord.Interaction, name: str):
        name = name.strip()
        if name.isascii() and name.isdecimal():
            character_id = name
        else:
            await interaction.response.defer(thinking=True)
            characters = await get_catalog("character/list")
            character_id = feeds.autocomplete_index("character/list", characters).resolve_id(name)
            if character_id is None:
                message = "No matching character found." if characters else "The character list is temporarily unavailable."
                await interaction.edit_original_response(content=message)
                return
        await lookup(
            interaction, f"character/{character_id}", "Character details",
            character_profile=True, alerts=alerts,
        )

    @character.autocomplete("name")
    async def character_autocomplete(interaction: discord.Interaction, current: str):
        if current.isascii() and current.isdecimal():
            return []
        records = await get_catalog("character/list")
        matches = feeds.autocomplete_index("character/list", records).suggestions(current)
        return [
            app_commands.Choice(name=name[:100], value=str(character_id))
            for _, name, character_id in matches
        ]

    @group.command(name="support", description="Search for a support card")
    @app_commands.describe(name="Enter a support card or character name")
    async def support(interaction: discord.Interaction, name: str):
        name = name.strip()
        await interaction.response.defer(thinking=True)
        details = await get_catalog("gametora")
        index = feeds.autocomplete_index("gametora", details)
        cards = details
        card_id = index.resolve_id(name)
        if card_id is None and not cards:
            cards = await get_catalog("support")
            card_id = feeds.autocomplete_index("support", cards).resolve_id(name)
        if card_id is None:
            message = "No matching support card found." if cards else "The support card list is temporarily unavailable."
            await interaction.edit_original_response(content=message)
            return
        metadata = index.records_by_id.get(card_id, {})
        await lookup(
            interaction, f"support/{card_id}", "Support card details",
            support_metadata=metadata, alerts=alerts,
        )

    @support.autocomplete("name")
    async def support_autocomplete(interaction: discord.Interaction, current: str):
        endpoint = "gametora"
        records = await feeds.catalog(endpoint, timeout=1.0)
        if not records:
            endpoint = "support"
            records = await feeds.catalog(endpoint, timeout=1.2)
        index = feeds.autocomplete_index(endpoint, records)
        choices = []
        for _, name, card_id in index.suggestions(current):
            record = index.records_by_id[card_id]
            character = support_character_name(record)
            if character and normalize_name(character) != normalize_name(name):
                name = f"{character} — {name}"
            card_type = str(record.get("type") or "").strip()
            if card_type.casefold() in {"wisdom", "intelligence"}:
                card_type = "Wit"
            rarity = str(record.get("rarity_string") or record.get("rarity") or "").strip().upper()
            rarity = {"1": "R", "2": "SR", "3": "SSR"}.get(rarity, rarity)
            labels = []
            if rarity in {"R", "SR", "SSR"}:
                labels.append(rarity)
            if card_type:
                labels.append(card_type.title()[:20])
            suffix = f" ({' · '.join(labels)})" if labels else ""
            choices.append(app_commands.Choice(name=name[:100 - len(suffix)] + suffix, value=str(card_id)))
        return choices

    cards = group.command(name="cards", description=support.description)(support.callback)
    cards.autocomplete("name")(support_autocomplete)

    limit_break_choices = [app_commands.Choice(name=f"{level}LB", value=level) for level in range(5)]

    @group.command(name="compare", description="Compare two support cards at their selected limit breaks")
    @app_commands.describe(
        first="First support card name or ID",
        first_lb="Limit breaks for the first card",
        second="Second support card name or ID",
        second_lb="Limit breaks for the second card (default 4)",
    )
    @app_commands.choices(first_lb=limit_break_choices, second_lb=limit_break_choices)
    async def compare(
        interaction: discord.Interaction,
        first: app_commands.Range[str, 1, 100],
        first_lb: int,
        second: app_commands.Range[str, 1, 100],
        second_lb: int = 4,
    ):
        await interaction.response.defer(thinking=True)
        records, definitions = await load_support_stats(feeds, card_catalog="gametora")
        if not records or definitions is None:
            await interaction.edit_original_response(content="Support card stats are temporarily unavailable.")
            return

        index = feeds.autocomplete_index("gametora", records)
        selected = []
        for position, query in (("first", first), ("second", second)):
            card = index.records_by_id.get(index.resolve_id(query))
            if card is None:
                await interaction.edit_original_response(content=f"No match for the {position} support card. Try a card name or ID.")
                return
            if not has_support_stats(card):
                await interaction.edit_original_response(content=f"Stats are unavailable for the {position} support card.")
                return
            selected.append(card)

        view = SupportComparisonView(selected, (first_lb, second_lb), definitions, interaction.user.id)
        await interaction.edit_original_response(embed=view.effects_embed(), view=view)

    compare.autocomplete("first")(support_autocomplete)
    compare.autocomplete("second")(support_autocomplete)

    register_borrow(group, feeds, support_autocomplete)
    register_community(group, alerts, get_catalog, feeds.events, feeds.projections)
    if feedback is not None:
        register_feedback(group, feedback)

    @group.command(name="ping", description="Ping yourself and measure the reply time")
    async def ping(interaction: discord.Interaction):
        content = f"Pong, {interaction.user.mention}!"
        response = await interaction.response.send_message(
            content,
            allowed_mentions=discord.AllowedMentions(
                users=[interaction.user], roles=False, everyone=False, replied_user=False,
            ),
        )
        message = response.resource
        if not isinstance(message, discord.InteractionMessage):
            message = await interaction.original_response()
        delay = max(0, (message.created_at - interaction.created_at).total_seconds() * 1000)
        await interaction.edit_original_response(
            content=f"{content} Reply time **{delay:,.0f} ms**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @group.command(name="help", description="Show available UmaBot commands")
    async def help_command(interaction: discord.Interaction):
        commands = sorted(
            (f"/{item.qualified_name}", item.description)
            for item in group.walk_commands() if isinstance(item, app_commands.Command)
        )
        description = "\n".join(f"`{name}` {description}" for name, description in commands)
        await interaction.response.send_message(
            embed=discord.Embed(title="UmaBot commands", description=description), ephemeral=True,
        )

    async def show_skill(interaction, name, *, unique):
        await interaction.response.defer(thinking=True)
        index, definitions = await asyncio.gather(feeds.skills(unique=unique), get_catalog("static/skill_conditions"))
        if not index.records_by_id:
            await interaction.edit_original_response(content="The GameTora skill catalog is temporarily unavailable.")
            return
        row = index.records_by_id.get(index.resolve_id(name))
        if row is None:
            command = "/uma skill" if unique else "/uma unique"
            await interaction.edit_original_response(content=f"No matching skill found. You can also search with `{command}`.")
            return
        condition_reference = {definition["name"]: definition for definition in definitions if "name" in definition}
        await interaction.edit_original_response(embed=skill_embed(row, condition_reference))

    @group.command(name="skill", description="Look up a shared Umamusume skill")
    @app_commands.describe(name="Search by shared skill name")
    async def skill(interaction: discord.Interaction, name: app_commands.Range[str, 1, 100]):
        await show_skill(interaction, name, unique=False)

    @group.command(name="unique", description="Look up an Umamusume trainee’s unique skill")
    @app_commands.describe(name="Search by unique skill, trainee, or outfit name")
    async def unique_skill(interaction: discord.Interaction, name: app_commands.Range[str, 1, 100]):
        await show_skill(interaction, name, unique=True)

    @skill.autocomplete("name")
    async def skill_autocomplete(interaction: discord.Interaction, current: str):
        return await skill_choices(current, unique=False)

    @unique_skill.autocomplete("name")
    async def unique_autocomplete(interaction: discord.Interaction, current: str):
        return await skill_choices(current, unique=True)

    async def skill_choices(current, *, unique):
        try:
            async with asyncio.timeout(2.5):
                index = await feeds.skills(unique=unique)
        except TimeoutError:
            return []
        choices = []
        query = normalize_name(current or "")
        for _, name, skill_id in index.suggestions(current or ""):
            outfits = index.records_by_id[skill_id]["owner_outfits"]
            if outfits:
                owner = next((outfit for outfit in outfits if query in normalize_name(outfit)), outfits[0])[:70]
                suffix = f" — {owner}" + (f" +{len(outfits) - 1}" if len(outfits) > 1 else "")
                name = name[:100 - len(suffix)] + suffix
            choices.append(app_commands.Choice(name=name[:100], value=str(skill_id)))
        return choices

    @group.command(name="track", description="View an Umamusume racetrack and its courses")
    @app_commands.describe(track="Enter a track name or choose a suggestion", distance="Optional course distance in meters")
    async def racetrack(
        interaction: discord.Interaction,
        track: app_commands.Range[str, 1, 100],
        distance: app_commands.Range[int, 1, 5000] | None = None,
    ):
        await interaction.response.defer(thinking=True)
        index = await feeds.tracks()
        if not index.records_by_id:
            await interaction.edit_original_response(content="The GameTora racetrack catalog is temporarily unavailable.")
            return
        row = index.records_by_id.get(index.resolve_id(track))
        if row is None:
            await interaction.edit_original_response(content="No matching racetrack found.")
            return
        courses = [course for course in row["courses"] if distance is None or course["length"] == distance]
        if not courses:
            await interaction.edit_original_response(content=f"{row['name_en']} has no {distance} m course.")
            return

        embed = discord.Embed(title=row["name_en"], url="https://gametora.com/umamusume/racetracks")
        lines = []
        for course in sorted(courses, key=lambda item: (item["length"], item["terrain"], item["id"])):
            terrain = RACE_SURFACES.get(course["terrain"], "Unknown surface")
            direction = RACE_DIRECTIONS.get(course["turn"], "Unknown direction")
            layout = {2: "Inner", 3: "Outer"}.get(course.get("inout"))
            label = " · ".join(part for part in (f"{course['length']} m", terrain, direction, layout) if part)
            if distance is None:
                lines.append(f"- {label}")
                continue
            details = [f"Corners: {len(course.get('corners', []))}"]
            if course.get("straights"):
                final = course["straights"][-1]
                if final["end"] == course["length"]:
                    details.append(f"Final straight: {final['end'] - final['start']} m")
            if meters := course.get("spurtStart", {}).get("meters"):
                details.append(f"Last spurt starts at {meters:,} m")
            embed.add_field(name=label, value="\n".join(details), inline=False)
        if lines:
            embed.description = "\n".join(lines)
            embed.set_footer(text="Enter a distance to see course details.")
        await interaction.edit_original_response(embed=embed)

    @racetrack.autocomplete("track")
    async def racetrack_autocomplete(interaction: discord.Interaction, current: str):
        index = await feeds.tracks()
        return [
            app_commands.Choice(name=name[:100], value=str(track_id))
            for _, name, track_id in index.suggestions(current)
        ]

    @racetrack.autocomplete("distance")
    async def distance_autocomplete(interaction: discord.Interaction, current: int):
        track = interaction.namespace.track
        if not track:
            return []
        index = await feeds.tracks()
        row = index.records_by_id.get(index.resolve_id(track))
        if row is None:
            return []
        prefix = str(current or "")
        lengths = sorted({course["length"] for course in row["courses"]})
        return [
            app_commands.Choice(name=f"{length:,} m", value=length)
            for length in lengths if str(length).startswith(prefix)
        ][:25]

    tree.add_command(group, guild=server)
