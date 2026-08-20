# Landkreis Speiseplan

Automatisch aktualisierter Wochenplan des Landkreis Restaurants Osnabrück.

- **Kalenderabo:** `webcal://bigzype.github.io/landkreis-speiseplan/speiseplan.ics`
- **ICS-Datei:** https://bigzype.github.io/landkreis-speiseplan/speiseplan.ics
- **Lesbare Wochenübersicht:** `speiseplan.txt`
- **Aktuelles Original:** `Speiseplan.pdf`
- **Archiv:** `data/` und `pdf/`

Die Aktualisierung läuft montags um 11 Uhr (Europe/Berlin). Der Workflow entdeckt den jeweils aktuellen PDF-Link direkt auf der Restaurant-Website, liest Hauptgerichte, Eintöpfe, Beilagen, Gemüsebeilagen, Salatangebot und Dessert aus und erstellt daraus ganztägige Kalendereinträge.
