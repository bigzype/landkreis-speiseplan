# Landkreis Speiseplan

Automatisch aktualisierter Wochenplan des Landkreis Restaurants Osnabrück.

- **Kalenderabo:** `speiseplan.ics`
- **Lesbare Wochenübersicht:** `speiseplan.txt`
- **Aktuelles Original:** `Speiseplan.pdf`
- **Archiv:** `data/` und `pdf/`

Die Aktualisierung läuft montags um 11 Uhr (Europe/Berlin). Der Workflow entdeckt den jeweils aktuellen PDF-Link direkt auf der Restaurant-Website, liest Hauptgerichte, Eintöpfe, Beilagen, Gemüsebeilagen, Salatangebot und Dessert aus und erstellt daraus ganztägige Kalendereinträge.
