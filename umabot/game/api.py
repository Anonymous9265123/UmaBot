"""Fetch and interpret game data from Umapyoi and GameTora."""

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit

import aiohttp


API_BASE = "https://umapyoi.net/api/v1/"
CATEGORIES = {"character", "gacha", "music", "news", "outfit", "support", "va", "vpn"}


def build_url(endpoint):
    parts = urlsplit(endpoint.strip())
    if parts.scheme or parts.netloc or parts.fragment:
        raise ValueError("Enter an endpoint path, such as `en/news/latest/1`.")

    path = parts.path.strip("/").removeprefix("api/v1/")
    segments = [unquote(segment) for segment in path.split("/")]

    for segment in segments:
        if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment:
            raise ValueError("That endpoint path is invalid.")

    if segments[0] not in CATEGORIES and segments[:2] != ["en", "news"]:
        raise ValueError("Unknown API category. Try `character`, `support`, or `en/news`.")

    encoded_path = "/".join(quote(segment, safe="") for segment in segments)
    query = urlencode(parse_qsl(parts.query, keep_blank_values=True))
    url = API_BASE + encoded_path
    return f"{url}?{query}" if query else url


async def fetch_record(session, url):
    async with session.get(url, allow_redirects=False) as response:
        status = response.status
        if 300 <= status < 400:
            location = response.headers.get("Location")
            target = urljoin(url, location) if location else ""
            return status, target if urlsplit(target).scheme in {"https", "http"} else None
        if status in {404, 429}:
            return status, None
        response.raise_for_status()
        return status, await response.json(content_type=None)


def sns_icon_url(character):
    icon = character.get("sns_icon")
    if isinstance(icon, str) and icon.strip():
        url = urljoin(API_BASE, icon.strip())
        if urlsplit(url).scheme in {"http", "https"}:
            return url
    return None


async def birthday_image(session, character):
    icon = sns_icon_url(character)
    if icon:
        return icon

    character_id = character.get("id")
    if not isinstance(character_id, int):
        return None

    url = build_url(f"character/{character_id}")
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with session.get(url, timeout=timeout, allow_redirects=False) as response:
            if response.status != 200:
                return None
            profile = await response.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        return None

    return sns_icon_url(profile) if isinstance(profile, dict) else None


async def related_character_data(session, category, game_id):
    url = build_url(f"{category}/character/{game_id}")
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with session.get(url, timeout=timeout) as response:
            response.raise_for_status()
            records = await response.json(content_type=None)
    except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        return []
    return records if isinstance(records, list) else []


def en_release_date(record):
    release = record.get("release_en")
    if not isinstance(release, str):
        return None
    try:
        date = datetime.fromisoformat(release)
    except ValueError:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date


def en_release_status(record):
    if "release_en" in record and record["release_en"] is None:
        return "Unreleased"
    date = en_release_date(record)
    if date is None:
        return "Unknown"
    return "Unreleased" if date > datetime.now(timezone.utc) else "Released"


def support_name(record):
    slug = record.get("gametora") or ""
    slug = re.sub(r"^\d+-", "", slug.rsplit("/", 1)[-1])
    return record.get("title_en") or record.get("title") or record.get("name_en") or slug.replace("-", " ").title()


def support_character_name(record):
    return (
        record.get("char_name")
        or support_name({"gametora": record.get("gametora")})
    )


async def gametora_manifest(session):
    async with session.get("https://gametora.com/data/manifests/umamusume.json") as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def gametora_catalog(session, category, *, manifest=None, list_key=None):
    if manifest is None:
        manifest = await gametora_manifest(session)
    version = manifest.get(category) if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]+", version):
        raise ValueError(f"Missing GameTora catalog version for {category}")

    url = f"https://gametora.com/data/umamusume/{category}.{version}.json"
    async with session.get(url) as response:
        response.raise_for_status()
        cards = await response.json(content_type=None)
    if list_key is not None and isinstance(cards, dict):
        cards = cards.get(list_key)
    if not isinstance(cards, list):
        raise ValueError(f"Unexpected GameTora catalog format for {category}")
    return cards


def support_catalog_rows(cards):
    return [
        {
            **card,
            "id": card["support_id"],
            "name_en": card.get("char_name"),
            "title": card.get("title_ja"),
        }
        for card in cards
        if isinstance(card, dict) and isinstance(card.get("support_id"), int)
    ]
