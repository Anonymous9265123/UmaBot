"""Translate GameTora skill rules into race terminology."""

import re
from operator import eq, ge, gt, le, lt, ne


RULE = re.compile(r"([a-z0-9_]+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)")
COMPARISONS = {"==": "exactly", "!=": "anything except", ">=": "at least", "<=": "at most", ">": "more than", "<": "less than"}
OPERATORS = {"==": eq, "!=": ne, ">=": ge, "<=": le, ">": gt, "<": lt}
PHASES = ("early race", "mid-race", "late race", "last spurt phase")
RANDOM_PHASES = {
    "phase_random": "during",
    "phase_firsthalf_random": "in the first half of",
    "phase_laterhalf_random": "in the second half of",
    "phase_firstquarter_random": "in the first quarter of",
    "phase_corner_random": "on a turn during",
    "phase_straight_random": "on a straight during",
}
COMMON_RULES = {
    "always==1": "Any time",
    "corner==0": "Outside a corner",
    "corner!=0": "On a corner",
    "distance_rate>=50": "At least halfway through the race",
    "is_finalcorner==1": "At or past the final corner",
    "is_finalcorner_random==1": "At a random point on the final corner",
    "is_last_straight==1": "On the final straight",
    "is_lastspurt==1": "Your trainee is making her last spurt",
    "is_overtake==1": "Your trainee is trying to catch a runner ahead",
    "is_overtake==0": "Your trainee has no runner ahead to catch",
    "order_rate<=50": "In the front half of the field",
    "order_rate_out50_continue==1": "Stayed outside the top half after the first 5 seconds",
    "temptation_count==0": "Your trainee has never rushed this race",
    "activate_count_heal>=1": "Used at least one recovery skill earlier in the race",
    "straight_random==1": "At a random point on a straight",
    "all_corner_random==1": "At a random point on a corner",
}
NUMERIC_RULES = {
    "accumulatetime": "{amount} seconds into the race",
    "activate_count_all": "Activated {amount} skills this race",
    "activate_count_heal": "Recovery skills used this race: {amount}",
    "bashin_diff_behind": "Runner behind is {amount} horse lengths away",
    "bashin_diff_infront": "Runner ahead is {amount} horse lengths away",
    "course_distance": "Race length is {amount} m",
    "distance_diff_top": "{amount} m from the leader",
    "distance_rate": "Completed {amount}% of the race",
    "order_rate": "Position rank is {amount}% of the field, counting from the front",
    "remain_distance": "{amount} m left in the race",
}


def explain_rule(rule, reference):
    match = RULE.fullmatch(rule.strip())
    if not match:
        return f"`{rule.strip()}`"
    key, operator, value = match.groups()
    normalized = f"{key}{operator}{value}"
    if normalized in COMMON_RULES:
        return COMMON_RULES[normalized]
    number = float(value)
    if key == "phase":
        phases = [phase for index, phase in enumerate(PHASES) if OPERATORS[operator](index, number)]
        if phases:
            return "During " + " or ".join(phases)
    if key in RANDOM_PHASES and operator == "==" and number.is_integer() and 0 <= number < len(PHASES):
        return f"At a random point {RANDOM_PHASES[key]} the {PHASES[int(number)]}"
    if key == "order":
        if operator == "<=":
            return f"In the top {value}"
        if operator == "==":
            return f"In position {value}"
        if operator in ("<", ">", ">="):
            placement = {"<": "ahead of", ">": "behind", ">=": "at or behind"}[operator]
            return f"Running {placement} position {value}"
    amount = f"{COMPARISONS[operator]} {value}"
    if key in NUMERIC_RULES:
        return NUMERIC_RULES[key].format(amount=amount)

    metadata = reference.get(key, {})
    if normalized == metadata.get("example") and metadata.get("example_meaning"):
        return metadata["example_meaning"].rstrip(".")
    values = dict(re.findall(r"(-?\d+)\s*\(([^()]*)\)", metadata.get("values") or ""))
    if operator in ("==", "!=") and value in values:
        label = key.replace("_", " ").capitalize()
        choice = values[value]
        return f"{label} is {choice}" if operator == "==" else f"{label} is anything except {choice}"
    if metadata.get("desc"):
        return f"{metadata['desc'].rstrip('.')} ({amount})"
    return f"`{normalized}`"


def explain_conditions(expression, reference):
    alternatives = []
    for alternative in expression.split("@"):
        rules = [explain_rule(rule, reference) for rule in alternative.split("&") if rule.strip()]
        if rules:
            alternatives.append("\n".join(f"- {rule}" for rule in rules))
    return "\n**Or**\n".join(alternatives)
