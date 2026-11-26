"""Show support card stats by limit break in Discord."""

from urllib.parse import quote

import discord

from .game.api import build_url
from .game.support_stats import (
    format_effect,
    has_support_stats,
    load_support_stats,
    support_effect_name,
    support_effect_values,
    support_effects,
    support_level,
    unique_effects,
)


def support_card_url(card):
    if slug := card.get("url_name"):
        return f"https://gametora.com/umamusume/supports/{quote(slug, safe='-')}"
    return build_url(f"support/{card['support_id']}/gametora")


def support_card_labels(card):
    name = card.get("name_en_gl") or card.get("char_name") or "Support card"
    title = card.get("title_en") or card.get("title_ja")
    return name, title


def support_comparison(cards, limit_breaks, definitions):
    """Show the active and unique effects at each selected level cap."""
    embed = discord.Embed(title="Support card comparison", color=0x58A65C)
    for card, limit_break in zip(cards, limit_breaks, strict=True):
        level = support_level(card, limit_break)
        name, title = support_card_labels(card)
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


class SupportComparisonView(discord.ui.View):
    def __init__(self, cards, limit_breaks, definitions, user_id):
        super().__init__(timeout=180)
        self.cards = cards
        self.limit_breaks = limit_breaks
        self.definitions = definitions
        self.user_id = user_id
        self.show_effects = True

    async def interaction_check(self, interaction):
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Run /uma compare to change your own comparison view.", ephemeral=True)
        return False

    def effects_embed(self):
        headings = []
        values = []
        for label, card, limit_break in zip(("A", "B"), self.cards, self.limit_breaks, strict=True):
            level = support_level(card, limit_break)
            name, title = support_card_labels(card)
            name = discord.utils.escape_markdown(f"{name} · {title}" if title else name)[:130]
            headings.append(f"**{label}** {name} · {limit_break}LB · Lv {level}")
            values.append({effect_id: value for effect_id, value, _ in support_effect_values(card, level)})
        left, right = values
        shared, exclusive = [], []
        for effect_id in dict.fromkeys((*left, *right)):
            first, second = left.get(effect_id), right.get(effect_id)
            if first is None and second is None:
                continue
            effect = self.definitions.get(effect_id, {})
            name = support_effect_name(effect_id, effect)
            display_name = discord.utils.escape_markdown(name)[:100]
            a = "—" if first is None else format_effect(effect, first)
            b = "—" if second is None else format_effect(effect, second)
            if first is not None and (second is None or first > second):
                a = f"**{a}**"
            if second is not None and (first is None or second > first):
                b = f"**{b}**"
            rows = shared if first is not None and second is not None else exclusive
            rows.append((name.casefold(), f"{display_name} · A {a} | B {b}"))

        sections = ["\n".join(headings)]
        remaining = 24
        for heading, rows in (("Shared stats", shared), ("Stats active on one card", exclusive)):
            if rows and remaining:
                visible = sorted(rows)[:remaining]
                sections.append(f"**{heading}**\n" + "\n".join(row for _, row in visible))
                remaining -= len(visible)
        if not shared and not exclusive:
            sections.append("No active support effects.")
        if hidden := len(shared) + len(exclusive) - (24 - remaining):
            sections.append(f"… and {hidden} more. See **Card details**.")

        return discord.Embed(
            title="Support effect comparison",
            description="\n\n".join(sections),
            color=0x58A65C,
        ).set_footer(text="Bold marks a higher or exclusive value · — means inactive or absent")

    @discord.ui.button(label="Card details")
    async def toggle(self, interaction, button):
        self.show_effects = not self.show_effects
        button.label = "Card details" if self.show_effects else "Compare effects"
        embed = self.effects_embed() if self.show_effects else support_comparison(
            self.cards, self.limit_breaks, self.definitions,
        )
        await interaction.response.edit_message(embed=embed, view=self)


class SupportStatsView(discord.ui.View):
    def __init__(self, card, definitions):
        super().__init__(timeout=180)
        self.card = card
        self.definitions = definitions
        self.url = support_card_url(card)
        self.limit_break.options = [
            discord.SelectOption(label=f"{index}LB · Lv {support_level(card, index)}", value=str(index), default=index == 4)
            for index in range(5)
        ]

    def embed(self, limit_break):
        level = support_level(self.card, limit_break)
        name, title = support_card_labels(self.card)
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
        cards, definitions = await load_support_stats(feeds)
        card = next((card for card in cards if card.get("support_id") == self.card_id), None)
        if definitions is None or not has_support_stats(card):
            url = build_url(f"support/{self.card_id}/gametora")
            await interaction.edit_original_response(
                content=f"Support stats are unavailable right now. [Open the card on GameTora]({url}).",
            )
            return
        view = SupportStatsView(card, definitions)
        await interaction.edit_original_response(embed=view.embed(4), view=view)
