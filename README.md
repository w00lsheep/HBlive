# HBlive

Live-Dashboard für die **1. und 2. Herren des MTV Hondelage**. Ein Web-Scraper liest
Spielplan, Ergebnisse und Tabellen von [nuLiga (HVNB)](https://hvnb-handball.liga.nu/cgi-bin/WebObjects/nuLigaHBDE.woa/wa/clubTeams?club=32005)
und ein Dashboard zeigt sie an. Es aktualisiert sich von selbst.

## Was das Dashboard zeigt

- **Heute / Live**: Spiele des Tages mit Countdown bis zum Anpfiff, Live-Anzeige mit
  Spielminute während des Spiels und „Ergebnis ausstehend“, bis nuLiga das Ergebnis hat.
- **Mannschaftskarten**: Tabellenplatz, Punkte, Bilanz (S-U-N), Tordifferenz, Form der
  letzten 5 Spiele, nächstes und letztes Spiel.
- **Spielplan**: kommende Spiele, Ergebnisse (mit Halbzeitstand) oder alle Spiele, nach Monat
  gruppiert. Angezeigt werden Halle (mit Link zu Google Maps), Schiedsrichter, verlegte
  Spiele sowie Spielbericht und PDF.
- **Tabellen** beider Staffeln, die eigene Mannschaft ist hervorgehoben.
- Liquid-Glass-Design (Milchglas, pastelliges Umgebungslicht), hell/dunkel, auf dem Handy als App-Layout mit schwebender Tab-Leiste (Alle / 1. Herren / 2. Herren). Respektiert „Transparenz reduzieren“ und „Bewegung reduzieren“.
- **Kalender-Abo** (`calendar.ics`) mit allen Spielen.
- Neues Ergebnis da? Das Dashboard blendet einen Hinweis ein und hebt das Spiel hervor.

> nuLiga hat keinen Live-Ticker. Ergebnisse erscheinen, sobald der Spielbericht eingetragen
> ist. Während eines Spiels zeigt das Dashboard deshalb „Live“ mit der laufenden Minute,
> aber noch keinen Spielstand.

## Schnellstart (lokaler Server)

```bash
pip install -r requirements.txt
python -m hblive.server            # → http://localhost:8000
```

Der Server lädt die Daten im Hintergrund neu:

| Situation | Abrufintervall |
|---|---|
| normal | alle 15 Minuten (`--interval`) |
| 30 Minuten vor Anpfiff bis 5 Stunden danach (solange das Ergebnis fehlt) | alle 2 Minuten (`--hot-interval`) |

Der Browser fragt `data.json` alle 2 Minuten ab, rund um Spiele alle 30 Sekunden. Per ETag
werden dabei nur geänderte Daten übertragen. Der Knopf ⟳ stößt sofort einen neuen Abruf an
(höchstens einmal pro Minute).

| Endpunkt | Inhalt |
|---|---|
| `/` | Dashboard |
| `/data.json` | alle Daten als JSON |
| `/calendar.ics` | Kalender-Abo |
| `/api/status` | Zustand des Scrapers (letzter Abruf, Fehler, nächster Abruf) |
| `POST /api/refresh` | sofort neu abrufen |

Mit Docker:

```bash
docker build -t hblive .
docker run -p 8000:8000 hblive
```

## Nur scrapen

```bash
python -m hblive.scraper -o data/data.json     # JSON schreiben
python -m hblive.build site/                   # statische Seite inkl. data.json + calendar.ics
```

## Hosting ohne eigenen Server (GitHub Pages)

Der Workflow `.github/workflows/pages.yml` baut das Dashboard alle 15 Minuten neu und
veröffentlicht es auf GitHub Pages. Zum Aktivieren einmalig unter
**Settings → Pages → Source** „GitHub Actions“ auswählen. GitHub startet geplante
Workflows teils mit einigen Minuten Verzögerung. Wer schnellere Updates am Spieltag will,
nimmt den Server.

## Aufbau

```
hblive/scraper.py   nuLiga-Scraper (clubTeams → Staffeltabelle + Gesamtspielplan)
hblive/server.py    HTTP-Server + Hintergrund-Aktualisierung (nur Standardbibliothek)
hblive/build.py     statischen Build erzeugen
hblive/ical.py      iCalendar-Export
web/                Dashboard (HTML/CSS/JS, keine Abhängigkeiten)
tests/              Tests gegen gespeicherte nuLiga-Seiten
```

Der Scraper sucht die Mannschaften über die Vereinsseite (Verein `32005`). Zur neuen Saison
werden Liga und Staffel deshalb automatisch übernommen. Andere Mannschaften lassen sich
über `DEFAULT_TEAMS` in `hblive/scraper.py` hinzufügen, z. B. `"männl. Jugend D"`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```
