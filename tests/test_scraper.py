"""Tests gegen gespeicherte nuLiga-Seiten (Stand 08.11.2026) – kein Netzwerk nötig."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from hblive import scraper
from hblive.ical import build_ics

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def fake_fetch(url: str) -> str:
    if "clubTeams" in url:
        return read("clubTeams.html")
    suffix = "2" if "group=489228" in url else ""  # 489563 = 1. Herren, 489228 = 2. Herren
    if "displayDetail=meetings" in url:
        return read(f"groupSchedule{suffix}.html")
    if "groupPage" in url:
        return read(f"groupPage{suffix}.html")
    raise AssertionError(f"unerwartete URL {url}")


def test_parse_club_teams():
    club, entries = scraper.parse_club_teams(read("clubTeams.html"))
    assert club == "MTV Hondelage"
    names = [e.nuliga_name for e in entries]
    assert names[:2] == ["Männer", "Männer II"]
    first = entries[0]
    assert first.league == "Regionsliga Männer Staffel Nord"
    assert first.group_id == "489563"
    assert first.championship == "HRBG 26/27"
    assert first.season == "HR Braunschweig-Göttingen 2026/27"


def test_parse_standings():
    standings, schedule_url = scraper.parse_standings(read("groupPage.html"))
    assert len(standings) == 10
    own = next(s for s in standings if s["team"] == "MTV Hondelage")
    assert own["rank"] == 5
    assert (own["won"], own["drawn"], own["lost"]) == (2, 0, 2)
    assert (own["goals_for"], own["goals_against"], own["goal_diff"]) == (95, 83, 12)
    assert (own["points_plus"], own["points_minus"]) == (4, 4)
    assert "displayDetail=meetings" in schedule_url


def test_parse_schedule_and_enrich():
    games = scraper.parse_schedule(read("groupSchedule.html"))
    assert len(games) == 90
    own = [scraper.enrich_game(g, "MTV Hondelage") for g in games if "MTV Hondelage" in (g["home"], g["away"])]
    assert len(own) == 18

    by_nr = {g["number"]: g for g in own}
    # Ergebnis mit Halbzeit
    g = by_nr["12021"]
    assert (g["home_goals"], g["away_goals"]) == (35, 31)
    assert g["halftime"] == {"home": 19, "away": 14}
    assert g["outcome"] == "W" and g["state"] == "finished" and g["is_home"]
    assert g["report_url"] and g["meeting_id"] == "8324293"
    assert g["venue_name"] == "Braunschweig, SH Hondelage"
    # Datum aus der Vorzeile übernommen (leere Datumszelle)
    assert by_nr["12012"]["date"] == "03.10.2026"
    # Nicht angetreten -> als Niederlage gewertet
    ng = by_nr["12001"]
    assert ng["state"] == "special" and ng["outcome"] == "L"
    assert ng["result_note"] == "Gastmannschaft nicht angetreten"
    # Kommendes Spiel mit Schiedsrichtern
    up = by_nr["12028"]
    assert up["state"] == "scheduled" and up["referees"] == "Feyerabend Sven / Riekeberg Stefan"
    assert up["kickoff"] == "2026-11-08T16:00:00+01:00"
    assert up["opponent"] == "HC Braunschweig" and not up["is_home"]
    # verlegtes Spiel
    moved = by_nr["12047"]
    assert moved["time_marker"] == "v" and moved["time_note"].startswith("verlegt")


def test_scrape_with_fixtures():
    data = scraper.scrape(fetch=fake_fetch)
    assert data["club"]["name"] == "MTV Hondelage"
    assert [t["key"] for t in data["teams"]] == ["herren1", "herren2"]
    h1 = data["teams"][0]
    assert h1["team_name"] == "MTV Hondelage"
    assert len(h1["games"]) == 18
    assert sum(s["is_own"] for s in h1["competitions"][0]["standings"]) == 1
    kickoffs = [g["kickoff"] for g in h1["games"]]
    assert kickoffs == sorted(kickoffs)
    h2 = data["teams"][1]
    assert h2["team_name"] == "MTV Hondelage II"
    assert h2["competitions"][0]["league"] == "Regionsklasse M Nord"
    assert len(h2["games"]) == 12
    assert next(s for s in h2["competitions"][0]["standings"] if s["is_own"])["rank"] == 4


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 11, 8, 15, 30, tzinfo=timezone.utc), 120),  # Spiel läuft
        (datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc), 900),  # weit weg vom nächsten Anpfiff
        (datetime(2026, 11, 8, 14, 20, tzinfo=timezone.utc), 600),  # wacht pünktlich 30 min vor Anpfiff auf
    ],
)
def test_next_refresh_seconds(now, expected):
    data = {"teams": [{"games": [{"state": "scheduled", "kickoff": "2026-11-08T16:00:00+01:00"}]}]}
    assert scraper.next_refresh_seconds(data, now=now) == expected


def test_expected_team_name():
    assert scraper.expected_team_name("MTV Hondelage", "Männer") == "MTV Hondelage"
    assert scraper.expected_team_name("MTV Hondelage", "Männer II") == "MTV Hondelage II"


def test_ics():
    ics = build_ics(scraper.scrape(fetch=fake_fetch))
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.count("BEGIN:VEVENT") == 30  # 18 + 12 Spiele
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
