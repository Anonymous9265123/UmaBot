"""Prepare localized GameTora skills and trainee aliases for lookup."""

from .search import NameSearch


def skill_search(skills, trainees, *, unique):
    names = {row.get("card_id"): row.get("name_en") or row.get("name_jp") for row in trainees}
    outfits = {}
    for trainee in trainees:
        card_id = trainee.get("card_id")
        if not (trainee_name := names.get(card_id)):
            continue
        title = trainee.get("title_en_gl") or trainee.get("title")
        outfits[card_id] = f"{trainee_name} [{title.strip('[]')}]" if title else trainee_name
    rows = {}
    for skill in skills:
        rarity = skill.get("rarity")
        if rarity not in ({3, 4, 5} if unique else {1, 2}) or skill.get("inherited"):
            continue
        localized = (skill.get("loc") or {}).get("en") or {}
        row = {**skill, **localized}
        cards = [card for card in (row.get("char") or []) if card in outfits] if unique else []
        owners = sorted({names[card] for card in cards if names.get(card)})
        cards.sort(key=outfits.get)
        owner_outfits = list(dict.fromkeys(outfits[card] for card in cards))
        owner_card = cards[0] if cards else None
        name = row.get("name_en") or row.get("enname") or row.get("jpname")
        if not name:
            continue
        row = {**row, "name_en": name, "owners": owners, "owner_outfits": owner_outfits, "owner_card": owner_card}
        inherited = skill.get("gene_version") if unique else None
        if inherited:
            row["inherited_version"] = {**inherited, **(localized.get("gene_version") or {})}
        rows[row["id"]] = row
    aliases = ("owners", "owner_outfits") if unique else ()
    return NameSearch(rows.values(), alias_fields=aliases)
