"""Show support card stats by limit break in Discord."""

import asyncio
from urllib.parse import quote

import discord

from .game.api import build_url
from .game.support_stats import BASE_LEVELS, support_effects, unique_effects


def support_card_url(card):
    if slug := card.get("url_name"):
        return f"https://gametora.com/umamusume/supports/{quote(slug, safe='-')}"
    return build_url(f"support/{card['support_id']}/gametora")


def support_comparison(cards, limit_breaks, definitions):
    """Show the active and unique effects at each selected level cap."""
    embed = discord.Embed(title="Support card comparison", color=0x58A65C)
    for card, limit_break in zip(cards, limit_breaks, strict=True):
        level = BASE_LEVELS[card["rarity"]] + limit_break * 5
        name = card.get("name_en_gl") or card.get("char_name") or "Support card"
        title = card.get("title_en") or card.get("title_ja")
        rarity = {1: "R", 2: "SR", 3: "SSR"}[card["rarity"]]
        url = support_card_url(card)
        active, _ = support_effects(card, level, definitions)
        unique = unique_effects(card, level, definitions, url)
        lines = [f"**{rarity} · {limit_break}LB · Lv {level}**"]
        if title:
            lines.append(discord.utils.escape_markdown(title[:120]))
        lines.extend(active or ["No active support effects."])
        lines.extend(["", "**Unique effect**", *(unique or ["None listed."])])
        details = "\n".join(lines)
        link = f"\n[View card on GameTora]({url})"
        budget = 1024 - len(link)
        if len(details) > budget:
            details = details[:budget - 2].rsplit("\n", 1)[0] + "\n…"
        embed.add_field(name=name[:256], value=details + link, inline=True)
    return embed


class SupportStatsView(discord.ui.View):
    def __init__(self, card, definitions):
        super().__init__(timeout=180)
        self.card = card
        self.definitions = definitions
        self.base = BASE_LEVELS[card["rarity"]]
        self.url = support_card_url(card)
        self.limit_break.options = [
            discord.SelectOption(label=f"{index}LB · Lv {self.base + index * 5}", value=str(index), default=index == 4)
            for index in range(5)
        ]

    def embed(self, limit_break):
        level = self.base + limit_break * 5
        name = self.card.get("name_en_gl") or self.card.get("char_name") or "Support card"
        title = self.card.get("title_en") or self.card.get("title_ja")
        rarity = {1: "R", 2: "SR", 3: "SSR"}[self.card["rarity"]]
        details = [item for item in (title, rarity, f"{limit_break}LB", f"Lv {level}") if item]
        embed = discord.Embed(
            title=f"{name} · Support stats"[:256],
            url=self.url,
            description=" · ".join(details),
            color=0x58A65C,
        )
        card_id = self.card["support_id"]
        embed.set_thumbnail(url=f"https://media.gametora.com/umamusume/supports/full/small/{card_id}.png")
        active, locked = support_effects(self.card, level, self.definitions)
        if active:
            embed.add_field(name="Support effects", value="\n".join(active), inline=False)
        if unique := unique_effects(self.card, level, self.definitions, self.url):
            embed.add_field(name="Unique effect", value="\n".join(unique), inline=False)
        if locked:
            embed.add_field(name="Unlocks at higher levels", value="\n".join(locked), inline=False)
        return embed

    @discord.ui.select(placeholder="Choose a limit break")
    async def limit_break(self, interaction, select):
        value = select.values[0]
        for option in select.options:
            option.default = option.value == value
        await interaction.response.edit_message(embed=self.embed(int(value)), view=self)


class StatsButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"uma-stats:(?P<card_id>[0-9]+)",
):
    def __init__(self, card_id):
        self.card_id = card_id
        super().__init__(discord.ui.Button(label="Stats", custom_id=f"uma-stats:{card_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["card_id"]))

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        feeds = interaction.client.feeds
        cards, effects = await asyncio.gather(feeds.catalog("support-cards"), feeds.catalog("support_effects"))
        card = next((card for card in cards if card.get("support_id") == self.card_id), None)
        if not effects or not card or card.get("rarity") not in BASE_LEVELS or not card.get("effects"):
            url = build_url(f"support/{self.card_id}/gametora")
            await interaction.edit_original_response(
                content=f"Support stats are unavailable right now. [Open the card on GameTora]({url}).",
            )
            return
        view = SupportStatsView(card, {effect["id"]: effect for effect in effects if "id" in effect})
        await interaction.edit_original_response(embed=view.embed(4), view=view)
