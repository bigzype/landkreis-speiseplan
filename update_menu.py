#!/usr/bin/env python3
"""Landkreis-Restaurant: Wochen-PDF finden, sauber auslesen und ICS bauen."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pdfplumber
import requests

BASE_URL = "https://www.landkreis-restaurant.de/"
DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}
HEADERS = {"Eintopf", "Hauptgerichte", "Beilagen", "Gemüsebeilagen", "Dessert"}
LOCATION = "Landkreis Restaurant Osnabrück, Am Schölerberg 1, 49082 Osnabrück, Deutschland"
PHONE = "0541 5011 064"
EMAIL = "lampe@landkreisrestaurant.de"
WEBSITE = "www.landkreis-restaurant.de"
EVENT_SEQUENCE = 5


def compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_text(value: str | None) -> str:
    text = value or ""
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "-", text)
    text = re.sub(r"(?<=\w)\s*\n\s*([a-zäöüß])(?=\s|$)", r"\1", text)
    text = compact(text)
    # In fehlerhaften PDF-Exporten klebt der letzte Allergencode direkt am
    # folgenden Wort (z. B. A,C,F,LSchmand).
    text = re.sub(r"(?<!\w)(?:[A-R],){1,}[A-R](?=[A-ZÄÖÜ][a-zäöüß])", " ", text)
    # Die einzelnen Großbuchstaben sind die Allergen-/Zusatzstoff-Codes. Ein optionales
    # Schlusskomma gehört ebenfalls zum Codeblock, nicht zum Gericht.
    text = re.sub(
        r"(?<!\w)[A-Z](?!\w)(?=(?:\s*[,/]?\s*[A-Z](?!\w))|(?:\s*[,/]))"
        r"(?:\s*[,/]?\s*[A-Z](?!\w))*\s*,?",
        " ", text,
    )
    text = re.sub(r"\s+[A-Z]$", "", text)
    text = re.sub(r"\bP\b", " ", text)
    text = re.sub(r"(?<=[a-zäöüß])[A-R]\b", "", text)
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r",(?=\S)", ", ", text)
    text = re.sub(r"\s+([,.;:/])", r"\1", text)
    text = re.sub(r"\s*/\s*", " / ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\bWarp\b", "Wrap", text, flags=re.I)
    return text.strip(" -/,")


def clean_price(value: str | None) -> str:
    raw = compact(value).replace("-", "").strip()
    if not raw:
        return ""
    match = re.search(r"\d+(?:[,.]\d+)?", raw)
    if not match:
        return raw
    number = match.group(0).replace(".", ",")
    if "," not in number:
        number += ",00"
    elif len(number.split(",", 1)[1]) == 1:
        number += "0"
    return f"{number} €"


def detect_day(cell: str | None) -> str | None:
    letters = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", cell or "")
    # Der PDF-Export legt die vertikale Tages-Spalte teils in visueller,
    # teils in logischer Reihenfolge ab (z. B. „gatsneDi“). Die Buchstaben
    # sind jedoch vollständig und eindeutig.
    for day in DAYS:
        if len(letters) == len(day) and sorted(letters.lower()) == sorted(day.lower()):
            return day
    return None


def parse_period(text: str) -> date:
    match = re.search(
        r"Zeitraum\s*:?\s*(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüß]+)\s+bis\s+(?:(?:zu|zum)\s+)?(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüß]+)\.?\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if not match:
        raise ValueError("Zeitraum im PDF nicht gefunden")
    start_day, start_month, _end_day, end_month, year = match.groups()
    month = MONTHS.get(start_month.lower())
    final_month = MONTHS.get(end_month.lower())
    if not month or not final_month:
        raise ValueError(f"Unbekannter Monat: {start_month}")
    start_year = int(year) - 1 if month > final_month else int(year)
    return date(start_year, month, int(start_day))


def discover_pdf(session: requests.Session) -> str:
    response = session.get(BASE_URL, timeout=45)
    response.raise_for_status()
    links = re.findall(r'href=["\']([^"\']*Speise\d{1,2}\.\d{4}\.pdf)["\']', response.text, re.I)
    if not links:
        raise RuntimeError("Kein aktueller Speiseplan-Link auf der Website gefunden")
    return urllib.parse.urljoin(BASE_URL, html.unescape(links[0]))


def add_item(day: dict, category: str, text: str | None, price: str | None) -> None:
    item_text = clean_text(text)
    if not item_text:
        return
    day[category].append({"text": item_text, "price": clean_price(price)})


def parse_pdf(pdf_path: Path, source_url: str) -> dict:
    with pdfplumber.open(pdf_path) as pdf:
        page_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        period_start = parse_period(page_text)
        # Der gedruckte Zeitraum beginnt gelegentlich erst am Dienstag, obwohl die
        # Tabelle weiterhin Montag bis Freitag der ISO-Woche enthält.
        week_start = period_start - timedelta(days=period_start.weekday())
        tables = [table for page in pdf.pages for table in page.extract_tables()]

    table = next((t for t in tables if any(row and "Hauptgerichte" in compact(" ".join(x or "" for x in row)) for row in t)), None)
    if not table:
        raise RuntimeError("Menü-Tabelle im PDF nicht gefunden")

    header = next(row for row in table if row and "Hauptgerichte" in compact(" ".join(x or "" for x in row)))
    normalized_header = [re.sub(r"[^a-zäöüß]", "", (cell or "").lower()) for cell in header]

    def header_index(predicate, label: str) -> int:
        match = next((index for index, value in enumerate(normalized_header) if predicate(value)), None)
        if match is None:
            raise RuntimeError(f"Spalte {label} im PDF nicht gefunden")
        return match

    soup_col = header_index(lambda value: value == "eintopf", "Eintopf")
    main_col = header_index(lambda value: value == "hauptgerichte", "Hauptgerichte")
    side_col = header_index(
        lambda value: (value.endswith("beilagen") or value.startswith("sättigungsbeil"))
        and "gemüse" not in value,
        "Beilagen",
    )
    vegetable_col = header_index(lambda value: "gemüsebeilagen" in value, "Gemüsebeilagen")
    dessert_col = header_index(lambda value: value == "dessert", "Dessert")
    category_cols = [soup_col, main_col, side_col, vegetable_col, dessert_col]

    def price_column(category_col: int) -> int:
        next_category = next((index for index in category_cols if index > category_col), len(header))
        match = next((index for index in range(category_col + 1, next_category) if compact(header[index]) == "€"), None)
        if match is None:
            raise RuntimeError(f"Preisspalte nach PDF-Spalte {category_col} nicht gefunden")
        return match

    soup_price_col = price_column(soup_col)
    main_price_col = price_column(main_col)
    side_price_col = price_column(side_col)
    vegetable_price_col = price_column(vegetable_col)
    dessert_price_col = price_column(dessert_col)
    max_col = max(dessert_col, dessert_price_col)

    menus: list[dict] = []
    current: dict | None = None
    for raw_row in table:
        row = (raw_row + [None] * (max_col + 1))[:max_col + 1]
        day_name = detect_day(row[0])
        if day_name:
            current = {
                "day": day_name,
                "date": (week_start + timedelta(days=DAYS.index(day_name))).isoformat(),
                "soups": [], "mains": [], "sides": [], "vegetables": [],
                "desserts": [], "salads": [],
            }
            menus.append(current)
        if not current:
            continue

        main_text = "\n".join(x or "" for x in row[main_col:main_price_col]).strip()
        # In manchen PDFs rutscht „Wrap/Warp + Allergene“ optisch in die erste
        # Zeile des Folgetags. Der Text gehört noch zum Salat des Vortags.
        if day_name and len(menus) > 1:
            parts = main_text.splitlines()
            if parts and re.fullmatch(r"(?:Wrap|Warp)(?:\s+[A-R]){1,8}", parts[0].strip(), re.I):
                previous = menus[-2]
                if previous["salads"]:
                    previous["salads"][-1]["text"] += " " + clean_text(parts[0])
                main_text = "\n".join(parts[1:])

        add_item(current, "soups", row[soup_col], row[soup_price_col])
        if re.match(r"^(?:heute zum salat|zum salat reichen wir(?: heute)?)\s*:", main_text, re.I):
            salad_text = re.sub(
                r"^(?:heute zum salat|zum salat reichen wir(?: heute)?)\s*:\s*",
                "",
                main_text,
                flags=re.I,
            )
            add_item(current, "salads", salad_text, row[main_price_col])
        else:
            add_item(current, "mains", main_text, row[main_price_col])
        add_item(current, "sides", row[side_col], row[side_price_col])
        vegetable = clean_text(row[vegetable_col])
        if vegetable and vegetable.lower() != "tagessalat":
            add_item(current, "vegetables", vegetable, row[vegetable_price_col])
        add_item(current, "desserts", row[dessert_col], row[dessert_price_col])
        if current["day"] == "Freitag" and "mit wurst" in compact(row[0]).lower() and current["soups"]:
            soup = current["soups"][-1]["text"]
            if "wurst" not in soup.lower():
                current["soups"][-1]["text"] = soup + " mit Wurst"

    if [m["day"] for m in menus] != DAYS:
        raise RuntimeError(f"Erwartet Mo–Fr, erkannt: {[m['day'] for m in menus]}")
    required = {
        "soups": "Eintopf",
        "mains": "Hauptgericht",
        "sides": "Beilage",
        "vegetables": "Gemüsebeilage",
        "desserts": "Dessert",
    }
    for key, label in required.items():
        missing = [menu["day"] for menu in menus if not menu[key]]
        if missing:
            raise RuntimeError(f"{label} fehlt für: {chr(44).join(missing)}")

    iso = week_start.isocalendar()
    return {
        "week": f"{iso.year}-KW{iso.week:02d}",
        "week_start": week_start.isoformat(),
        "source_url": source_url,
        "menus": menus,
    }


def escape_ics(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold_ics(line: str, limit: int = 73) -> list[str]:
    # Byte-genaues Falten ist bei UTF-8 wichtig; Fortsetzungszeilen beginnen mit Leerzeichen.
    chunks: list[str] = []
    current = ""
    for char in line:
        candidate = current + char
        if len(candidate.encode("utf-8")) > limit and current:
            chunks.append(current)
            current = " " + char
        else:
            current = candidate
    chunks.append(current)
    return chunks


def priced(item: dict) -> str:
    return f"{item['text']} – {item['price']}" if item.get("price") else item["text"]


def short_name(text: str) -> str:
    text = re.split(r"\s+(?:auf|mit|an)\s+|[„“/]", text, maxsplit=1, flags=re.I)[0]
    return text.strip(" ,.-")[:70]


def event_description(menu: dict) -> str:
    lines: list[str] = []

    def section(title: str, items: list[dict], prefix: str = "") -> None:
        if not items:
            return
        if lines:
            lines.append("")
        lines.append(title.upper())
        lines.append("─" * len(title))
        lines.extend(f"• {prefix}{priced(item)}\n" for item in items)

    section("Eintopf", menu["soups"])
    if menu["mains"] or menu["salads"]:
        if lines:
            lines.append("")
        lines.append("HAUPTGERICHTE")
        lines.append("─" * len("Hauptgerichte"))
        lines.extend(f"• {priced(item)}\n" for item in menu["mains"])
        lines.extend(f"• Heute zum Salat: {priced(item)}\n" for item in menu["salads"])
    section("Beilagen", menu["sides"])
    section("Gemüsebeilagen", menu["vegetables"])
    section("Dessert", menu["desserts"])
    return "\n".join(lines)


def calendar_description(menu: dict, source_url: str) -> str:
    return (
        event_description(menu)
        + "\n\n\nWEITERE INFORMATIONEN\n─────────────────────\n"
        + "Öffnungszeiten: Mo–Fr 12:00–13:45 Uhr\n"
        + f"Telefon: {PHONE}\n"
        + f"E-Mail: {EMAIL}\n"
        + f"Web: {WEBSITE}\n"
        + f"Quelle: {source_url}"
    )


def html_description(menu: dict, source_url: str) -> str:
    sections = [
        ("Eintopf", menu["soups"]),
        ("Hauptgerichte", menu["mains"] + [
            {"text": "Heute zum Salat: " + item["text"], "price": item.get("price", "")}
            for item in menu["salads"]
        ]),
        ("Beilagen", menu["sides"]),
        ("Gemüsebeilagen", menu["vegetables"]),
        ("Dessert", menu["desserts"]),
    ]
    parts = ["<html><body>"]
    for title, items in sections:
        if not items:
            continue
        parts.append(f"<p><strong><u>{html.escape(title)}</u></strong><br>")
        parts.extend(f"• {html.escape(priced(item))}<br><br>" for item in items)
        parts.append("</p>")
    parts.append(
        "<br><p><strong><u>Weitere Informationen</u></strong><br>"
        "Öffnungszeiten: Mo–Fr 12:00–13:45 Uhr<br>"
        f'<a href="tel:+495415011064">Telefon: {PHONE}</a><br>'
        f'<a href="mailto:{EMAIL}">E-Mail: {EMAIL}</a><br>'
        f'<a href="https://{WEBSITE}">Web: {WEBSITE}</a><br>'
        f'<a href="{html.escape(source_url)}">PDF-Speiseplan</a></p>'
    )
    parts.append("</body></html>")
    return "".join(parts)


def render_overview(data: dict) -> str:
    lines = [f"Speiseplan {data['week']}", ""]
    for menu in data["menus"]:
        lines.append(f"{menu['day']}, {datetime.fromisoformat(menu['date']):%d.%m.%Y}")
        lines.append(event_description(menu))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_ics(all_weeks: list[dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//PRO mac-support//Landkreis Speiseplan//DE",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Landkreis Speiseplan",
        "X-WR-CALDESC:Wöchentlicher Speiseplan des Landkreis Restaurants Osnabrück",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
        "BEGIN:VTIMEZONE", "TZID:Europe/Berlin", "X-LIC-LOCATION:Europe/Berlin",
        "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST",
        "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
        "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET",
        "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for data in sorted(all_weeks, key=lambda item: item["week_start"]):
        for menu in data["menus"]:
            start = date.fromisoformat(menu["date"])
            names = [short_name(item["text"]) for item in menu["mains"][:2]]
            summary = "🍽 " + " · ".join(names)
            dtstamp = datetime.combine(date.fromisoformat(data["week_start"]), datetime.min.time(), tzinfo=timezone.utc)
            event = [
                "BEGIN:VEVENT",
                f"UID:landkreis-speiseplan-{start.isoformat()}@pro-mac-support.de",
                f"SEQUENCE:{EVENT_SEQUENCE}",
                f"DTSTAMP:{dtstamp:%Y%m%dT%H%M%SZ}",
                f"DTSTART;TZID=Europe/Berlin:{start:%Y%m%d}T120000",
                f"DTEND;TZID=Europe/Berlin:{start:%Y%m%d}T134500",
                f"SUMMARY:{escape_ics(summary)}",
                "DESCRIPTION:" + escape_ics(calendar_description(menu, data["source_url"])),
                f"X-ALT-DESC;FMTTYPE=text/html:{escape_ics(html_description(menu, data['source_url']))}",
                f"LOCATION:{escape_ics(LOCATION)}",
                f"URL:{data['source_url']}",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
            lines.extend(event)
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in fold_ics(line)]
    return "\r\n".join(folded) + "\r\n"


def validate_ics(content: str, expected_events: int) -> None:
    if not content.startswith("BEGIN:VCALENDAR\r\n") or not content.endswith("END:VCALENDAR\r\n"):
        raise RuntimeError("Ungültiger ICS-Rahmen")
    if content.count("BEGIN:VEVENT") != expected_events or content.count("END:VEVENT") != expected_events:
        raise RuntimeError("Falsche Anzahl Kalenderereignisse")
    for line in content.split("\r\n"):
        if len(line.encode("utf-8")) > 75:
            raise RuntimeError(f"ICS-Zeile länger als 75 Byte: {line[:40]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, help="Lokales PDF statt Website verwenden")
    parser.add_argument("--source-url", help="Quell-URL bei lokalem PDF")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    root = args.root.resolve()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "pdf").mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = "landkreis-speiseplan-calendar/1.0"
    source_url = args.source_url or (args.pdf.as_uri() if args.pdf else discover_pdf(session))

    if args.pdf:
        pdf_path = args.pdf.resolve()
    else:
        response = session.get(source_url, timeout=60)
        response.raise_for_status()
        temp_path = root / "Speiseplan.pdf"
        temp_path.write_bytes(response.content)
        pdf_path = temp_path

    data = parse_pdf(pdf_path, source_url)
    json_path = root / "data" / f"{data['week']}.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive_path = root / "pdf" / f"{data['week']}.pdf"
    if pdf_path != archive_path:
        archive_path.write_bytes(pdf_path.read_bytes())
    (root / "Speiseplan.pdf").write_bytes(pdf_path.read_bytes())
    (root / "speiseplan.txt").write_text(render_overview(data), encoding="utf-8")

    # Der abonnierbare Feed enthält ausschließlich die aktuelle Woche. Archivdaten
    # bleiben als JSON erhalten, dürfen aber keine veralteten Termine im Live-Feed erzeugen.
    ics = render_ics([data])
    validate_ics(ics, len(data["menus"]))
    (root / "speiseplan.ics").write_bytes(ics.encode("utf-8"))
    print(render_overview(data), end="")
    print(f"\nQuelle: {source_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        raise
