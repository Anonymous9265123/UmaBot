"""Format Discord embeds for news, birthdays, trainees, and support cards."""

import re
from calendar import month_name
from html.parser import HTMLParser

import discord

from .game.api import build_url, en_release_status, sns_icon_url, support_character_name
from .game.conditions import explain_conditions


class ArticleText(HTMLParser):
    """Convert a news article's HTML into readable paragraphs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3", "figure"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "h1", "h2", "h3", "figure"}:
            self.parts.append("\n")

    def text(self):
        lines = (
            " ".join(line.split())
            for line in "".join(self.parts).splitlines()
        )
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def plain_text(value):
    parser = ArticleText()
    parser.feed(str(value))
    parser.close()
    return parser.text()


def birthday_date(character):
    month = character.get("birth_month")
    day = character.get("birth_day")
    if isinstance(month, int) and isinstance(day, int) and 1 <= month <= 12 and 1 <= day <= 31:
        return f"{month_name[month]} {day}"
    return None


def birthday_entries(data):
    for key, characters in data.items():
        if not characters:
            continue

        heading = key.replace("_", " ").capitalize()
        if key in {"birthdays", "current_birthdays", "today_birthdays"}:
            heading = "Today's birthdays"

        for character in characters:
            name_en = character.get("name_en")
            name_jp = character.get("name_jp")
            name = name_en or name_jp or "Unknown character"
            if name_en and name_jp and name_en != name_jp:
                name += f" ({name_jp})"
            date = birthday_date(character)
            yield heading, f"{date} — {name}" if date else name, character


def birthday_embeds(entries, images):
    embeds = []
    for (heading, description, _), image in zip(entries, images):
        embed = discord.Embed(title=heading, description=description)
        if image:
            embed.set_thumbnail(url=image)
        embeds.append(embed)
    return embeds


def article_preview(body, limit=750):
    if len(body) <= limit:
        return body

    excerpt = body[:limit]
    cut = excerpt.rfind("\n")
    if cut < limit // 2:
        cut = excerpt.rfind(" ")
    if cut > 0:
        excerpt = excerpt[:cut]

    return excerpt.rstrip() + "…"


def news_embeds(posts):
    embeds = []
    for post in posts:
        title = post.get("title") or "Untitled announcement"
        body = plain_text(post.get("message") or "")
        details = str(post.get("label_name_en") or "EN news")[:200]
        timestamp = post.get("post_at")
        if isinstance(timestamp, int):
            details += f" · <t:{timestamp}:f>"

        preview = article_preview(body) or "No article text supplied."
        embeds.append(discord.Embed(
            title=title[:256],
            description=f"{details}\n\n{preview}",
            color=0x58A65C,
        ))
    return embeds


SKILL_EFFECT_LABELS = {27: "Target Speed", 28: "Acceleration", 31: "Lane Movement Speed"}
SKILL_ACTIVATION_LABELS = {0: "Guaranteed when conditions are met", 1: "Wit check"}


def skill_summary(skill):
    description = plain_text(skill.get("desc_en") or "").strip()
    description = re.sub(r"\bvelocity\b", "speed", description, flags=re.IGNORECASE)
    if not description:
        description = "No English description is available."
    return description[0].upper() + description[1:]


def skill_conditions(group, condition_reference):
    sections = []
    for key, label in (("precondition", "Before it can trigger"), ("condition", "When it can trigger")):
        if expression := group.get(key):
            sections.append(f"*{label}*\n{explain_conditions(expression, condition_reference)}")
    return "\n\n".join(sections)


def skill_effects(group):
    details = []
    for effect in group.get("effects", []):
        kind, value = effect.get("type"), effect.get("value")
        label = SKILL_EFFECT_LABELS.get(kind)
        if label and isinstance(value, (int, float)):
            detail = f"{value / 10000:+g} {label.lower()}"
        else:
            detail = f"Type {kind}, data value {value}"
        if effect.get("additional_activation"):
            detail += " (additional activation)"
        details.append(detail)
    duration = group.get("base_time")
    if isinstance(duration, int) and duration > 0:
        details.append(f"{duration / 10000:g} s base duration")
    return " · ".join(details)


def skill_embed(skill, condition_reference):
    owners = skill["owners"]
    name = skill["name_en"]
    if owners:
        name += f" — {owners[0]}"
        if len(owners) > 1:
            name += f" +{len(owners) - 1}"
    groups = skill.get("condition_groups") or []
    description = [skill_summary(skill)[:850]]
    if outfits := skill.get("owner_outfits"):
        description.insert(0, f"*{', '.join(outfits)[:300]}*")
    if len(groups) == 1 and (effects := skill_effects(groups[0])):
        description.append(effects[:500])
    embed = discord.Embed(
        title=name[:256],
        url="https://gametora.com/umamusume/skills",
        description="\n\n".join(description),
        color=0x58A65C,
    )
    if isinstance(card_id := skill.get("owner_card"), int):
        embed.set_thumbnail(url=f"https://media.gametora.com/umamusume/characters/portrait/trainee/256/{card_id}.png")
    elif isinstance(icon_id := skill.get("iconid"), int):
        embed.set_thumbnail(url=f"https://media.gametora.com/umamusume/skills/icon/{icon_id}.png")
    rarity = {
        1: "Normal", 2: "Rare", 3: "Unique (1★)", 4: "Unique (2★)",
        5: "Unique (3★)", 6: "Evolved",
    }.get(skill.get("rarity"))
    activation = SKILL_ACTIVATION_LABELS.get(skill.get("activation"))
    metadata = [label for label in (rarity, activation) if label]
    if isinstance(cost := skill.get("cost"), int) and cost > 0:
        metadata.append(f"{cost} skill points")
    if metadata:
        embed.set_footer(text=" · ".join(metadata))

    for index, group in enumerate(groups[:4], start=1):
        conditions = skill_conditions(group, condition_reference)
        if len(groups) == 1:
            title, details = "Race conditions", conditions
        else:
            title = f"Trigger {index}"
            details = "\n\n".join(filter(None, (skill_effects(group), conditions)))
        if details:
            embed.add_field(name=title, value=details[:900], inline=False)
    if inherited := skill.get("inherited_version"):
        facts = []
        if isinstance(cost := inherited.get("cost"), int) and cost > 0:
            facts.append(f"{cost} skill points")
        if inherited_activation := SKILL_ACTIVATION_LABELS.get(inherited.get("activation")):
            facts.append(inherited_activation)
        inherited_groups = inherited.get("condition_groups") or []
        rule_keys = ("precondition", "condition")
        same_rules = (
            bool(inherited_groups)
            and [tuple(group.get(key) for key in rule_keys) for group in inherited_groups]
            == [tuple(group.get(key) for key in rule_keys) for group in groups]
            and any(group.get(key) for group in groups for key in rule_keys)
        )
        sections = []
        for index, group in enumerate(inherited_groups, start=1):
            details = [skill_effects(group)]
            if len(inherited_groups) > 1:
                details.insert(0, f"*Trigger {index}*")
            if not same_rules:
                details.append(skill_conditions(group, condition_reference))
            sections.append("\n\n".join(filter(None, details)))
        if facts:
            sections.append(" · ".join(facts))
        if same_rules:
            sections.append("Same race conditions as the original.")
        title = "Inherited version"
        limit = min(1024, 6000 - len(embed) - len(title))
        details = "\n\n".join(filter(None, sections))
        if details and limit > 0:
            embed.add_field(name=title, value=details[:limit], inline=False)
    return embed


def related_items(records, category):
    for record in records:
        if not isinstance(record, dict):
            continue
        label = (
            record.get("title_en") or record.get("name_en")
            or record.get("title") or record.get("name")
            or record.get("gametora")
        )
        record_id = record.get("id")
        if not label and not isinstance(record_id, int):
            continue
        label = discord.utils.escape_markdown(str(label or f"{category.title()} {record_id}")[:80])
        rarity = record.get("rarity_string") if category == "support" else None
        if rarity:
            label = f"{str(rarity)[:10]} · {label}"
        link = build_url(f"{category}/{record_id}/gametora") if isinstance(record_id, int) else None
        line = f"• [{label}]({link})" if link else f"• {label}"
        if category == "support" and en_release_status(record) == "Unreleased":
            line += " — **Unreleased (Global)**"
        yield line


def add_related_field(embed, heading, records, category):
    lines = list(related_items(records, category))

    if lines:
        visible = lines[:]
        while len("\n".join(visible)) > 985:
            visible.pop()
        if len(visible) < len(lines):
            visible.append(f"… and {len(lines) - len(visible)} more")
        embed.add_field(name=heading, value="\n".join(visible), inline=False)


def character_embed(profile, outfits=(), support_cards=(), release_status="Unknown"):
    name_en = profile.get("name_en")
    name_jp = profile.get("name_jp")
    title = name_en or name_jp or "Character details"
    if name_en and name_jp and name_en != name_jp:
        title = f"{name_en} ({name_jp})"

    description = "\n\n".join(
        plain_text(profile[key]) for key in ("slogan", "profile") if profile.get(key)
    )
    embed = discord.Embed(title=title[:256], description=description[:3500] or None, color=0x58A65C)
    date = birthday_date(profile)
    if date:
        embed.add_field(name="Birthday", value=date)
    embed.add_field(name="EN release", value=release_status)
    icon = sns_icon_url(profile)
    if icon:
        embed.set_thumbnail(url=icon)
    add_related_field(embed, "Playable outfits", outfits, "outfit")
    add_related_field(embed, "Support cards", support_cards, "support")
    return embed


def support_embed(record, details=None):
    details = details or {}
    character = details.get("char_name") or support_character_name(record) or "Support card"
    card_name = (
        record.get("title_en") or details.get("title_en")
        or record.get("title") or details.get("title")
    )
    metadata = [str(record[key]) for key in ("rarity_string", "type") if record.get(key)]
    description = [" · ".join(metadata)] if metadata else []
    if card_name:
        description.insert(0, plain_text(card_name))
    card_id = record.get("id")
    url = build_url(f"support/{card_id}/gametora") if isinstance(card_id, int) else None
    embed = discord.Embed(
        title=character[:256], description="\n".join(description) or None,
        url=url, color=0x58A65C,
    )
    status = en_release_status({**record, **details})
    embed.add_field(name="EN release", value=status)
    image = next(
        (record[key] for key in ("image_url", "image", "thumb_img")
         if isinstance(record.get(key), str) and record[key].startswith(("https://", "http://"))),
        None,
    )
    if not image and isinstance(card_id, int):
        image = f"https://gametora.com/images/umamusume/supports/tex_support_card_{card_id}.png"
    if image:
        embed.set_thumbnail(url=image)
    return embed
