"""Scraper für die Spiele der Herrenmannschaften des MTV Hondelage auf nuLiga (HVNB).

Ablauf:
  1. Vereinsseite "Mannschaften und Ligeneinteilung" (clubTeams) laden und dort die
     gewünschten Mannschaften (Standard: "Männer" und "Männer II") finden.
  2. Pro Mannschaft/Wettbewerb die Staffelseite (Tabelle) und den Gesamtspielplan der
     Staffel laden.
  3. Tabelle und alle Spiele der eigenen Mannschaft als JSON ausgeben.

Aufruf:
    python -m hblive.scraper -o data/data.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

log = logging.getLogger("hblive.scraper")

BASE_URL = "https://hvnb-handball.liga.nu"
WA = "/cgi-bin/WebObjects/nuLigaHBDE.woa/wa"
CLUB_ID = "32005"  # MTV Hondelage
TZ = ZoneInfo("Europe/Berlin")
USER_AGENT = "HBlive/1.0 (MTV Hondelage Dashboard; +https://github.com/w00lsheep/HBlive)"

# nuLiga-Mannschaftsbezeichnung -> (Schlüssel, Anzeigename)
DEFAULT_TEAMS: dict[str, tuple[str, str]] = {
    "Männer": ("herren1", "1. Herren"),
    "Männer II": ("herren2", "2. Herren"),
}

SCORE_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")
ROMAN_RE = re.compile(r"\s+([IVX]+)$")

Fetcher = Callable[[str], str]


# --------------------------------------------------------------------------- HTTP


class HttpFetcher:
    """Lädt Seiten mit Session, Timeout, Retries und kleiner Pause zwischen Requests."""

    def __init__(self, delay: float = 0.5, retries: int = 3, timeout: float = 20.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9"})
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0

    def __call__(self, url: str) -> str:
        url = urljoin(BASE_URL, url)
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                log.debug("GET %s", url)
                resp = self.session.get(url, timeout=self.timeout)
                self._last = time.monotonic()
                resp.raise_for_status()
                resp.encoding = resp.encoding or "utf-8"
                return resp.text
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("Fehler beim Laden (%d/%d) %s: %s", attempt, self.retries, url, exc)
                time.sleep(2**attempt)
        raise RuntimeError(f"Konnte {url} nicht laden: {last_exc}")


# ------------------------------------------------------------------------ helpers


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _abs(href: str | None) -> str | None:
    return urljoin(BASE_URL, href) if href else None


def _query(href: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(href).query).items()}


def _title(node: Tag | None) -> str | None:
    """Erstes title-Attribut des Knotens oder eines Kindknotens."""
    if node is None:
        return None
    if node.get("title"):
        return str(node["title"]).strip()
    child = node.find(attrs={"title": True})
    return str(child["title"]).strip() if child else None


def _h1_lines(soup: BeautifulSoup) -> list[str]:
    h1 = soup.find("h1")
    if not h1:
        return []
    return [line.strip() for line in h1.get_text("\n").split("\n") if line.strip()]


# ------------------------------------------------------------------------ parsing


@dataclass
class TeamEntry:
    nuliga_name: str
    league: str
    championship: str
    group_id: str
    group_url: str
    season: str


def parse_club_teams(html: str) -> tuple[str, list[TeamEntry]]:
    """Parst die clubTeams-Seite -> (Vereinsname, Mannschaften)."""
    soup = BeautifulSoup(html, "html.parser")
    lines = _h1_lines(soup)
    club_name = lines[0] if lines else "MTV Hondelage"  # "<Verein>\n Mannschaften und Ligeneinteilung"
    entries: list[TeamEntry] = []
    season = ""
    for table in soup.select("table.result-set"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) == 1:  # Abschnittsüberschrift, z. B. "HR Braunschweig-Göttingen 2026/27"
                season = _text(cells[0])
                continue
            link = tr.find("a", href=re.compile(r"groupPage"))
            if not link or len(cells) < 2:
                continue
            q = _query(link["href"])
            entries.append(
                TeamEntry(
                    nuliga_name=_text(cells[0]),
                    league=_text(cells[1]),
                    championship=q.get("championship", ""),
                    group_id=q.get("group", ""),
                    group_url=_abs(link["href"]) or "",
                    season=season,
                )
            )
    return club_name, entries


def _split_pair(text: str) -> tuple[int | None, int | None]:
    m = SCORE_RE.match(text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def parse_standings(html: str) -> tuple[list[dict], str | None]:
    """Parst die Tabelle einer Staffelseite -> (Tabelle, Link zum Gesamtspielplan)."""
    soup = BeautifulSoup(html, "html.parser")
    standings: list[dict] = []
    for table in soup.select("table.result-set"):
        header = [_text(c) for c in table.find("tr").find_all(["th", "td"])] if table.find("tr") else []
        if "Rang" not in header or "Punkte" not in header:
            continue
        idx = {name: i for i, name in enumerate(header) if name}
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) < len(header):
                continue
            val = lambda col: _text(cells[idx[col]]) if col in idx else ""  # noqa: E731
            gf, ga = _split_pair(val("Tore"))
            pp, pm = _split_pair(val("Punkte"))
            link = tr.find("a", href=re.compile(r"teamPortrait"))
            standings.append(
                {
                    "rank": int(val("Rang")) if val("Rang").isdigit() else None,
                    "team": val("Mannschaft"),
                    "games": int(val("Begegnungen") or 0),
                    "won": int(val("S") or 0),
                    "drawn": int(val("U") or 0),
                    "lost": int(val("N") or 0),
                    "goals_for": gf,
                    "goals_against": ga,
                    "goal_diff": int(val("+/-").replace("+", "") or 0) if re.match(r"^[+-]?\d+$", val("+/-")) else None,
                    "points_plus": pp,
                    "points_minus": pm,
                    "team_url": _abs(link["href"]) if link else None,
                }
            )
        break
    schedule = soup.find("a", href=re.compile(r"displayDetail=meetings"))
    return standings, (_abs(schedule["href"]) if schedule else None)


def parse_schedule(html: str) -> list[dict]:
    """Parst einen Spielplan (Staffel-Gesamtspielplan) -> alle Spiele der Staffel."""
    soup = BeautifulSoup(html, "html.parser")
    games: list[dict] = []
    for table in soup.select("table.result-set"):
        first = table.find("tr")
        header = [_text(c) for c in first.find_all(["th", "td"])] if first else []
        if "Heimmannschaft" not in header:
            continue
        weekday = date_str = ""
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) < 8:
                continue
            # Leere Tag/Datum-Zellen bedeuten: gleicher Tag wie die Zeile davor.
            weekday = _text(cells[0]) or weekday
            date_str = _text(cells[1]) or date_str
            time_cell = cells[2]
            time_txt = _text(time_cell)
            tm = re.match(r"(\d{1,2}:\d{2})\s*(.*)", time_txt)
            kickoff_time = tm.group(1) if tm else None
            marker = (tm.group(2).strip() if tm else time_txt) or None
            time_note = _title(time_cell) if time_cell.get("title") else None

            venue_cell = cells[3]
            venue_link = venue_cell.find("a")
            result_cell = cells[7]
            result_txt = _text(result_cell)
            report_link = result_cell.find("a", href=re.compile(r"MeetingReport"))
            pdf_link = cells[8].find("a", href=True) if len(cells) > 8 else None
            approved = bool(tr.find("img", attrs={"title": re.compile("genehmigt")}))

            home_goals, away_goals = _split_pair(result_txt)
            halftime = None
            referees = None
            result_note = None
            if home_goals is not None:
                ht = result_cell.find(attrs={"title": re.compile("Halbzeit")})
                hm = re.search(r"(\d+)\s*:\s*(\d+)", str(ht["title"])) if ht else None
                if hm:
                    halftime = {"home": int(hm.group(1)), "away": int(hm.group(2))}
            elif result_cell.get("title"):
                # z. B. "NG" mit title="Gastmannschaft nicht angetreten"
                result_note = str(result_cell["title"]).strip()
            elif result_txt:
                # Vor dem Spiel stehen hier die angesetzten Schiedsrichter.
                referees = _title(result_cell) or result_txt

            kickoff = None
            try:
                d = datetime.strptime(date_str, "%d.%m.%Y")
                if kickoff_time:
                    hh, mm = map(int, kickoff_time.split(":"))
                    d = d.replace(hour=hh, minute=mm)
                kickoff = d.replace(tzinfo=TZ).isoformat()
            except ValueError:
                pass

            meeting_id = _query(report_link["href"]).get("meeting") if report_link else None
            if not meeting_id and pdf_link:
                meeting_id = _query(pdf_link["href"]).get("meeting")

            games.append(
                {
                    "number": _text(cells[4]),
                    "meeting_id": meeting_id,
                    "weekday": weekday,
                    "date": date_str,
                    "time": kickoff_time,
                    "kickoff": kickoff,
                    "time_marker": marker,  # "v" = verlegt, "x" = ...
                    "time_note": time_note,
                    "venue_code": _text(venue_cell) or None,
                    "venue_name": _title(venue_cell),
                    "venue_url": _abs(venue_link["href"]) if venue_link else None,
                    "home": _text(cells[5]),
                    "away": _text(cells[6]),
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "halftime": halftime,
                    "result_text": result_txt or None,
                    "result_note": result_note,
                    "referees": referees,
                    "report_url": _abs(report_link["href"]) if report_link else None,
                    "report_pdf_url": _abs(pdf_link["href"]) if pdf_link else None,
                    "report_approved": approved,
                }
            )
    return games


def expected_team_name(club_name: str, nuliga_name: str) -> str:
    """'Männer II' -> 'MTV Hondelage II', 'Männer' -> 'MTV Hondelage'."""
    m = ROMAN_RE.search(nuliga_name)
    return f"{club_name} {m.group(1)}" if m else club_name


def find_own_team(standings: list[dict], games: list[dict], expected: str, club_name: str) -> str:
    names = {s["team"] for s in standings} | {g["home"] for g in games} | {g["away"] for g in games}
    if expected in names:
        return expected
    candidates = sorted(n for n in names if n.startswith(club_name))
    if candidates:
        log.warning("Mannschaft %r nicht gefunden, verwende %r", expected, candidates[0])
        return candidates[0]
    return expected


def enrich_game(game: dict, own: str) -> dict:
    g = dict(game)
    g["is_home"] = g["home"] == own
    g["opponent"] = g["away"] if g["is_home"] else g["home"]
    if g["home_goals"] is not None:
        ours, theirs = (
            (g["home_goals"], g["away_goals"]) if g["is_home"] else (g["away_goals"], g["home_goals"])
        )
        g["goals_for"], g["goals_against"] = ours, theirs
        g["outcome"] = "W" if ours > theirs else "D" if ours == theirs else "L"
        g["state"] = "finished"
    elif g["result_note"]:
        # z. B. "Gastmannschaft nicht angetreten" -> Spiel wird für den Gegner gewertet
        note = g["result_note"].lower()
        home_absent, away_absent = "heimmannschaft nicht" in note, "gastmannschaft nicht" in note
        we_absent = home_absent if g["is_home"] else away_absent
        they_absent = away_absent if g["is_home"] else home_absent
        g["goals_for"] = g["goals_against"] = None
        g["outcome"] = "L" if we_absent and not they_absent else "W" if they_absent and not we_absent else None
        g["state"] = "special"
    else:
        g["goals_for"] = g["goals_against"] = None
        g["outcome"] = None
        g["state"] = "scheduled"
    return g


# ------------------------------------------------------------------------ scraping


def scrape(
    fetch: Fetcher | None = None,
    club_id: str = CLUB_ID,
    teams: dict[str, tuple[str, str]] | None = None,
) -> dict:
    fetch = fetch or HttpFetcher()
    teams = teams or DEFAULT_TEAMS
    club_url = f"{BASE_URL}{WA}/clubTeams?club={club_id}"
    club_name, entries = parse_club_teams(fetch(club_url))
    log.info("Verein %s: %d Mannschaften/Wettbewerbe gefunden", club_name, len(entries))

    result_teams: dict[str, dict] = {}
    for entry in entries:
        if entry.nuliga_name not in teams:
            continue
        key, label = teams[entry.nuliga_name]
        standings, schedule_url = parse_standings(fetch(entry.group_url))
        schedule_url = schedule_url or (
            f"{BASE_URL}{WA}/groupPage?displayTyp=vorrunde&displayDetail=meetings"
            f"&championship={requests.utils.quote(entry.championship)}&group={entry.group_id}"
        )
        all_games = parse_schedule(fetch(schedule_url))
        own = find_own_team(standings, all_games, expected_team_name(club_name, entry.nuliga_name), club_name)
        for row in standings:
            row["is_own"] = row["team"] == own
        own_games = [enrich_game(g, own) for g in all_games if own in (g["home"], g["away"])]
        for g in own_games:
            g["competition"] = entry.league
        log.info("%s (%s): %d Spiele, %d Tabellenplätze", label, entry.league, len(own_games), len(standings))

        team = result_teams.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "nuliga_name": entry.nuliga_name,
                "team_name": own,
                "competitions": [],
                "games": [],
            },
        )
        team["competitions"].append(
            {
                "league": entry.league,
                "season": entry.season,
                "championship": entry.championship,
                "group_id": entry.group_id,
                "group_url": entry.group_url,
                "schedule_url": schedule_url,
                "standings": standings,
            }
        )
        team["games"].extend(own_games)

    for team in result_teams.values():
        team["games"].sort(key=lambda g: (g["kickoff"] or "", g["number"]))

    ordered = [result_teams[k] for _, (k, _) in teams.items() if k in result_teams]
    missing = [label for _, (k, label) in teams.items() if k not in result_teams]
    if missing:
        log.warning("Keine Daten gefunden für: %s", ", ".join(missing))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": club_url,
        "club": {"id": club_id, "name": club_name},
        "teams": ordered,
    }


def next_refresh_seconds(data: dict, now: datetime | None = None, idle: int = 900, hot: int = 120) -> int:
    """Kürzeres Intervall, solange ein eigenes Spiel läuft oder das Ergebnis noch fehlt."""
    now = now or datetime.now(timezone.utc)
    wait = idle
    for team in data.get("teams", []):
        for g in team.get("games", []):
            if g.get("state") != "scheduled" or not g.get("kickoff"):
                continue
            start = datetime.fromisoformat(g["kickoff"]) - timedelta(minutes=30)
            if start <= now <= start + timedelta(hours=5, minutes=30):
                return hot
            if now < start:  # rechtzeitig zum Beginn des nächsten "heißen" Fensters aufwachen
                wait = min(wait, max(hot, int((start - now).total_seconds())))
    return wait


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomar, damit das Dashboard nie eine halbe Datei liest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="nuLiga-Scraper für MTV Hondelage Herren")
    parser.add_argument("-o", "--output", default="data/data.json", help="Zieldatei (Standard: data/data.json)")
    parser.add_argument("--club", default=CLUB_ID, help="nuLiga-Vereins-ID (Standard: 32005)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data = scrape(club_id=args.club)
    if not data["teams"]:
        log.error("Keine Mannschaften gefunden – Datei wird nicht überschrieben.")
        return 1
    write_json(data, Path(args.output))
    log.info("Gespeichert: %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
