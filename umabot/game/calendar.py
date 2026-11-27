"""Normalize the dated EN events published by GameTora."""

import asyncio
from dataclasses import dataclass


CM_CATALOG = "en/events/champions-meeting"
CM_REFERENCE_CATALOG = "events/champions-meeting"
CM_FORECAST_CATALOG = "en/foresight/timeline"
CM_URL = "https://gametora.com/umamusume/events/champions-meeting"
BANNER_CATALOGS = {
    "en/gacha/char-standard": "Scout banners",
    "en/gacha/support-standard": "Support banners",
}
EVENT_CATALOGS = {
    CM_CATALOG: "Champions Meeting",
    "en/events/legend-race": "Legend Races",
    "en/storyevents": "Story events",
    "en/missions/limited": "Limited missions",
    **BANNER_CATALOGS,
}
FORECAST_CATALOGS = {
    "future_cm": CM_CATALOG,
    "future_char_banners": "en/gacha/char-standard",
    "future_support_banners": "en/gacha/support-standard",
    "future_story_events": "en/storyevents",
}


@dataclass(frozen=True)
class GameEvent:
    key: str
    category: str
    title: str
    start: int
    end: int
    featured: tuple[str, ...] = ()


def event_timestamp(value):
    if type(value) is not int or value <= 0:
        return 0
    return value // 1000 if value >= 100_000_000_000 else value


def featured_names(ids, names):
    return tuple(dict.fromkeys(
        names[card_id] for card_id in ids if type(card_id) is int and names.get(card_id)
    ))


def featured_title(featured):
    title = ", ".join(featured[:3])
    if len(featured) > 3:
        title += f" +{len(featured) - 3}"
    return title


async def banner_card_names(get_catalog):
    trainees, cards = await asyncio.gather(get_catalog("character-cards"), get_catalog("support-cards"))
    return {
        "en/gacha/char-standard": {row.get("card_id"): row.get("name_en") for row in trainees},
        "en/gacha/support-standard": {row.get("support_id"): row.get("char_name") for row in cards},
    }


async def projected_events(timeline, get_catalog, confirmed, now):
    """Build a separate EN preview from GameTora's Japanese-release-based estimates."""
    known = {event.key.rsplit(":", 1)[0] for event in confirmed}
    projections = {}
    for key, source in FORECAST_CATALOGS.items():
        rows = timeline.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or type(row.get("id")) is not int:
                continue
            identity = f"{source}:{row['id']}"
            start = event_timestamp(row.get("display_start"))
            if identity not in known and now < start <= now + 30 * 86400:
                projections[identity] = (source, start, row)

    sources = {source for source, _, _ in projections.values()}
    banner_names = await banner_card_names(get_catalog) if sources & {"en/gacha/char-standard", "en/gacha/support-standard"} else {}
    stories = await get_catalog("storyevents") if "en/storyevents" in sources else []
    story_names = {row.get("event_id"): row.get("storyEventEn") or row.get("storyEventOriginal") for row in stories}
    entries = []
    for source, start, row in projections.values():
        category = EVENT_CATALOGS[source]
        title = row.get("name_en") or row.get("name")
        if source in banner_names and isinstance(row.get("pickup_ids"), list):
            title = featured_title(featured_names(row["pickup_ids"], banner_names[source]))
        if source == "en/storyevents":
            title = story_names.get(row["id"])
        entries.append((start, category, title or f"#{row['id']}"))
    return sorted(entries)


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
        banner_names = await banner_card_names(get_catalog)

    events = {}
    for key, category, title, event_id, start, end, row in visible:
        featured = ()
        if key in banner_names and isinstance(raw_pickups := row.get("pickups"), list):
            pickups = (pickup[0] for pickup in raw_pickups if isinstance(pickup, list) and pickup)
            featured = featured_names(pickups, banner_names[key])
            title = featured_title(featured) or title
        if not title:
            title = f"{category} #{event_id}" if event_id is not None else category
        identity = f"{key}:{event_id}:{start}"
        events[identity] = GameEvent(identity, category, title, start, end, featured)
    return sorted(events.values(), key=lambda event: (event.start > now, event.end if event.start <= now else event.start))
