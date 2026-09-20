# Publisher PDF regression fixture

`Speise39.2026.pdf` is the unmodified public PDF downloaded from
https://www.landkreis-restaurant.de/documents/281/Speise39.2026.pdf
on 2026-09-20.

SHA-256: `d6e1bcc6be3bc0cf7cdcebaad165ca97f4681107e9c83d570f18333cf8230188`

The visible heading and pdfplumber text both say:
`Speiseplan für den Zeitraum: 21: September bis zum 25.September 2026`.
The colon after `21` is printed in the source, not inferred from its filename.
The fixture exercises the real full menu parser offline; negative tests retain
invalid-date, conflicting-period and expected-week rejection.
