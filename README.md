# claude-cleaner

Przenośny porządkowacz rozszerzeń Claude Code. Jeden plik, Python 3.8+, tylko biblioteka standardowa, działa na Windows / macOS / Linux.

## Co wykrywa

Wszystko, co zostało dołożone do Claude Code, niezależnie od tego, jak się nazywa:

| Kategoria | Skąd |
|---|---|
| Pluginy | `~/.claude/plugins/installed_plugins.json`, `plugins/cache/`, `plugins/data/`, `enabledPlugins` w `settings.json` |
| Marketplace'y pluginów | `plugins/known_marketplaces.json`, `plugins/marketplaces/`, `extraKnownMarketplaces` |
| Serwery MCP | `~/.claude.json` (globalne i per projekt), `settings.json`, `.mcp.json` w projektach |
| Skills, komendy, agenci, hooki (pliki), style | `~/.claude/skills`, `commands`, `agents`, `hooks`, `output-styles` |
| Hooki i statusLine | wpisy w `settings.json` / `settings.local.json` (globalne i projektowe) |
| Znane frameworki | GSD, gstack, SuperClaude, BMAD, spec-kit, CCPM, claude-flow, Serena, Agent OS, Task Master, OMC, claude-mem, Conductor. Ich części są grupowane w jeden „pakiet”, który usuwa się jednym zaznaczeniem |
| Nieznane elementy | każdy katalog/plik w `~/.claude`, który nie należy do samego Claude Code |
| Katalogi narzędzi w `$HOME` | np. `~/.gstack`, `~/.omc`, `~/.claude-flow` |
| Projekty | `.claude/` (commands, agents, skills, hooks, settings), `.mcp.json`, `CLAUDE.md`, `CLAUDE.local.md`, `.planning`, `.gstack`, `.specify`, `.bmad*`, `.serena` itd. |

Lokalizacja Claude Code jest wykrywana automatycznie (`CLAUDE_CONFIG_DIR` lub `~/.claude`). Nic nie trzeba wskazywać.

## Instalacja

Pobierz jeden plik i uruchom go Pythonem (3.8+):

```bash
curl -O https://raw.githubusercontent.com/mbuas/claude-cleaner/main/claude-cleaner.py
python claude-cleaner.py
```

Albo sklonuj repozytorium:

```bash
git clone https://github.com/mbuas/claude-cleaner.git
cd claude-cleaner
python claude-cleaner.py
```

## Uruchomienie

1. Skrypt skanuje konfigurację globalną.
2. Pyta o folder z projektami: **Enter** otwiera okno wyboru folderu, **w** pozwala wpisać ścieżkę, **s** pomija.
3. Pokazuje listę z checkboxami: **↑/↓** nawigacja, **spacja** zaznacz, **a** cała kategoria, **A** wszystko, **i** odwróć, **Enter** dalej, **q** wyjście.
4. Wyświetla podsumowanie operacji i prosi o potwierdzenie (`tak`).

## Bezpieczeństwo

- Pliki nie są kasowane. Są **przenoszone** do `~/.claude-cleaner-backups/<data>/` razem z `manifest.json`.
- Pliki JSON są kopiowane do tego samego folderu przed edycją.
- Cofnięcie: `python claude-cleaner.py --restore ~/.claude-cleaner-backups/<data>`
- Skrypt nigdy nie przeniesie samego `~/.claude`, `~/.claude.json`, katalogu domowego ani żadnego katalogu nadrzędnego wobec nich.
- Jeśli któraś operacja elementu się nie powiedzie, pozostałe operacje tego elementu są pomijane, żeby nie zostawić niespójnego stanu.
- Uszkodzone pliki JSON są pomijane i wyraźnie zgłaszane na liście jako nieprzeanalizowane.
- Kopia z innego komputera przy `--restore` wywołuje ostrzeżenie i pytanie o potwierdzenie.

## Flagi

| Flaga | Działanie |
|---|---|
| `--dry-run` | pokaż operacje, nic nie zmieniaj |
| `--list` | tylko wypisz wykryte elementy |
| `--list --json` | to samo jako JSON |
| `--projects <dir>` | folder projektów bez okna dialogowego |
| `--no-projects` | pomiń skan projektów |
| `--no-dialog` | zamiast okna: wpisanie ścieżki |
| `--select 1,3,5-7` / `--select all` | wybór bez interakcji |
| `--yes` | bez pytania o potwierdzenie |
| `--backup-dir <dir>` | własne miejsce na kopie |
| `--restore <dir>` | przywróć z kopii |
| `--home <dir>` | traktuj `<dir>` jako HOME (do testów na sztucznym katalogu) |
| `--no-color` | bez kolorów |

## Testy

```bash
python test_claude_cleaner.py -v
```

Testy budują własne, sztuczne środowisko w katalogu tymczasowym i uruchamiają skrypt z `--home`, więc nigdy nie dotykają prawdziwej instalacji Claude Code.
