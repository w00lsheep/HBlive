"""Erzeugt einen iCalendar-Feed (.ics) aus den gescrapten Spielen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

GAME_DURATION = timedelta(minutes=90)


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """RFC 5545: Zeilen nach 75 Oktetts umbrechen."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 75:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def build_ics(data: dict) -> str:
    stamp = _fmt(datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//HBlive//MTV Hondelage Herren//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(data.get('club', {}).get('name', 'MTV Hondelage'))} Herren",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]
    for team in data.get("teams", []):
        for g in team.get("games", []):
            if not g.get("kickoff"):
                continue
            start = datetime.fromisoformat(g["kickoff"])
            summary = f"{team['label']}: {g['home']} – {g['away']}"
            if g.get("home_goals") is not None:
                summary += f" ({g['home_goals']}:{g['away_goals']})"
            desc = [g.get("competition", ""), f"Spiel-Nr. {g.get('number', '')}"]
            if g.get("referees"):
                desc.append(f"Schiedsrichter: {g['referees']}")
            if g.get("time_note"):
                desc.append(g["time_note"])
            if g.get("result_note"):
                desc.append(g["result_note"])
            lines += [
                "BEGIN:VEVENT",
                f"UID:hblive-{team['key']}-{g.get('number')}-{g.get('date')}@hblive",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{_fmt(start)}",
                f"DTEND:{_fmt(start + GAME_DURATION)}",
                f"SUMMARY:{_esc(summary)}",
                f"LOCATION:{_esc(g.get('venue_name') or g.get('venue_code') or '')}",
                f"DESCRIPTION:{_esc(chr(10).join(d for d in desc if d))}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
