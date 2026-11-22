"""Regenerate bot/cogs/loldle/champions.json from Data Dragon (fr_FR), Meraki and loldle_traits.csv.

    uv run python scripts/update_loldle_champions.py

Gender, damage, species, positions, regions and year come from `scripts/loldle_traits.csv` (derived
from github.com/OolaaPleur/loldel, MIT: see loldle_traits.LICENSE). Run this when a champion is
released. A champion missing from a source, or with a clue we cannot map, is left out and printed:
a wrong clue breaks the game, a missing champion does not.
"""

from __future__ import annotations

import csv
import json
import urllib.request
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "bot" / "cogs" / "loldle" / "champions.json"
TRAITS = Path(__file__).with_name("loldle_traits.csv")
DDRAGON = "https://ddragon.leagueoflegends.com"
MERAKI = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions.json"
CDRAGON = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champions"
ABILITY_ICON = "https://cdn.communitydragon.org/latest/champion/{id}/ability-icon/{key}"

POSITIONS = {"Top": "Top", "Jungle": "Jungle", "Middle": "Mid", "Bottom": "ADC", "Support": "Support"}
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
    "Bandle City": "Bandle",
    "Bilgewater": "Bilgewater",
    "Camavor": "Camavor",
    "Demacia": "Demacia",
    "Freljord": "Freljord",
    "Ionia": "Ionia",
    "Ixtal": "Ixtal",
    "Noxus": "Noxus",
    "Piltover": "Piltover",
    "Runeterra": "Runeterra",
    "Shadow Isles": "Îles obscures",
    "Shurima": "Shurima",
    "Targon": "Targon",
    "Void": "Néant",
    "Zaun": "Zaun",
}

GENDERS = {"Homme", "Femme", "Autre"}
DAMAGES = {"AD", "AP", "Hybride"}
SPECIES = {
    "Ascended": "Transfiguré",
    "Brackern": "Brackern",
    "Celestial": "Céleste",
    "Darkin": "Darkin",
    "Demi-God": "Demi-dieu",
    "Demon": "Démon",
    "Dragon": "Dragon",
    "Golem": "Golem",
    "Human": "Humain",
    "Iceborn": "Sang-de-glace",
    "Minotaur": "Minotaure",
    "Mummy": "Momie",
    "Spirit": "Esprit",
    "Troll": "Troll",
    "Undead": "Mort-vivant",
    "Vastayan": "Vastaya",
    "Void": "Néant",
    "Werewolf": "Loup-garou",
    "Yordle": "Yordle",
}


def _traits(t: dict | None) -> dict | None:
    if not t or t["genre"] not in GENDERS or t["degats"] not in DAMAGES:
        return None
    try:
        species = sorted({SPECIES[s.strip()] for s in t["especes"].split(",")})
        positions = sorted({POSITIONS[p.strip()] for p in t["positions"].split(",")})
        regions = sorted({REGIONS[r.strip()] for r in t["regions"].split(",")})
        year = int(t["annee"])
    except (KeyError, ValueError):
        return None
    return {
        "gender": t["genre"],
        "damage": t["degats"],
        "species": species,
        "positions": positions,
        "region": "/".join(regions),
        "year": year,
    }


def _row(dd: dict, m: dict, t: dict | None) -> dict | None:
    traits = _traits(t)
    if traits is None:
        return None
    try:
        classes = sorted(CLASSES[t] for t in dd["tags"])
        range_ = RANGES[m["attackType"]]
        resource = RESOURCES.get(m["resource"], "Autre") if m["resource"] else None
        spells = [m["abilities"][k][0]["icon"] for k in "PQWER"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not (classes and resource and all(spells)):
        return None
    return {
        "id": dd["id"],
        "name": dd["name"],
        "classes": classes,
        "range": range_,
        "resource": resource,
        "spells": spells,
        "splash": f"{DDRAGON}/cdn/img/champion/splash/{dd['id']}_0.jpg",
        **traits,
    }


def fallback_stats(dd: dict, cdragon: dict) -> dict:
    """What Meraki would have said, from Riot's own data: Meraki lags behind new releases."""
    partype = dd.get("partype") or ""
    resource = {"Mana": "MANA", "Énergie": "ENERGY", "": "NONE"}.get(partype, "OTHER")
    attack = (cdragon.get("tacticalInfo") or {}).get("attackType")
    return {
        "attackType": attack.upper() if attack else None,
        "resource": resource,
        "abilities": {k: [{"icon": ABILITY_ICON.format(id=dd["id"], key=k.lower())}] for k in "PQWER"},
    }


def build(ddragon: dict, meraki: dict, traits: dict) -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    for key in sorted(ddragon):
        row = _row(ddragon[key], meraki[key], traits.get(key)) if key in meraki else None
        if row is None:
            skipped.append(key)
        else:
            rows.append(row)
    return rows, skipped


def load_traits() -> dict[str, dict]:
    with TRAITS.open(encoding="utf-8", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f, delimiter=";")}


def _get(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "loldle-data-update"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https URLs
        return json.load(response)


def main() -> None:
    version = _get(f"{DDRAGON}/api/versions.json")[0]
    ddragon = _get(f"{DDRAGON}/cdn/{version}/data/fr_FR/champion.json")["data"]
    meraki, traits = _get(MERAKI), load_traits()
    for key in sorted(set(ddragon) - set(meraki)):
        if key in traits:
            meraki[key] = fallback_stats(ddragon[key], _get(f"{CDRAGON}/{ddragon[key]['key']}.json"))
    rows, skipped = build(ddragon, meraki, traits)
    OUTPUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} champions written (Data Dragon {version}).")
    if skipped:
        print("Left out:", ", ".join(skipped))


if __name__ == "__main__":
    main()
