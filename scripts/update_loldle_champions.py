"""Regenerate bot/cogs/loldle/champions.json from Data Dragon (fr_FR) and Meraki.

    uv run python scripts/update_loldle_champions.py

Run it when a champion is released. A champion missing from a source, or with a clue we cannot
map, is left out and printed: a wrong clue breaks the game, a missing champion does not.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "bot" / "cogs" / "loldle" / "champions.json"
DDRAGON = "https://ddragon.leagueoflegends.com"
MERAKI = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions.json"

POSITIONS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "SUPPORT": "Support"}
CLASSES = {
    "Assassin": "Assassin",
    "Fighter": "Combattant",
    "Mage": "Mage",
    "Marksman": "Tireur",
    "Support": "Support",
    "Tank": "Tank",
}
RANGES = {"MELEE": "Mêlée", "RANGED": "Distance"}
RESOURCES = {"MANA": "Mana", "ENERGY": "Énergie", "NONE": "Aucune"}
REGIONS = {
    "bandle-city": "Bandle",
    "bilgewater": "Bilgewater",
    "demacia": "Demacia",
    "freljord": "Freljord",
    "ionia": "Ionia",
    "ixtal": "Ixtal",
    "mount-targon": "Targon",
    "noxus": "Noxus",
    "piltover": "Piltover",
    "shadow-isles": "Îles obscures",
    "shurima": "Shurima",
    "unaffiliated": "Runeterra",
    "void": "Néant",
    "zaun": "Zaun",
}


def _row(dd: dict, m: dict) -> dict | None:
    try:
        positions = sorted(POSITIONS[p] for p in m["positions"])
        classes = sorted(CLASSES[t] for t in dd["tags"])
        range_ = RANGES[m["attackType"]]
        resource = RESOURCES.get(m["resource"], "Autre") if m["resource"] else None
        region = REGIONS[m["faction"]]
        year = int(str(m["releaseDate"])[:4])
        spells = [m["abilities"][k][0]["icon"] for k in "PQWER"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not (positions and classes and resource and all(spells)):
        return None
    return {
        "id": dd["id"],
        "name": dd["name"],
        "positions": positions,
        "classes": classes,
        "range": range_,
        "resource": resource,
        "region": region,
        "year": year,
        "spells": spells,
        "splash": f"{DDRAGON}/cdn/img/champion/splash/{dd['id']}_0.jpg",
    }


def build(ddragon: dict, meraki: dict) -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    for key in sorted(ddragon):
        row = _row(ddragon[key], meraki[key]) if key in meraki else None
        if row is None:
            skipped.append(key)
        else:
            rows.append(row)
    return rows, skipped


def _get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "loldle-data-update"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URLs
        return json.load(response)


def main() -> None:
    version = _get(f"{DDRAGON}/api/versions.json")[0]
    ddragon = _get(f"{DDRAGON}/cdn/{version}/data/fr_FR/champion.json")["data"]
    rows, skipped = build(ddragon, _get(MERAKI))
    OUTPUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} champions written (Data Dragon {version}).")
    if skipped:
        print("Left out:", ", ".join(skipped))


if __name__ == "__main__":
    main()
