"""Open uma.moe's Global trainer search for an EN-released support card."""

import json
from base64 import b64encode
from time import time
from urllib.parse import urlencode

import discord
from discord import app_commands

from ..game.api import en_release_date, support_name


def trainer_search_url(card_id, min_lb):
    filters = json.dumps({"sc": str(card_id), "lb": min_lb}, separators=(",", ":"))
    query = urlencode({"filters": b64encode(filters.encode()).decode("ascii")})
    return f"https://uma.moe/database?{query}"


def register_borrow(group, feeds, support_autocomplete):
    borrow = app_commands.Group(name="borrow", description="Find Global trainers sharing support cards", parent=group)

    @borrow.command(name="find", description="Find Global trainers sharing an EN support card")
    @app_commands.describe(
        card="Choose a support card from autocomplete or enter its numeric ID",
        min_lb="Minimum limit break, from 0 to 4 (default 0)",
    )
    async def find(
        interaction: discord.Interaction,
        card: app_commands.Range[str, 1, 100],
        min_lb: app_commands.Range[int, 0, 4] = 0,
    ):
        await interaction.response.defer(thinking=True)
        card = card.strip()
        if not (card.isascii() and card.isdecimal()):
            await interaction.edit_original_response(content="Choose a support card from autocomplete or enter its numeric ID.")
            return

        records = await feeds.catalog("gametora", timeout=2)
        index = feeds.autocomplete_index("gametora", records)
        card_id = index.resolve_id(card)
        selected = index.records_by_id.get(card_id, {})
        date = en_release_date(selected)
        if date is None:
            message = (
                "This support card is unreleased on EN."
                if "release_en" in selected and selected["release_en"] is None
                else "I couldn’t confirm this card’s EN release."
            )
            await interaction.edit_original_response(content=message)
            return
        if (release := date.timestamp()) > time():
            await interaction.edit_original_response(content=f"This support card releases on EN on <t:{int(release)}:D>.")
            return

        name = support_name(selected) or f"Support card #{card_id}"
        listings = await feeds.borrow_trainers(card_id, min_lb)
        description = (
            f"Find Global trainers listing this card at **{min_lb}LB or higher**. "
            "Listings can change, and a trainer’s follow list may be full."
        )
        if listings is not None:
            lines = []
            for trainer_id, lb, trainer, updated in listings:
                parts = [f"`{trainer_id}`", f"{lb}LB"]
                if isinstance(trainer, str) and trainer.strip():
                    parts.append(discord.utils.escape_markdown(" ".join(trainer.split())[:70]))
                if isinstance(updated, str) and updated.strip():
                    parts.append(f"Updated {discord.utils.escape_markdown(' '.join(updated.split())[:32])}")
                lines.append(" · ".join(parts))
            description += "\n\n" + ("\n".join(lines) or "No matching trainer listings found.")
        elif feeds.umamoe_api_key:
            description += "\n\nDirect listings are temporarily unavailable. Open the filtered search below."
        embed = discord.Embed(
            title=name[:256],
            description=description,
        )
        embed.set_footer(text=f"uma.moe · Support card #{card_id}")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Find trainers on uma.moe", url=trainer_search_url(card_id, min_lb)))
        await interaction.edit_original_response(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())

    find.autocomplete("card")(support_autocomplete)
