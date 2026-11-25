"""Show support card stats by limit break in Discord."""

import asyncio
from urllib.parse import quote

import discord

from .game.api import build_url
from .game.support_stats import BASE_LEVELS, support_effects, unique_effects


class SupportStatsView(discord.ui.View):
    def __init__(self, card, definitions):
        super().__init__(timeout=180)
        self.card = card
        self.definitions = definitions
        self.base = BASE_LEVELS[card["rarity"]]
        card_id = card["support_id"]
        slug = card.get("url_name")
        self.url = (
            f"https://gametora.com/umamusume/supports/{quote(slug, safe='-')}"
            if slug else build_url(f"support/{card_id}/gametora")
        )
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
