"""Normalize catalog names once and rank matches consistently."""

from difflib import SequenceMatcher
from functools import lru_cache
from unicodedata import normalize

from .api import support_character_name, support_name


def normalize_name(name):
    return "".join(char for char in normalize("NFKC", name).casefold() if char.isalnum())


class NameSearch:
    def __init__(self, records, *, support=False, alias_fields=()):
        self.entries = []
        self.records_by_id = {}
        keys = ("title_en", "title", "name_en", "gametora") if support else ("name_en", "name_jp", "preferred_url")
        for record in records:
            record_id = record.get("id")
            if not isinstance(record_id, int):
                continue
            self.records_by_id[record_id] = record
            name = support_name(record) if support else record.get("name_en") or record.get("name_jp")
            if not name:
                continue
            names = {
                normalize_name(value)
                for key in keys
                if isinstance(value := record.get(key), str) and value
            }
            if support and (character := support_character_name(record)):
                names.add(normalize_name(character))
            names.add(normalize_name(name))
            aliases = {normalize_name(alias) for field in alias_fields for alias in record.get(field, ())}
            self.entries.append((name, record_id, names, aliases))
        self._cached_suggestions = None

    def rank(self, query):
        ranked = []
        for name, record_id, names, aliases in self.entries:
            score = 0
            if query:
                score = max(
                    4 if query == candidate else
                    3 if candidate.startswith(query) else
                    2 if query in candidate else
                    SequenceMatcher(None, query, candidate).ratio()
                    for candidate in names
                )
                for candidate in aliases:
                    if query == candidate:
                        score = max(score, 3.5)
                    elif candidate.startswith(query):
                        score = max(score, 3)
                    elif query in candidate:
                        score = max(score, 2)
                if score < 0.6:
                    continue
            ranked.append((-score, name, record_id))
        return sorted(ranked)

    def _suggestions(self, normalized_query):
        return tuple(self.rank(normalized_query)[:25])

    def _suggestions_for(self, normalized_query):
        if self._cached_suggestions is None:
            self._cached_suggestions = lru_cache(maxsize=128)(self._suggestions)
        return self._cached_suggestions(normalized_query)

    def suggestions(self, query):
        return self._suggestions_for(normalize_name(query))

    def resolve_id(self, query):
        """Return an explicit numeric ID or the highest-ranked name match."""
        query = query.strip()
        if query.isascii() and query.isdecimal():
            return int(query)
        normalized = normalize_name(query)
        matches = self._suggestions_for(normalized) if normalized else ()
        return matches[0][2] if matches else None
