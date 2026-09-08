# Landkreis Speiseplan

Automatisch aktualisierter Wochenplan des Landkreis Restaurants Osnabrück.

- **Kalenderabo:** `webcal://bigzype.github.io/landkreis-speiseplan/speiseplan.ics`
- **ICS-Datei:** https://bigzype.github.io/landkreis-speiseplan/speiseplan.ics
- **Lesbare Wochenübersicht:** `speiseplan.txt`
- **Aktuelles Original:** `Speiseplan.pdf`
- **Archiv:** `data/` und `pdf/`

Der Hermes-Quellenmonitor startet den Workflow per `workflow_dispatch`, sobald der Plan für die aktuelle ISO-Woche auf der Restaurantseite verfügbar ist. Der Workflow liest Hauptgerichte, Eintöpfe, Beilagen, Gemüsebeilagen, Salatangebot und Dessert aus und erstellt Kalendereinträge für Mo–Fr, 12:00–13:45 Uhr (Europe/Berlin).

## Gemeinsame Quellenerkennung

`menu_source.py` benötigt ausschließlich die Python-Standardbibliothek (Python 3.9+ mit Zeitzonendaten für `Europe/Berlin`). Updater und lokaler Quellenmonitor können dieselbe Datei importieren:

- `menu_identity(url: str) -> tuple[int, int] | None`: validiertes `(ISO-Jahr, ISO-Woche)` aus dem URL-Basisdateinamen.
- `menu_candidates(page: str, base_url: str) -> list[str]`: deduplizierte tatsächliche Menü-Links gleicher Herkunft in Dokumentreihenfolge. Fremde Herkunft wird ausgeschlossen.
- `discover_menu_url(page: str, base_url: str, expected: date | None = None) -> str`: genau ein Link für die erwartete ISO-Woche; ohne Datum gilt heute in `Europe/Berlin`. Kein Treffer, mehrere unterschiedliche Treffer oder überschrittene Eingabegrenzen führen zu `ValueError`.

Akzeptiert werden `Speise37.2026.pdf` sowie ein optionaler Unterstrich, ein Leerzeichen oder ein Bindestrich vor der Woche. Query und Fragment bleiben erhalten; URLs werden aus HTML-Links aufgelöst, nicht aus Wochenzahlen konstruiert. Grenzen: 2.000.000 HTML-Zeichen, 10.000 Links, 4.096 URL-Zeichen. ISO-Jahr und Kalenderwoche werden auch am Jahreswechsel korrekt geprüft.

`update_menu.discover_pdf(session, expected=None)` verwendet diese Erkennung. `parse_pdf(pdf_path, source_url, expected=None)` prüft zusätzlich den gedruckten PDF-Zeitraum gegen die erwartete ISO-Woche (Standard ebenfalls heute in Berlin); Archivtests übergeben ihr historisches Datum ausdrücklich. Vor Änderungen an bestehenden Ausgaben werden Download, Woche, fünf Wochentage, Pflichtkategorien und erzeugter Kalender geprüft. Die Menüformatierung bleibt unverändert.

## Regressionstests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Die Tests benötigen kein Netzwerk, prüfen die aufbewahrten PDFs gegen die Parser-Ausgabe vor der Reparatur und die archivierten JSON-Inhalte und testen, dass fehlgeschlagene Downloads/Validierungen keine gültigen Ausgaben ersetzen. Bei KW35 wich die gespeicherte JSON bereits vor dieser Reparatur vom Parser ab; deshalb wird dort die unveränderte Parser-Ausgabe per SHA-256 geprüft, ohne Archivdaten umzuschreiben.
