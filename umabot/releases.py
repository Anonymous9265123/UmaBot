"""Prepare EN release messages and announcements shared by all servers."""

import discord

from .game.api import build_url, en_release_date, support_catalog_rows, support_name


def support_releases(records, now):
    messages, announcements = {}, []
    next_release = float("inf")
    for card in support_catalog_rows(records):
        date = en_release_date(card)
        if date is None:
            continue
        timestamp = date.timestamp()
        if timestamp > now:
            next_release = min(next_release, timestamp)
            continue
        topic = str(card["id"])
        name = discord.utils.escape_markdown(support_name(card) or "Support card")[:200]
        link = build_url(f"support/{topic}/gametora")
        message = f"**{name}** is available on EN. <{link}>"
        messages[topic] = message
        announcements.append((timestamp, f"support:{topic}", {"content": f"New EN support card\n{message}"}))
    return (messages, announcements), next_release


def trainee_releases(records, now):
    trainees = {}
    priority = {"Unreleased": 0, "Unknown": 1, "Released": 2}
    next_release = float("inf")
    for card in records:
        if not isinstance(card.get("char_id"), int):
            continue
        topic = f"trainee:{card['char_id']}"
        date = en_release_date(card)
        if date is None:
            status = "Unreleased" if "release_en" in card and card["release_en"] is None else "Unknown"
        elif date.timestamp() > now:
            status = "Unreleased"
            next_release = min(next_release, date.timestamp())
        else:
            status = "Released"
        previous = trainees.get(topic)
        if (
            previous is None
            or priority[status] > priority[previous[0]]
            or (status == previous[0] and date is not None and (previous[2] is None or date < previous[2]))
        ):
            name = card.get("name_en") or card.get("name_jp") or "Your trainee"
            trainees[topic] = (status, name, date)

    statuses = {topic: status for topic, (status, _, _) in trainees.items()}
    messages, announcements = {}, []
    for topic, (status, name, date) in trainees.items():
        if status == "Released":
            message = f"**{discord.utils.escape_markdown(name)[:200]}** is now playable on EN."
            messages[topic] = message
            announcements.append((date.timestamp(), topic, {"content": f"New EN character\n{message}"}))
    return (statuses, messages, announcements), next_release
