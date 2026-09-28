# MyFitnessPal-MCP-Server

Mit diesem Server kann Claude (Claude Desktop oder Claude Code) dein
MyFitnessPal-Tagebuch **lesen**: Mahlzeiten, Nährwerte, Gewicht und Training.

> **Hinweis:** Das ist ein inoffizielles Projekt. MyFitnessPal hat keine öffentliche
> API. Der Server nutzt die Community-Bibliothek
> [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal), die die
> Webseite ausliest. Wenn MyFitnessPal seine Seite ändert, kann es vorübergehend
> nicht funktionieren. Der Server **schreibt oder ändert nichts** in deinem Konto.

## Werkzeuge (Tools)

| Tool | Was es liefert |
|---|---|
| `get_diary(date)` | Mahlzeiten mit Einträgen (Name, Menge, Einheit, Kalorien, Makros), Tagessumme, Ziele, Rest bis zum Ziel, Wasser, Notizen |
| `get_nutrition_summary(start_date, end_date)` | Standard: letzte 7 Tage (max. 62). Durchschnitt pro geloggtem Tag, Durchschnittsziele, Makro-Verteilung in %, Tage im Kalorienziel, Werte pro Tag |
| `get_weight_history(start_date, end_date, measurement)` | Standard: Gewicht der letzten 90 Tage. Alle Werte plus Statistik (erster/letzter Wert, Änderung, Min, Max, Durchschnitt, Änderung pro Woche) |
| `get_exercises(date)` | Cardio und Krafttraining eines Tages |

Datumsangaben: `YYYY-MM-DD` (z. B. `2026-09-28`), `heute`/`today`, `gestern`/`yesterday`.

## Einrichtung

### 1. uv installieren

`uv` verwaltet Python und alle Bibliotheken automatisch. Im Terminal:

```bash
brew install uv
```

(Ohne Homebrew: `curl -LsSf https://astral.sh/uv/install.sh | sh`)

Danach einmal im Projektordner:

```bash
uv sync
```

### 2. Cookies exportieren (Anmeldung)

Der Server meldet sich mit den Cookies deines Browsers an – du gibst also nie dein
Passwort ein.

1. Im Browser (z. B. Chrome) auf <https://www.myfitnesspal.com> **anmelden**.
2. Die Browser-Erweiterung **„Get cookies.txt LOCALLY“** installieren.
3. Auf myfitnesspal.com das Erweiterungs-Symbol anklicken → **Export** (Format: Netscape).
4. Die Datei z. B. als `mfp-cookies.txt` speichern – an einem Ort, den du dir merkst.

> ⚠️ Die Cookie-Datei ist wie ein Passwort. Nicht weitergeben, nicht hochladen.
> Sie ist in `.gitignore` eingetragen und wird nie mit Git gespeichert.

**Alternative:** Statt einer Datei kann der Server die Cookies direkt aus dem Browser
lesen. Dazu `MFP_BROWSER` auf `chrome`, `firefox`, `safari`, `edge` oder `brave`
setzen (statt `MFP_COOKIE_FILE`). Das kann unter macOS nach dem Schlüsselbund-Passwort
fragen oder an Berechtigungen scheitern – die cookies.txt ist der zuverlässigere Weg.

### 3. In Claude Desktop eintragen

Datei öffnen: Claude Desktop → Einstellungen → Entwickler → „Konfiguration bearbeiten“
(`~/Library/Application Support/Claude/claude_desktop_config.json`).

Dort unter `mcpServers` eintragen (Pfade anpassen):

```json
{
  "mcpServers": {
    "myfitnesspal": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "run",
        "--directory",
        "/PFAD/ZU/MyFitnessPal MCP",
        "server.py"
      ],
      "env": {
        "MFP_COOKIE_FILE": "/PFAD/ZU/mfp-cookies.txt"
      }
    }
  }
}
```

- Den Pfad zu `uv` findest du mit `which uv` im Terminal.
- Claude Desktop danach **komplett beenden und neu starten**.

### Optional: Claude Code

```bash
claude mcp add myfitnesspal --scope user \
  -e MFP_COOKIE_FILE=/PFAD/ZU/mfp-cookies.txt \
  -- /opt/homebrew/bin/uv run --directory "/PFAD/ZU/MyFitnessPal MCP" server.py
```

## Beispiel-Fragen an Claude

- „Was habe ich heute gegessen und wie viele Kalorien habe ich noch übrig?“
- „Wie war meine Proteinzufuhr in den letzten 7 Tagen im Schnitt?“
- „An wie vielen Tagen im September war ich in meinem Kalorienziel?“
- „Wie hat sich mein Gewicht in den letzten 3 Monaten entwickelt?“
- „Welches Training habe ich gestern eingetragen?“
- „Vergleiche meine Makro-Verteilung dieser Woche mit meinen Zielen.“

## Fehlerbehebung

| Problem | Lösung |
|---|---|
| „Anmeldung ist abgelaufen“ / „neu anmelden“ | Im Browser bei myfitnesspal.com neu anmelden, cookies.txt neu exportieren (gleicher Dateiname), Claude Desktop neu starten. |
| „Cookie-Datei nicht gefunden“ | Pfad in `MFP_COOKIE_FILE` prüfen – vollständiger Pfad, ohne `~`-Abkürzung am sichersten. |
| „Keine Anmeldedaten konfiguriert“ | Im `env`-Block fehlt `MFP_COOKIE_FILE` (oder `MFP_BROWSER`). |
| Server erscheint nicht in Claude Desktop | JSON auf Tippfehler prüfen (Kommas!), Pfad zu `uv` prüfen, Claude Desktop vollständig beenden (⌘Q) und neu öffnen. Logs: `~/Library/Logs/Claude/mcp-server-myfitnesspal.log`. |
| „Measurement … does not exist“ | Die Messung muss genau so heißen wie in MyFitnessPal (Englisch, z. B. `Weight`, `Body Fat`). |
| Zusammenfassung dauert lange | Pro Tag ist eine Abfrage nötig. Tage werden 5 Minuten zwischengespeichert. |

## Entwicklung

```bash
uv run pytest            # Tests (ohne Login, MyFitnessPal wird simuliert)
uv run mcp dev server.py # MCP Inspector im Browser
```
