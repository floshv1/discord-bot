"""The champion snapshot: a wrong or empty clue breaks the game, so a bad row is dropped, not guessed."""

from bot.cogs.loldle import catalog
from scripts.update_loldle_champions import build, fallback_stats


def _dd(**kw):
    return {"id": "Ahri", "name": "Ahri", "tags": ["Mage", "Assassin"], **kw}


TRAITS = {
    "Ahri": {
        "genre": "Femme",
        "degats": "AP",
        "especes": "Vastayan, Human",
        "positions": "Middle, Bottom",
        "regions": "Shadow Isles, Ionia",
        "annee": "2012",
    }
}


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
    rows, skipped = build({"Ahri": _dd()}, {"Ahri": _meraki()}, TRAITS)
    assert skipped == []
    assert rows[0] == {
        "id": "Ahri",
        "name": "Ahri",
        "positions": ["ADC", "Mid"],
        "classes": ["Assassin", "Mage"],
        "range": "Distance",
        "resource": "Mana",
        "region": "Ionia/Îles obscures",
        "year": 2012,
        "spells": [f"https://cdn.example/ahri/{k}" for k in "PQWER"],
        "splash": "https://ddragon.leagueoflegends.com/cdn/img/champion/splash/Ahri_0.jpg",
        "gender": "Femme",
        "damage": "AP",
        "species": ["Humain", "Vastaya"],
    }


def test_build_groups_rare_resources_and_names_unaffiliated():
    traits = {"Ahri": {**TRAITS["Ahri"], "regions": "Runeterra"}}
    rows, _ = build({"Ahri": _dd()}, {"Ahri": _meraki(resource="BLOOD_WELL")}, traits)
    assert rows[0]["resource"] == "Autre" and rows[0]["region"] == "Runeterra"


def test_build_skips_a_champion_missing_from_meraki():
    rows, skipped = build({"Ahri": _dd(), "Locke": _dd(id="Locke", name="Locke")}, {"Ahri": _meraki()}, TRAITS)
    assert [r["id"] for r in rows] == ["Ahri"] and skipped == ["Locke"]


def test_build_skips_empty_or_unknown_clues():
    for bad in ({"attackType": None}, {"resource": ""}, {"abilities": {}}):
        rows, skipped = build({"Ahri": _dd()}, {"Ahri": _meraki(**bad)}, TRAITS)
        assert rows == [] and skipped == ["Ahri"], bad


def test_build_skips_missing_or_unknown_traits():
    assert build({"Ahri": _dd()}, {"Ahri": _meraki()}, {})[1] == ["Ahri"]
    for bad in (
        {"genre": "?"},
        {"degats": "True"},
        {"especes": "Martien"},
        {"especes": ""},
        {"positions": "Roam"},
        {"positions": ""},
        {"regions": "Atlantis"},
        {"regions": ""},
        {"annee": "bientot"},
    ):
        traits = {"Ahri": {**TRAITS["Ahri"], **bad}}
        assert build({"Ahri": _dd()}, {"Ahri": _meraki()}, traits)[1] == ["Ahri"], bad


def test_fallback_builds_what_meraki_lacks_from_riot_data():
    dd = _dd(id="Locke", name="Locke", partype="Mana")
    stats = fallback_stats(dd, {"tacticalInfo": {"attackType": "melee"}})
    assert stats["attackType"] == "MELEE" and stats["resource"] == "MANA"
    assert stats["abilities"]["Q"][0]["icon"] == "https://cdn.communitydragon.org/latest/champion/Locke/ability-icon/q"
    rows, skipped = build({"Locke": dd}, {"Locke": stats}, {"Locke": TRAITS["Ahri"]})
    assert skipped == [] and rows[0]["range"] == "Mêlée" and rows[0]["resource"] == "Mana"


def test_fallback_maps_resources_and_refuses_an_unknown_range():
    assert fallback_stats(_dd(partype="Énergie"), {"tacticalInfo": {"attackType": "ranged"}})["resource"] == "ENERGY"
    assert fallback_stats(_dd(partype=""), {"tacticalInfo": {"attackType": "ranged"}})["resource"] == "NONE"
    assert fallback_stats(_dd(partype="Rage"), {"tacticalInfo": {"attackType": "ranged"}})["resource"] == "OTHER"
    stats = fallback_stats(_dd(partype="Mana"), {})
    assert build({"Ahri": _dd()}, {"Ahri": stats}, TRAITS)[1] == ["Ahri"]


def test_the_two_latest_champions_are_in_the_game():
    champions = catalog.load()
    assert champions["Zaahen"].species == ("Darkin",) and champions["Zaahen"].range == "Mêlée"
    locke = champions["Locke"]
    assert (locke.damage, locke.region, locke.year, locke.positions) == ("AP", "Demacia", 2026, ("Mid",))
    assert locke.gender == "Homme" and locke.species == ("Humain",)


def test_committed_snapshot_is_complete():
    champions = catalog.load()
    assert len(champions) >= 171
    for c in champions.values():
        assert c.name and c.positions and c.classes and c.range and c.resource and c.region and c.year >= 2009
        assert len(c.spells) == 5 and c.splash
        assert c.gender in ("Homme", "Femme", "Autre") and c.damage in ("AD", "AP", "Hybride") and c.species


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
