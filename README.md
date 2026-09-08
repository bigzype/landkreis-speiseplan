# Landkreis Speiseplan

Automatisch aktualisierter Wochenplan des Landkreis Restaurants Osnabrück.

- **Kalenderabo:** `webcal://bigzype.github.io/landkreis-speiseplan/speiseplan.ics`
- **ICS-Datei:** https://bigzype.github.io/landkreis-speiseplan/speiseplan.ics
- **Lesbare Wochenübersicht:** `speiseplan.txt`
- **Aktuelles Original:** `Speiseplan.pdf`
- **Archiv:** `data/` und `pdf/`

Der Quellenmonitor kann den Workflow per `workflow_dispatch` starten, sobald ein neuer Plan erkannt wird: **Dateiname zuerst, PDF-Inhalt nur als Fallback.** Ein gegenüber der letzten erfolgreichen Quelle geänderter Basisdateiname genügt als Update-Signal, auch wenn die im Namen angegebene Woche alt ist. Das ist noch kein Nachweis gültiger PDF-Inhalte. Der echte Updater prüft anschließend Zeitraum und Gerichte vollständig, bevor er etwas veröffentlicht. Sonntag ist die **Zielwoche** die kommende Woche; Montag bis Samstag die laufende ISO-Woche. Der Workflow liest Hauptgerichte, Eintöpfe, Beilagen, Gemüsebeilagen, Salatangebot und Dessert aus und erstellt Kalendereinträge für Mo–Fr, 12:00–13:45 Uhr (Europe/Berlin).

Der externe Monitor prüft ausschließlich **Sonntag und Montag, 08–20 Uhr (Europe/Berlin)**, und nur bis zum Wochenerfolg; Dienstag ist kein Prüftag. Sobald die neue Wochenquelle erkannt und als `current` im Monitor-Cache gespeichert ist, erfolgen keine weiteren Quellenproben für diese Zielwoche. Nach erfolgreicher Veröffentlichung bleibt der Wochenstatus `COMPLETE` ohne Netzwerkzugriff. Cache, Zeitfenster, Stopp und Workflow-Auslösung verwaltet der externe Monitor, nicht diese zustandslose CLI. Als `--previous-source` dient die letzte erfolgreiche `source_url` aus dem Monitor-Health-Status oder dem passenden neuesten Archiv `data/YYYY-KWww.json` (keine `data/menu.json`); die Baseline bleibt über den Wochenwechsel erhalten.

## Gemeinsame Ziel- und Quellenerkennung

`menu_source.py` benötigt nur die Standardbibliothek (Python 3.9+ mit Zeitzonendaten für `Europe/Berlin`):

- `current_menu_date() -> date`: tatsächliches heutiges Datum in Berlin, **ohne** Sonntagsverschiebung.
- `target_menu_date(now: date | datetime | None = None) -> date`: Sonntag plus einen Tag, sonst unverändert. Aware Datetimes werden nach Berlin umgerechnet; naive Datetimes gelten ausdrücklich als Berliner Ortszeit, unabhängig von der Host-Zeitzone. `None` nutzt das tatsächliche Berliner Datum. ISO-Jahr und DST-Grenzen werden damit über das lokale Zieldatum bestimmt.
- `menu_identity(url) -> tuple[int, int] | None`: validiertes `(ISO-Jahr, ISO-Woche)` aus bekannten Dateinamen wie `Speise37.2026.pdf`, `Speise_37.2026.pdf`, `Speise 37.2026.pdf`, `Speise-37.2026.pdf`. Ohne Baseline als Monitoring-Signal nutzbar, niemals Nachweis geprüfter PDF-Inhalte.
- `menu_candidates(page, base_url) -> list[str]`: höchstens vier deduplizierte tatsächliche, gleichursprüngliche PDF-Links. Zulässig: im Bereich `#speiseplan` oder mit Speiseplan-/Menü-Bezeichnung in Linktext, Titel, ARIA-Label, Bild-Alttext oder Dateinamen. Catering-Bereiche/-Zwischenüberschriften und entsprechend benannte Angebote werden ausgeschlossen; insbesondere wird der Catering-Link innerhalb des echten `#speiseplan`-Bereichs nicht geladen. Ungewöhnliche/generische Dateinamen sind zulässig. Mehr als vier Kandidaten ist ein Fehler, keine Kürzung.
- `discover_menu_url(page, base_url, expected=None)`: historischer **dateinamenbasierter** Selektor; bleibt für Kompatibilität verfügbar, wird aber nicht für Veröffentlichung oder Probe-Frischenachweis verwendet. Standarddatum ist das Zieldatum.

URLs entstehen ausschließlich durch Auflösen tatsächlicher HTML-`href`s (relative Links eingeschlossen). Query und Fragment bleiben erhalten. Kein Erraten von Dokumentnummern, Kalenderwochen-URLs oder weiteren Seiten; ein fremder `<base>`-Tag wird ignoriert. Fremde Herkunft, Credentials und gefährliche URL-Zeichen werden ausgeschlossen. Grenzen: 2.000.000 HTML-Zeichen, 10.000 Links, 4.096 URL-Zeichen.

`menu_fetch.probe_menu(session, expected=None)` bleibt der vollständige Netzwerk-/PDF-Pfad für den echten Updater **und** die CLI ohne `--filename-first`. Er liefert `ProbeResult(report, data, pdf_bytes)`; in diesem vollständigen Modus enthält nur `current` Daten und geprüfte Originalbytes. Technische Fehler werfen `ProbeError(reason, detail)`.

`menu_fetch.probe_menu_filename_first(session, expected=None, base_url=BASE_URL, previous_source=None)` verwendet dieselbe begrenzte Index-Abfrage, Kandidatenerkennung und Netzwerklogik:

1. **Genau ein sicherer Kandidat + Baseline:** Der percent-dekodierte Basisdateiname wird mit dem der letzten erfolgreichen Quelle verglichen. Ist er anders, folgt `current` / `FILENAME_CHANGED` ohne PDF-Download oder Parser, unabhängig von der Dateinamen-Woche. Verzeichnis-, Query- oder Fragmentänderungen allein zählen nicht als Dateinamenwechsel.
2. **Genau ein sicherer Kandidat, keine Baseline:** Entspricht der bekannte Dateiname der erwarteten ISO-Woche einschließlich ISO-Jahr, folgt `current` / `FILENAME_WEEK`, ebenfalls ohne PDF-Zugriff.
3. **Gleicher Name, generischer/falscher Name ohne Baseline oder mehrere Kandidaten:** Vollständiger PDF-Fallback mit bereits gelesener Indexseite und gemeinsamem Zeitbudget; keine zweite Index-Abfrage, kein willkürlich ausgewählter Link. Mehrdeutige tatsächliche PDF-Wochen und überschrittene Kandidatenlimits bleiben Fehler.

Im reinen Dateinamenpfad bleiben `data` und `pdf_bytes` leer (`None`), und `pdf_week` fehlt. `--previous-source` wird niemals heruntergeladen; die URL muss ein absoluter PDF-Link desselben Ursprungs sein. Ungültige Ursprünge, Credentials und rohe sowie percent-dekodierte Steuerzeichen werden auch für die Baseline abgelehnt. Die bestehenden Catering-/Fremdlink-Ausschlüsse gelten in beiden Modi unverändert.

Im vollständigen Modus und im PDF-Fallback wird jeder sinnvolle Kandidat vollständig heruntergeladen und durch denselben `update_menu.parse_pdf`-Parser geprüft: tatsächlicher gedruckter Zeitraum einschließlich Ende, genau eine Menütabelle, Mo–Fr und alle Pflichtkategorien. Erst nach Prüfung **aller** Kandidaten wird die Zielwoche gewählt. Der PDF-Zeitraum ist maßgeblich, auch bei falscher Dateinamen-Woche. Mehrere URLs für dieselbe tatsächliche PDF-Woche sind mehrdeutig, selbst bei identischen Bytes; unterschiedliche gültige Wochen dürfen nebeneinander stehen. Ein kaputter oder widersprüchlicher Kandidat blockiert auch einen anderen gültigen Treffer.

Netzwerkgrenzen: 2.000.000 HTML-Bytes, 8.000.000 PDF-Bytes (jeweils gelesene/dekodierte Bytes), höchstens drei explizite Weiterleitungen pro Download, ausschließlich innerhalb desselben Ursprungs (Schema/Host/Port). Verbindungs-Timeout maximal 5 s, Lese-Timeout maximal 10 s, gemeinsames 75-s-Netzwerkbudget an Request-/Chunk-Grenzen; der Monitor setzt zusätzlich ein hartes 120-s-Subprozesslimit. PDFs dürfen höchstens acht Seiten haben. Größenlimits gelten auch ohne `Content-Length`.

`update_menu.discover_pdf(session, expected=None)` ist ein Kompatibilitätswrapper um die vollständige Probe. `update_menu.main()` nutzt deren geprüfte Daten und Bytes direkt, lädt den ausgewählten Link nicht erneut und erfasst das **Zieldatum einmal**. `parse_pdf(..., expected=None)` behält als direkte Parser-Voreinstellung das tatsächliche Berliner Datum; Aufrufer übergeben Ziel-/Archivdatum ausdrücklich. Ausschließlich die gemeinsame Probe setzt intern `verify_expected=False`, um auch veraltete Kandidaten vollständig zu validieren und als `waiting` einordnen zu können.

Vor Änderungen an bestehenden Ausgaben müssen alle Quellenprüfungen, das Zieldatum, sämtliche Menükategorien und der erzeugte Kalender gültig sein. Bei `waiting` oder Fehlern beendet sich der Updater ohne Schreibzugriff auf Last-good-Ausgaben. Menüformatierung und Inhalte bleiben unverändert. `--source-url` ist nur zusammen mit `--pdf` erlaubt; lokale PDFs werden ebenfalls gegen das Zieldatum geprüft.

## Read-only-Probe: exaktes JSON-Protokoll

Im installierten Monitor den absoluten Interpreter verwenden (keine Abhängigkeit vom Arbeitsverzeichnis/PATH):

```sh
/Users/osiris/Desktop/Projekte/landkreis-speiseplan/.venv/bin/python \
  /Users/osiris/Desktop/Projekte/landkreis-speiseplan/menu_probe.py \
  --filename-first --expected 2026-09-08 \
  --previous-source 'https://www.landkreis-restaurant.de/documents/278/Speise36.2026.pdf'
```

`menu_probe.py` ist ausführbar. Ohne `--filename-first` bleibt die vollständige PDF-Probe Standard. `--previous-source URL` ist optional und nur zusammen mit `--filename-first` zulässig. Ohne `--expected` gilt `target_menu_date()`. Explizite Daten müssen exakt `YYYY-MM-DD` und kalendergültig sein; ungültige Argumente werden vor Netzwerkzugriff abgelehnt. Ein ausdrücklich übergebenes Sonntagsdatum wird **nicht erneut verschoben**: `--expected` bezeichnet bereits das Ziel.

Stdout enthält genau **ein JSON-Objekt**, keine Menütexte/Logs (ausgenommen `--help`). Diagnostik von Abhängigkeiten kann auf stderr erscheinen. Die Probe schreibt keine Menü-, Archiv- oder Zustandsdateien.

| Feld | Typ / Bedeutung | Präsenz |
|---|---|---|
| `status` | `"current"`, `"waiting"` oder `"error"` | immer |
| `expected_date` | gültiger ISO-Datumsstring `YYYY-MM-DD` | immer |
| `source_url` | tatsächlicher absoluter Website-Link, keine konstruierte URL | bei `current` immer; bei `waiting` nur mit genau einem Kandidaten |
| `actual` | percent-dekodierter **Basisdateiname** des `source_url`-Pfads (kein Datum, keine Query/Fragment) | zusammen mit `source_url` |
| `pdf_week` | tatsächliche PDF-ISO-Woche als `YYYY-KWww` | zusammen mit `source_url` nach PDF-Prüfung; **fehlt bei Dateinamen-Erkennung** |
| `detected_by` | `"filename"`: nur Dateiname geprüft, nicht das PDF | ausschließlich bei Dateinamen-Erkennung |
| `reason` | stabiler `UPPER_SNAKE_CASE`-Code, siehe unten | bei `waiting`/`error` und dateinamenbasiertem `current`; fehlt bei PDF-geprüftem `current` |
| `detail` | menschliche Diagnose, nicht stabil; **nicht für Status-Hashes verwenden** | bei `waiting`/`error` |

Bei `INVALID_ARGUMENT` bleibt `expected_date` das gültige Standard-Zieldatum; der ungültige Eingabestring wird niemals als geprüftes Datum ausgegeben und es gibt kein `current`.

Exitcode **0**: `current` oder `waiting`. Exitcode **1**: `error`.

- `current` / `FILENAME_CHANGED` oder `FILENAME_WEEK`, `detected_by: "filename"`: Update-Signal aus dem Dateinamen; `pdf_week` fehlt ausdrücklich. Keine Behauptung validierter Veröffentlichung. Der echte Updater muss die Inhalte noch prüfen.
- `current` ohne `detected_by`/`reason`: Genau ein vollständig geprüfter Plan für die erwartete ISO-Woche; `pdf_week` muss zur Woche von `expected_date` passen.
- `waiting` / `PDF_WEEK_MISMATCH`: Mindestens ein vollständig gültiger Plan, aber keiner für die Zielwoche. Bei mehreren unterschiedlichen Wochen fehlen die optionalen Quellenfelder, statt einen willkürlichen Kandidaten als aktuell zu melden.
- `error`-Codes: `INVALID_ARGUMENT`, `NO_MENU_LINK`, `SOURCE_INVALID` (z. B. Kandidatenlimit, HTML/URL), `UNSAFE_REDIRECT`, `REDIRECT_LIMIT`, `SIZE_LIMIT`, `HTTP_ERROR` (unerwarteter nicht-200-Erfolgsstatus), `NETWORK_TIMEOUT`, `NETWORK_ERROR` (inklusive HTTP-4xx/5xx), `PDF_INVALID`, `AMBIGUOUS_PDF_WEEK`, `INTERNAL_ERROR`.

Beispiel des Dateinamen-Protokolls (kein PDF-Nachweis):

```json
{"actual":"Speise_37.2026.pdf","detected_by":"filename","expected_date":"2026-09-08","reason":"FILENAME_CHANGED","source_url":"https://www.landkreis-restaurant.de/documents/279/Speise_37.2026.pdf","status":"current"}
```

Beispiel eines vollständig PDF-verifizierten Ergebnisses:

```json
{"actual":"Speise_37.2026.pdf","expected_date":"2026-09-08","pdf_week":"2026-KW37","source_url":"https://www.landkreis-restaurant.de/documents/279/Speise_37.2026.pdf","status":"current"}
```

## Regressionstests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Die bisherigen 41 Regressionstests bleiben erhalten; zwölf neue Dateinamen-Tests prüfen insbesondere nachweislich ausbleibende PDF-Downloads/Parser-Aufrufe, geänderte Namen trotz alter Dateinamen-Woche, ISO-Wochen-Bootstrap, gleichen Namen mit echtem Inhalts-Fallback, generische Namen ohne Baseline, Mehrdeutigkeit, Baseline-/URL-Sicherheit, Fehler und unveränderte Ausgaben.

Die ursprünglichen 19 Regressionstests bleiben erhalten (Netzwerk-Mocks an den gemeinsamen PDF-Pfad angepasst). Ergänzungen prüfen Sonntag/ISO-Jahreswechsel/DST, generische Namen, gleichbleibende Namen mit neuen Inhalten, geänderte Namen mit alten Inhalten, falsche Dateinamen-Woche mit aktuellem Inhalt, sämtliche Kandidaten, technische Fehler, widersprüchliche Zeiträume, Netzwerkgrenzen, Catering-Ausschluss, CLI-Protokoll und echten Updater-Pfad in temporären Ausgabeverzeichnissen.

Archivtests prüfen alle aufbewahrten PDFs gegen die Parser-Ausgabe vor der Reparatur und die archivierten JSON-Inhalte. Bei KW35 wich die gespeicherte JSON bereits vorher vom Parser ab; dort wird die unveränderte Parser-Ausgabe per SHA-256 geprüft, ohne Archivdaten umzuschreiben. Fehlgeschlagene Quellen-/Kalenderprüfungen dürfen keine gültigen Ausgaben ersetzen.
