"""Resolve support effects at each rarity's limit-break level cap."""


BASE_LEVELS = {1: 20, 2: 25, 3: 30}
EFFECT_LEVELS = (1, *range(5, 51, 5))


def effect_at_level(values, level):
    points = [(step, value) for step, value in zip(EFFECT_LEVELS, values) if type(value) is int and value >= 0]
    if not points:
        return None, None
    previous_level, previous_value = points[0]
    if level < previous_level:
        return None, previous_level
    for next_level, next_value in points[1:]:
        if level < next_level:
            gain = (next_value - previous_value) * (level - previous_level) // (next_level - previous_level)
            return previous_value + gain, previous_level
        previous_level, previous_value = next_level, next_value
    return previous_value, previous_level


def format_effect(effect, value):
    symbol = effect.get("symbol")
    if symbol == "percent":
        return f"{value}%"
    if symbol == "level":
        return f"Lv {value}"
    return str(value)


def support_effects(card, level, definitions):
    active, locked = [], []
    for effect_id, *values in card["effects"]:
        effect = definitions.get(effect_id, {})
        name = effect.get("name_en") or effect.get("name_en_eon") or f"Effect #{effect_id}"
        value, unlock = effect_at_level(values, level)
        if value is not None:
            active.append(f"{name} · {format_effect(effect, value)}")
        elif unlock is not None:
            locked.append(f"{name} · Lv {unlock}")
    return active, locked


def unique_effects(card, level, definitions, url):
    unique = card.get("unique") or {}
    unlock = unique.get("level")
    if type(unlock) is not int:
        return []
    if level < unlock:
        return [f"Unlocks at level {unlock}."]

    def bonus(effect_id, value):
        if effect_id in definitions and type(value) is int:
            entry = definitions[effect_id]
            if name := entry.get("name_en") or entry.get("name_en_eon"):
                return f"{name} {format_effect(entry, value)}"
        return None

    lines = []
    needs_reference = False
    for effect in unique.get("effects") or []:
        kind, value = effect.get("type"), effect.get("value")
        if kind == 101 and type(value) is int:
            pairs = [
                (effect.get(f"value_{index}"), effect.get(f"value_{index + 1}"))
                for index in (1, 3) if effect.get(f"value_{index}")
            ]
            bonuses = [bonus(effect_id, amount) for effect_id, amount in pairs]
            if bonuses and all(bonuses):
                trigger = "when the bond gauge is full" if value == 100 else f"at {value} bond or higher"
                lines.append(f"{', '.join(bonuses)} {trigger}.")
                continue
        if detail := bonus(kind, value):
            lines.append(detail)
        else:
            needs_reference = True
    if needs_reference or not lines:
        lines.append(f"Unlocked at Lv {unlock}. [See the full unique effect on GameTora]({url}).")
    return lines
