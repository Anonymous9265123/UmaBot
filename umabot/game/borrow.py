"""Read trainer listings from the authenticated uma.moe search API."""

from urllib.parse import urlencode


async def trainer_listings(http, api_key, card_id, min_lb):
    listings = {}
    for page in range(2):
        query = urlencode({
            "search_type": "support_cards", "support_card_id": card_id,
            "min_limit_break": min_lb, "page": page, "limit": 20,
        })
        async with http.get(
            f"https://uma.moe/api/v3/search?{query}",
            headers={"X-API-Key": api_key}, allow_redirects=False,
        ) as response:
            response.raise_for_status()
            if response.status != 200:
                raise ValueError("Unexpected uma.moe search response")
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict) or not isinstance(items := payload.get("items"), list):
            raise ValueError("Unexpected uma.moe search format")
        for item in items:
            if not isinstance(item, dict) or not isinstance(card := item.get("support_card"), dict):
                continue
            trainer_id = item.get("account_id")
            lb = card.get("limit_break_count")
            if (
                card.get("support_card_id") != card_id or type(lb) is not int or not min_lb <= lb <= 4
                or not isinstance(trainer_id, str) or not trainer_id.isascii() or not trainer_id.isdecimal()
            ):
                continue
            listings.setdefault(trainer_id, (trainer_id, lb, item.get("trainer_name"), item.get("last_updated")))
            if len(listings) == 5:
                return list(listings.values())
        pages = payload.get("total_pages")
        if type(pages) is not int or page + 1 >= pages:
            break
    return list(listings.values())
