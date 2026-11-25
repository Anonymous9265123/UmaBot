"""Normalize the dated EN events published by GameTora."""

import asyncio
from dataclasses import dataclass


CM_CATALOG = "en/events/champions-meeting"
CM_REFERENCE_CATALOG = "events/champions-meeting"
CM_FORECAST_CATALOG = "en/foresight/timeline"
CM_URL = "https://gametora.com/umamusume/events/champions-meeting"
EVENT_CATALOGS = {
    CM_CATALOG: "Champions Meeting",
    "en/events/legend-race": "Legend Races",
    "en/storyevents": "Story events",
    "en/missions/limited": "Limited missions",
    "en/gacha/char-standard": "Scout banners",
    "en/gacha/support-standard": "Support banners",
}


@dataclass(frozen=True)
class GameEvent:
    key: str
    category: str
    title: str
    start: int
    end: int


def event_timestamp(value):
    if type(value) is not int or value <= 0:
        return 0
    return value // 1000 if value >= 100_000_000_000 else value


async def game_events(get_catalog, now, *, ending_within=None):
    results = await asyncio.gather(*(get_catalog(key) for key in EVENT_CATALOGS))
    visible = []
    for (key, category), rows in zip(EVENT_CATALOGS.items(), results):
        for row in rows:
            start = event_timestamp(row.get("start", row.get("startDate")))
            end = event_timestamp(row.get("end", row.get("endDate")))
            if not start or not start < end or end <= now or start > now + 30 * 86400:
                continue
            if ending_within is not None and (start > now or end - now > ending_within):
                continue
            event_id = row.get("id", row.get("event_id", row.get("eventId")))
            title = next((
                value for field in ("name", "name_en", "storyEventEn", "storyEventOriginal", "eventOriginal", "eventEn")
                if isinstance(value := row.get(field), str) and value.strip()
            ), None)
            visible.append((key, category, title, event_id, start, end, row))

    banner_names = {}
    if any(key.startswith("en/gacha/") for key, *_ in visible):
        trainees, cards = await asyncio.gather(get_catalog("character-cards"), get_catalog("support-cards"))
        banner_names = {
            "en/gacha/char-standard": {row.get("card_id"): row.get("name_en") for row in trainees},
            "en/gacha/support-standard": {row.get("support_id"): row.get("char_name") for row in cards},
        }

    events = {}
    for key, category, title, event_id, start, end, row in visible:
        if key in banner_names:
            names = banner_names[key]
            featured = list(dict.fromkeys(
                names[pickup[0]] for pickup in row.get("pickups", [])
                if isinstance(pickup, list) and pickup and isinstance(pickup[0], int) and names.get(pickup[0])
            ))
            if featured:
                title = ", ".join(featured[:3])
                if len(featured) > 3:
                    title += f" +{len(featured) - 3}"
        if not title:
            title = f"{category} #{event_id}" if event_id is not None else category
        identity = f"{key}:{event_id}:{start}"
        events[identity] = GameEvent(identity, category, title, start, end)
    return sorted(events.values(), key=lambda event: (event.start > now, event.end if event.start <= now else event.start))
