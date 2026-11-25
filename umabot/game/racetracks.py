"""Join racetrack names with course data and define shared race labels."""

from .search import NameSearch


RACE_SURFACES = {1: "Turf", 2: "Dirt"}
RACE_DIRECTIONS = {1: "Right", 2: "Left", 4: "Straight"}


def track_search(tracks, names):
    courses_by_id = {str(row.get("id")): row["courses"] for row in tracks}
    return NameSearch(
        {**row, "courses": courses_by_id[str(row["id"])]}
        for row in names if str(row.get("id")) in courses_by_id
    )
