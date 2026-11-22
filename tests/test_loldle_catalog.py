"""The champion snapshot: a wrong or empty clue breaks the game, so a bad row is dropped, not guessed."""

from bot.cogs.loldle import catalog
from scripts.update_loldle_champions import build


def _dd(**kw):
    return {"id": "Ahri", "name": "Ahri", "tags": ["Mage", "Assassin"], **kw}


def _meraki(**kw):
    icon = "https://cdn.example/ahri/{}"
    return {
        "positions": ["MIDDLE"],
        "attackType": "RANGED",
        "resource": "MANA",
        "faction": "ionia",
        "releaseDate": "2011-12-14",
        "abilities": {k: [{"icon": icon.format(k)}] for k in "PQWER"},
        **kw,
    }


def test_build_maps_every_clue_to_its_label():
    rows, skipped = build({"Ahri": _dd()}, {"Ahri": _meraki()})
    assert skipped == []
    assert rows[0] == {
        "id": "Ahri",
        "name": "Ahri",
        "positions": ["Mid"],
        "classes": ["Assassin", "Mage"],
        "range": "Distance",
        "resource": "Mana",
        "region": "Ionia",
        "year": 2011,
        "spells": [f"https://cdn.example/ahri/{k}" for k in "PQWER"],
        "splash": "https://ddragon.leagueoflegends.com/cdn/img/champion/splash/Ahri_0.jpg",
    }


def test_build_groups_rare_resources_and_names_unaffiliated():
    rows, _ = build({"Ahri": _dd()}, {"Ahri": _meraki(resource="BLOOD_WELL", faction="unaffiliated")})
    assert rows[0]["resource"] == "Autre" and rows[0]["region"] == "Runeterra"


def test_build_skips_a_champion_missing_from_meraki():
    rows, skipped = build({"Ahri": _dd(), "Locke": _dd(id="Locke", name="Locke")}, {"Ahri": _meraki()})
    assert [r["id"] for r in rows] == ["Ahri"] and skipped == ["Locke"]


def test_build_skips_empty_or_unknown_clues():
    for bad in ({"positions": []}, {"faction": "atlantis"}, {"releaseDate": ""}, {"attackType": None}):
        rows, skipped = build({"Ahri": _dd()}, {"Ahri": _meraki(**bad)})
        assert rows == [] and skipped == ["Ahri"], bad


def test_committed_snapshot_is_complete():
    champions = catalog.load()
    assert len(champions) >= 171
    for c in champions.values():
        assert c.name and c.positions and c.classes and c.range and c.resource and c.region and c.year >= 2009
        assert len(c.spells) == 5 and c.splash


def test_champion_json_round_trip():
    c = catalog.load()["Ahri"]
    assert catalog.Champion.from_json(c.to_json()) == c


def test_search_ignores_case_accents_and_punctuation():
    assert "Kaisa" in [c.id for c in catalog.search("kai")]
    assert catalog.search("kai")[0].name.lower().startswith("kai")
    assert len(catalog.search("a")) <= 25


def test_find_exact_id_or_name_only():
    assert catalog.find("kaisa").id == "Kaisa"
    assert catalog.find("Kai'Sa").id == "Kaisa"
    assert catalog.find("nope") is None
    assert catalog.find("kai") is None
