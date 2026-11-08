"""Baut eine statische Version des Dashboards (z. B. für GitHub Pages).

    python -m hblive.build site/

Ergebnis: site/index.html, site/app.js, site/style.css, site/data.json, site/calendar.ics
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .ical import build_ics
from .scraper import next_refresh_seconds, scrape, write_json

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Statisches Dashboard bauen")
    parser.add_argument("outdir", type=Path, nargs="?", default=ROOT / "site")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    data = scrape()
    if not data["teams"]:
        logging.error("Keine Mannschaften gefunden – Abbruch.")
        return 1
    data["refresh"] = {"interval": next_refresh_seconds(data), "next_at": None, "static": True}

    out: Path = args.outdir
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "web", out)
    write_json(data, out / "data.json")
    (out / "calendar.ics").write_text(build_ics(data), encoding="utf-8")
    logging.info("Statisches Dashboard gebaut: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
