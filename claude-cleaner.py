#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude-cleaner — przenośny porządkowacz rozszerzeń Claude Code.

Wykrywa wszystko, co zostało dołożone do Claude Code (pluginy, marketplace'y,
serwery MCP, skills, komendy, agentów, hooki, style, status line, znane
frameworki typu GSD / gstack / SuperClaude / BMAD / spec-kit / claude-flow
oraz nieznane katalogi), a także serwery MCP i rozszerzenia aplikacji
Claude Desktop (%APPDATA%\\Claude, kopia MSIX w Packages\\Claude_*\\LocalCache,
~/Library/Application Support/Claude, ~/.config/Claude), skanuje folder
z projektami i pozwala wybrać, co usunąć. Potwierdzenie: Enter dwa razy. Nic nie jest kasowane bezpowrotnie: pliki lądują w kopii
zapasowej z manifestem, a edytowane pliki JSON są wcześniej kopiowane.

Wymagania: Python 3.8+ (tylko biblioteka standardowa). Windows / macOS / Linux.

Użycie:
    python claude-cleaner.py                 # tryb interaktywny
    python claude-cleaner.py --dry-run       # pokaż, nic nie zmieniaj
    python claude-cleaner.py --list          # tylko wypisz wykryte elementy
    python claude-cleaner.py --list --json   # to samo w JSON
    python claude-cleaner.py --projects D:\\projekty      # bez okna dialogowego
    python claude-cleaner.py --no-projects               # pomiń skan projektów
    python claude-cleaner.py --select 1,4,7 --yes        # bez interakcji
    python claude-cleaner.py --restore <folder_kopii>    # cofnij usunięcie
    python claude-cleaner.py --home <katalog>            # sztuczny HOME (testy)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Terminal / kolory
# ---------------------------------------------------------------------------


class C:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    UNDER = "\x1b[4m"
    INV = "\x1b[7m"
    # Paleta w stylu Claude Code (koral/pomarańcz) + Gemini CLI (niebieski/fiolet)
    ACCENT = "\x1b[38;5;209m"   # koral Claude
    BLUE = "\x1b[38;5;75m"
    PURPLE = "\x1b[38;5;141m"
    GREEN = "\x1b[38;5;114m"
    YELLOW = "\x1b[38;5;221m"
    RED = "\x1b[38;5;203m"
    GRAY = "\x1b[38;5;245m"
    WHITE = "\x1b[97m"


NO_COLOR = False


def col(code: str, text: str) -> str:
    if NO_COLOR:
        return text
    return f"{code}{text}{C.RESET}"


def enable_vt() -> None:
    """Włącza sekwencje ANSI i UTF-8 w konsoli Windows."""
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            for handle_id in (-11, -12):
                h = k32.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if k32.GetConsoleMode(h, ctypes.byref(mode)):
                    k32.SetConsoleMode(h, mode.value | 0x0004)
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def term_size() -> Tuple[int, int]:
    try:
        s = shutil.get_terminal_size((100, 30))
        return max(60, s.columns), max(12, s.lines)
    except Exception:
        return 100, 30


def is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def read_key() -> str:
    """Zwraca nazwę klawisza: up/down/left/right/space/enter/esc/pgup/pgdn/home/end lub znak."""
    if os.name == "nt":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            ch2 = msvcrt.getwch()
            return {
                "H": "up", "P": "down", "K": "left", "M": "right",
                "I": "pgup", "Q": "pgdn", "G": "home", "O": "end",
            }.get(ch2, "")
        if ch == "\r":
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1b":
            return "esc"
        return ch
    else:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    ch2 = sys.stdin.read(1)
                    if ch2 in ("[", "O"):
                        ch3 = sys.stdin.read(1)
                        if ch3.isdigit():
                            # sekwencje typu ESC [ 5 ~
                            ch4 = sys.stdin.read(1)
                            return {"5": "pgup", "6": "pgdn", "1": "home", "4": "end", "7": "home", "8": "end"}.get(ch3, "")
                        return {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}.get(ch3, "")
                return "esc"
            if ch in ("\r", "\n"):
                return "enter"
            if ch == " ":
                return "space"
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# Model danych
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """Jedna fizyczna operacja."""

    kind: str                     # rm_path | json_del | json_list_remove | json_hook_remove
    path: Path
    keys: List[str] = field(default_factory=list)
    value: str = ""
    desc: str = ""

    def describe(self) -> str:
        if self.kind == "rm_path":
            return f"przenieś do kopii: {self.path}"
        if self.kind == "json_del":
            return f"usuń klucz {'.'.join(self.keys)} z {self.path.name}"
        if self.kind == "json_list_remove":
            return f"usuń '{self.value}' z listy {'.'.join(self.keys)} w {self.path.name}"
        if self.kind == "json_hook_remove":
            return f"usuń hook [{self.keys[0]}] '{short(self.value, 50)}' z {self.path.name}"
        return self.kind


@dataclass
class Item:
    category: str
    name: str
    detail: str = ""
    actions: List[Action] = field(default_factory=list)
    tool: str = ""            # nazwa wykrytego frameworka (grupowanie)
    selected: bool = False
    parts: List[str] = field(default_factory=list)
    index: int = 0

    def all_paths(self) -> List[Path]:
        return [a.path for a in self.actions if a.kind == "rm_path"]


def short(s: str, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Wykrywanie lokalizacji Claude Code
# ---------------------------------------------------------------------------


@dataclass
class Env:
    user_home: Path
    claude_dir: Path
    claude_json: Path
    claude_bin: str = ""
    desktop_dirs: List[Path] = field(default_factory=list)   # dane aplikacji Claude Desktop

    def rel(self, p: Path) -> str:
        try:
            return "~/.claude/" + p.relative_to(self.claude_dir).as_posix()
        except ValueError:
            pass
        for i, d in enumerate(self.desktop_dirs):
            try:
                tag = "Claude Desktop" + (f"#{i + 1}" if len(self.desktop_dirs) > 1 else "")
                return f"[{tag}]/" + p.relative_to(d).as_posix()
            except ValueError:
                pass
        try:
            return "~/" + p.relative_to(self.user_home).as_posix()
        except ValueError:
            return str(p)


def find_env(home_override: Optional[str]) -> Env:
    if home_override:
        user_home = Path(home_override).expanduser().resolve()
        claude_dir = user_home / ".claude"
        claude_json = user_home / ".claude.json"
    else:
        user_home = Path.home()
        cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        if cfg:
            claude_dir = Path(cfg).expanduser().resolve()
            if not claude_dir.is_dir():
                print(col(C.YELLOW, f"  ! CLAUDE_CONFIG_DIR wskazuje nieistniejący katalog: {claude_dir}"))
            claude_json = claude_dir / ".claude.json"
            if not claude_json.exists() and (user_home / ".claude.json").exists():
                claude_json = user_home / ".claude.json"
        else:
            claude_dir = user_home / ".claude"
            claude_json = user_home / ".claude.json"
    return Env(user_home, claude_dir, claude_json, shutil.which("claude") or "",
               find_desktop_dirs(user_home, use_env=not home_override))


def find_desktop_dirs(user_home: Path, use_env: bool) -> List[Path]:
    """Katalogi danych aplikacji Claude Desktop (osobnej od Claude Code).

    Windows: %APPDATA%\\Claude oraz kopia MSIX
             %LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude
    macOS:   ~/Library/Application Support/Claude
    Linux:   ~/.config/Claude
    Przy --home wszystko jest szukane względem wskazanego katalogu (testy).
    """
    cands: List[Path] = []
    if use_env and os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            cands.append(Path(appdata) / "Claude")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            cands.extend(sorted((Path(local) / "Packages").glob("Claude_*/LocalCache/Roaming/Claude")))
    cands.append(user_home / "AppData" / "Roaming" / "Claude")
    cands.extend(sorted((user_home / "AppData" / "Local" / "Packages").glob("Claude_*/LocalCache/Roaming/Claude")))
    cands.append(user_home / "Library" / "Application Support" / "Claude")
    cands.append(user_home / ".config" / "Claude")
    out: List[Path] = []
    for c in cands:
        try:
            if not c.is_dir():
                continue
            # ten sam katalog pod dwiema nazwami (dowiązanie / przekierowanie) liczy się raz
            if any(os.path.samefile(str(c), str(o)) for o in out):
                continue
        except OSError:
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Pomocnicze: JSON
# ---------------------------------------------------------------------------


BAD_JSON: List[Path] = []


def load_json(p: Path) -> Optional[dict]:
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return None
    except Exception:
        if p not in BAD_JSON:
            BAD_JSON.append(p)
        return None


def save_json(p: Path, data: dict) -> None:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def dir_size(p: Path) -> int:
    total = 0
    try:
        if p.is_file():
            return p.stat().st_size
        for root, _dirs, files in os.walk(p):
            for fn in files:
                try:
                    total += (Path(root) / fn).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Sygnatury znanych narzędzi / frameworków
# ---------------------------------------------------------------------------
# Każdy wpis: nazwa, wzorce ścieżek względem ~/.claude (posix, case-insensitive),
# wzorce katalogów w $HOME, słowa-klucze w komendach hooków, wzorce w projektach.

FRAMEWORKS: List[dict] = [
    {
        "name": "GSD (Get Shit Done)",
        "claude": ["get-shit-done", "get-shit-done/**", "commands/gsd", "commands/gsd/**", "commands/gsd-*",
                   "agents/gsd-*", "hooks/gsd-*", "gsd-*"],
        "home": [".gsd"],
        "hook": ["gsd-", "get-shit-done"],
        "project": [".planning", ".gsd"],
    },
    {
        "name": "gstack",
        "claude": ["skills/gstack", "skills/gstack/**", "gstack", "gstack/**", "commands/gstack*", "agents/gstack*"],
        "home": [".gstack"],
        "hook": ["gstack", "timeline-stop-hook"],
        "project": [".gstack"],
    },
    {
        "name": "SuperClaude",
        "claude": ["SuperClaude", "SuperClaude/**", "superclaude", "commands/sc", "commands/sc/**",
                   ".superclaude-metadata.json", "superclaude-metadata.json", "*SuperClaude*"],
        "home": [".superclaude"],
        "hook": ["superclaude"],
        "project": [".superclaude"],
    },
    {
        "name": "BMAD Method",
        "claude": ["bmad*", "commands/bmad*", "commands/BMad*", "agents/bmad-*", "agents/BMad*", "commands/bmad-*/**"],
        "home": [".bmad"],
        "hook": ["bmad"],
        "project": [".bmad-core", ".bmad", "_bmad", "bmad", ".bmad-*"],
    },
    {
        "name": "spec-kit (GitHub)",
        "claude": ["commands/speckit*", "commands/speckit.*", "commands/specify*"],
        "home": [],
        "hook": ["speckit", "specify"],
        "project": [".specify"],
    },
    {
        "name": "CCPM (Claude Code PM)",
        "claude": ["commands/pm", "commands/pm/**", "ccpm", "ccpm/**", "agents/parallel-worker.md", "agents/code-analyzer.md"],
        "home": [],
        "hook": ["ccpm"],
        "project": [".claude/ccpm", ".claude/epics", ".claude/prds"],
    },
    {
        "name": "claude-flow / ruv-swarm",
        "claude": ["claude-flow*", "commands/claude-flow*", "commands/swarm*", "commands/hive-mind*", "agents/swarm*",
                   "commands/sparc*", "agents/hive-mind*"],
        "home": [".claude-flow", ".swarm", ".hive-mind", ".ruv-swarm"],
        "hook": ["claude-flow", "ruv-swarm", "hive-mind"],
        "project": [".claude-flow", ".swarm", ".hive-mind", ".roo", "claude-flow.config.json", ".ruv-swarm"],
    },
    {
        "name": "Serena (MCP)",
        "claude": ["serena*"],
        "home": [".serena"],
        "hook": ["serena"],
        "project": [".serena"],
    },
    {
        "name": "Agent OS",
        "claude": ["commands/plan-product.md", "commands/create-spec.md", "commands/execute-tasks.md", "commands/analyze-product.md",
                   "agents/context-fetcher.md", "agents/file-creator.md", "agents/git-workflow.md", "agents/test-runner.md"],
        "home": [".agent-os"],
        "hook": ["agent-os"],
        "project": [".agent-os"],
    },
    {
        "name": "Task Master AI",
        "claude": ["commands/tm", "commands/tm/**", "commands/taskmaster*", "agents/task-*"],
        "home": [".taskmaster"],
        "hook": ["taskmaster", "task-master"],
        "project": [".taskmaster", ".taskmasterconfig"],
    },
    {
        "name": "oh-my-claudecode (OMC)",
        "claude": ["omc*", "commands/omc*", "agents/omc*"],
        "home": [".omc"],
        "hook": ["oh-my-claudecode", "omc"],
        "project": [".omc"],
    },
    {
        "name": "claude-mem",
        "claude": ["claude-mem*", "plugins/*/claude-mem*"],
        "home": [".claude-mem"],
        "hook": ["claude-mem"],
        "project": [],
    },
    {
        "name": "Conductor",
        "claude": ["commands/conductor*"],
        "home": [],
        "hook": ["conductor"],
        "project": ["conductor", ".conductor"],
    },
    {
        "name": "Cursor / inne IDE (nie Claude Code)",
        "claude": [],
        "home": [],
        "hook": [],
        "project": [],
    },
]

# Elementy w ~/.claude, które należą do samego Claude Code i NIE są rozszerzeniami.
CORE_ENTRIES = {
    "projects", "sessions", "session-env", "shell-snapshots", "todos", "statsig", "cache",
    "telemetry", "backups", "downloads", "chrome", "ide", "debug", "logs", "history.jsonl",
    "settings.json", "settings.local.json", ".credentials.json", "credentials.json",
    ".last-cleanup", ".last-update-result.json", "mcp-needs-auth-cache.json", "keybindings.json",
    "file-history", "plans", "tasks", "paste-cache", "memory", "local", "bin", "versions",
    "stats-cache.json", "usage", "cached-", "plugins", "skills", "commands", "agents", "hooks",
    "output-styles", "CLAUDE.md", "CLAUDE.local.md", "teams", "worktrees", "scheduled-tasks",
    "routines", "artifacts", "code_reviews", "auto-mode", "remote", "web-sessions",
}

CAT_DESKTOP_MCP = "Serwery MCP (Claude Desktop)"
CAT_DESKTOP_EXT = "Rozszerzenia Claude Desktop"

# Katalogi rozszerzeń zarządzane pozycja po pozycji.
EXT_DIRS = {
    "skills": "Skills",
    "commands": "Komendy (slash)",
    "agents": "Agenci",
    "hooks": "Hooki (pliki)",
    "output-styles": "Style odpowiedzi",
}

PROJECT_MARKERS = [
    # (ścieżka względna od folderu projektu, opis)
    (".mcp.json", "serwery MCP projektu"),
    (".claude/settings.json", "ustawienia projektu"),
    (".claude/settings.local.json", "ustawienia lokalne projektu"),
    (".claude/commands", "komendy projektu"),
    (".claude/agents", "agenci projektu"),
    (".claude/skills", "skills projektu"),
    (".claude/hooks", "hooki projektu"),
    (".claude/output-styles", "style projektu"),
    (".claude/ccpm", "CCPM"),
    (".claude/epics", "CCPM epics"),
    (".claude/prds", "CCPM PRDs"),
    ("CLAUDE.md", "instrukcje projektu"),
    ("CLAUDE.local.md", "instrukcje lokalne projektu"),
]

SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", "venv", ".venv", "env", "__pycache__", "dist", "build",
    ".next", ".nuxt", "target", ".tox", ".mypy_cache", ".pytest_cache", ".cache", "vendor", "site-packages",
    ".idea", ".vscode", "coverage", ".gradle", "Pods", "DerivedData", "bin", "obj",
}

MAX_DEPTH = 8


# ---------------------------------------------------------------------------
# Detektor
# ---------------------------------------------------------------------------


class Detector:
    def __init__(self, env: Env):
        self.env = env
        self.items: List[Item] = []
        self.notes: List[str] = []

    # --- narzędzia ---
    def add(self, item: Item) -> Item:
        self.items.append(item)
        return item

    def match_framework(self, rel_posix: str, hook_cmd: str = "") -> str:
        r = rel_posix.lower()
        h = hook_cmd.lower()
        for fw in FRAMEWORKS:
            for pat in fw["claude"]:
                if fnmatch.fnmatch(r, pat.lower()) or fnmatch.fnmatch(r, pat.lower() + "/**"):
                    return fw["name"]
            if h:
                for kw in fw["hook"]:
                    if kw in h:
                        return fw["name"]
        return ""

    def match_home_framework(self, name: str) -> str:
        n = name.lower()
        for fw in FRAMEWORKS:
            for pat in fw["home"]:
                if fnmatch.fnmatch(n, pat.lower()):
                    return fw["name"]
        return ""

    def match_project_framework(self, rel_posix: str) -> str:
        r = rel_posix.lower()
        for fw in FRAMEWORKS:
            for pat in fw["project"]:
                if fnmatch.fnmatch(r, pat.lower()):
                    return fw["name"]
        return ""

    # --- globalne ---
    def run_global(self) -> None:
        env = self.env
        if env.claude_dir.is_dir() or env.claude_json.exists():
            self.detect_plugins()
            self.detect_marketplaces()
            self.detect_mcp_global()
            self.detect_ext_dirs()
            self.detect_settings_bits()
            self.detect_unknown_entries()
            self.detect_home_companions()
        else:
            self.notes.append("Nie znaleziono katalogu Claude Code (~/.claude ani ~/.claude.json).")
        self.detect_desktop_app()

    # --- aplikacja Claude Desktop ---
    def detect_desktop_app(self) -> None:
        """Serwery MCP z claude_desktop_config.json i katalog 'Claude Extensions' aplikacji Claude Desktop.

        Na Windows te same dane bywają w dwóch miejscach (%APPDATA%\\Claude i kopia MSIX w
        Packages\\Claude_*\\LocalCache\\Roaming\\Claude) — taki sam wpis jest łączony w jeden element,
        którego usunięcie czyści oba pliki."""
        env = self.env
        mcp_items: Dict[str, Item] = {}
        ext_items: Dict[str, Item] = {}
        for d in env.desktop_dirs:
            cfg_f = d / "claude_desktop_config.json"
            cfg = load_json(cfg_f) or {}
            mcps = cfg.get("mcpServers", {}) if isinstance(cfg.get("mcpServers"), dict) else {}
            for name, scfg in mcps.items():
                item = mcp_items.get(name)
                if item is None:
                    item = mcp_items[name] = self.add(Item(CAT_DESKTOP_MCP, name, tool=self.match_framework(name)))
                    item.detail = self._mcp_desc(scfg)
                item.actions.append(Action("json_del", cfg_f, ["mcpServers", name]))
                item.parts.append(f"wpis mcpServers w {env.rel(cfg_f)}")
            ext_dir = d / "Claude Extensions"
            if not ext_dir.is_dir():
                continue
            try:
                entries = sorted(ext_dir.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or entry.name.endswith((".lock", ".log", ".tmp")):
                    continue
                key = entry.name.lower()
                item = ext_items.get(key)
                if item is None:
                    item = ext_items[key] = self.add(Item(CAT_DESKTOP_EXT, entry.name, tool=self.match_framework(entry.name)))
                    item.detail = self._desktop_ext_desc(entry)
                item.actions.append(Action("rm_path", entry))
                item.parts.append(env.rel(entry))
                # ustawienia rozszerzenia (jeśli aplikacja trzyma je obok, plik o tej samej nazwie)
                sdir = d / "Claude Extensions Settings"
                if sdir.is_dir():
                    for sf in sorted(sdir.glob(entry.name + ".*")):
                        if sf.is_file():
                            item.actions.append(Action("rm_path", sf))
                            item.parts.append(env.rel(sf))

    @staticmethod
    def _desktop_ext_desc(entry: Path) -> str:
        if not entry.is_dir():
            return f"plik, {human(dir_size(entry))}"
        m = load_json(entry / "manifest.json") or {}
        bits = []
        disp = m.get("display_name") or m.get("name")
        if disp and str(disp) != entry.name:
            bits.append(str(disp))
        if m.get("version"):
            bits.append(f"v{m['version']}")
        if m.get("description"):
            bits.append(short(str(m["description"]).strip().splitlines()[0], 60))
        bits.append(human(dir_size(entry)))
        return " · ".join(bits)

    def detect_plugins(self) -> None:
        env = self.env
        pdir = env.claude_dir / "plugins"
        installed_f = pdir / "installed_plugins.json"
        installed = load_json(installed_f) or {}
        plugins = installed.get("plugins", {}) if isinstance(installed.get("plugins"), dict) else {}
        settings_f = env.claude_dir / "settings.json"
        settings_local_f = env.claude_dir / "settings.local.json"
        settings = load_json(settings_f) or {}
        settings_local = load_json(settings_local_f) or {}
        enabled = settings.get("enabledPlugins", {}) if isinstance(settings.get("enabledPlugins"), dict) else {}
        enabled_local = settings_local.get("enabledPlugins", {}) if isinstance(settings_local.get("enabledPlugins"), dict) else {}
        seen = set()

        def plugin_item(key: str, installs: list, enabled_flag) -> None:
            if key in seen:
                return
            seen.add(key)
            name, _, market = key.partition("@")
            item = Item("Pluginy", key, tool=self.match_framework(name))
            paths: List[Path] = []
            versions = []
            for inst in installs or []:
                if not isinstance(inst, dict):
                    continue
                ip = inst.get("installPath")
                versions.append(str(inst.get("version", "")))
                if ip:
                    paths.append(Path(ip))
            # cache katalog pluginu: plugins/cache/<market>/<name>/
            cache_dir = pdir / "cache" / market / name if market else None
            if cache_dir and cache_dir.is_dir():
                paths.append(cache_dir)
            # dane pluginu
            for dd in (pdir / "data").glob(f"{name}-*") if (pdir / "data").is_dir() else []:
                paths.append(dd)
            uniq: List[Path] = []
            for p in paths:
                if p.exists() and not any(self._is_within(p, u) for u in uniq):
                    uniq = [u for u in uniq if not self._is_within(u, p)]
                    uniq.append(p)
            size = sum(dir_size(p) for p in uniq)
            state = "włączony" if enabled_flag is True else ("wyłączony" if enabled_flag is False else "brak wpisu")
            vs = [v for v in versions if v]
            item.detail = f"{state}" + (f", v{' + v'.join(vs)}" if vs else "") + (f", {human(size)}" if size else "")
            for p in uniq:
                item.actions.append(Action("rm_path", p))
                item.parts.append(env.rel(p))
            if key in plugins:
                item.actions.append(Action("json_del", installed_f, ["plugins", key]))
                item.parts.append(f"wpis w installed_plugins.json")
            if key in enabled:
                item.actions.append(Action("json_del", settings_f, ["enabledPlugins", key]))
                item.parts.append("wpis enabledPlugins w settings.json")
            if key in enabled_local:
                item.actions.append(Action("json_del", settings_local_f, ["enabledPlugins", key]))
                item.parts.append("wpis enabledPlugins w settings.local.json")
            # cache marketplace może mieć plugin bez wpisu installed (dir w cache)
            if item.actions:
                self.add(item)

        for key, installs in plugins.items():
            plugin_item(key, installs if isinstance(installs, list) else [installs], enabled.get(key, enabled_local.get(key)))
        for key, flag in list(enabled.items()) + list(enabled_local.items()):
            if key not in seen and not key.endswith("@inline"):
                plugin_item(key, [], flag)
        # pluginy w cache bez żadnego wpisu
        cache = pdir / "cache"
        if cache.is_dir():
            for market in cache.iterdir():
                if not market.is_dir():
                    continue
                for pl in market.iterdir():
                    if pl.is_dir():
                        plugin_item(f"{pl.name}@{market.name}", [], None)

    def detect_marketplaces(self) -> None:
        env = self.env
        pdir = env.claude_dir / "plugins"
        known_f = pdir / "known_marketplaces.json"
        known = load_json(known_f) or {}
        settings_f = env.claude_dir / "settings.json"
        settings = load_json(settings_f) or {}
        extra = settings.get("extraKnownMarketplaces", {}) if isinstance(settings.get("extraKnownMarketplaces"), dict) else {}
        names = set(known.keys()) | set(extra.keys())
        mdir = pdir / "marketplaces"
        if mdir.is_dir():
            names |= {d.name for d in mdir.iterdir() if d.is_dir()}
        for name in sorted(names):
            item = Item("Marketplace'y pluginów", name)
            src = ""
            info = known.get(name) or extra.get(name) or {}
            if isinstance(info, dict):
                s = info.get("source", {})
                if isinstance(s, dict):
                    src = s.get("repo") or s.get("url") or s.get("path") or ""
            loc = mdir / name
            size = dir_size(loc) if loc.exists() else 0
            item.detail = (src + (", " if src else "")) + (human(size) if size else "brak katalogu")
            if name == "claude-plugins-official":
                item.detail += " — oficjalny (usunięcie: Claude Code pobierze go ponownie)"
            if loc.exists():
                item.actions.append(Action("rm_path", loc))
                item.parts.append(env.rel(loc))
            cache_loc = pdir / "cache" / name
            if cache_loc.exists():
                item.actions.append(Action("rm_path", cache_loc))
                item.parts.append(env.rel(cache_loc) + " (cache pluginów tego marketplace'u)")
            inst_f = pdir / "installed_plugins.json"
            inst = (load_json(inst_f) or {}).get("plugins", {}) or {}
            en = settings.get("enabledPlugins", {}) if isinstance(settings.get("enabledPlugins"), dict) else {}
            for pkey in list(inst) + [k for k in en if k not in inst]:
                if pkey.endswith("@" + name):
                    if pkey in inst:
                        item.actions.append(Action("json_del", inst_f, ["plugins", pkey]))
                    if pkey in en:
                        item.actions.append(Action("json_del", settings_f, ["enabledPlugins", pkey]))
                    item.parts.append(f"wpisy pluginu {pkey}")
            if name in known:
                item.actions.append(Action("json_del", known_f, [name]))
                item.parts.append("wpis w known_marketplaces.json")
            if name in extra:
                item.actions.append(Action("json_del", settings_f, ["extraKnownMarketplaces", name]))
                item.parts.append("wpis extraKnownMarketplaces w settings.json")
            if item.actions:
                self.add(item)

    def detect_mcp_global(self) -> None:
        env = self.env
        cj = load_json(env.claude_json) or {}
        mcps = cj.get("mcpServers", {}) if isinstance(cj.get("mcpServers"), dict) else {}
        for name, cfg in mcps.items():
            item = Item("Serwery MCP (globalne)", name, tool=self.match_framework(name))
            item.detail = self._mcp_desc(cfg)
            item.actions.append(Action("json_del", env.claude_json, ["mcpServers", name]))
            item.parts.append("wpis mcpServers w ~/.claude.json")
            self.add(item)
        projects = cj.get("projects", {}) if isinstance(cj.get("projects"), dict) else {}
        for proj, pcfg in projects.items():
            if not isinstance(pcfg, dict):
                continue
            pm = pcfg.get("mcpServers", {}) if isinstance(pcfg.get("mcpServers"), dict) else {}
            for name, cfg in pm.items():
                item = Item("Serwery MCP (zakres projektu w ~/.claude.json)", f"{name}", tool=self.match_framework(name))
                item.detail = f"{short(proj, 45)} · {self._mcp_desc(cfg)}"
                item.actions.append(Action("json_del", env.claude_json, ["projects", proj, "mcpServers", name]))
                item.parts.append(f"wpis projects[{short(proj, 40)}].mcpServers")
                self.add(item)
        # settings.json może też zawierać mcpServers (rzadkie, ale zdarza się w narzędziach)
        for sf in (env.claude_dir / "settings.json", env.claude_dir / "settings.local.json"):
            s = load_json(sf) or {}
            sm = s.get("mcpServers", {}) if isinstance(s.get("mcpServers"), dict) else {}
            for name, cfg in sm.items():
                item = Item("Serwery MCP (globalne)", name, tool=self.match_framework(name))
                item.detail = f"{sf.name} · {self._mcp_desc(cfg)}"
                item.actions.append(Action("json_del", sf, ["mcpServers", name]))
                item.parts.append(f"wpis mcpServers w {sf.name}")
                self.add(item)

    @staticmethod
    def _mcp_desc(cfg) -> str:
        if not isinstance(cfg, dict):
            return ""
        t = cfg.get("type", "stdio")
        if cfg.get("url"):
            return f"{t}: {short(cfg['url'], 50)}"
        cmd = cfg.get("command", "")
        args = cfg.get("args", [])
        if isinstance(args, list):
            cmd = (cmd + " " + " ".join(str(a) for a in args)).strip()
        return f"{t}: {short(cmd, 55)}" if cmd else t

    def detect_ext_dirs(self) -> None:
        env = self.env
        for sub, cat in EXT_DIRS.items():
            d = env.claude_dir / sub
            if not d.is_dir():
                continue
            for entry in sorted(d.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith(".") and entry.name in (".DS_Store",):
                    continue
                rel = entry.relative_to(env.claude_dir).as_posix()
                tool = self.match_framework(rel)
                item = Item(cat, entry.name, tool=tool)
                if entry.is_dir():
                    n_files = sum(len(f) for _, _, f in os.walk(entry))
                    item.detail = f"katalog, {n_files} plik(ów), {human(dir_size(entry))}"
                    desc = self._skill_description(entry)
                    if desc:
                        item.detail += f" — {desc}"
                else:
                    item.detail = f"plik, {human(dir_size(entry))}"
                    if entry.suffix == ".md":
                        desc = self._md_description(entry)
                        if desc:
                            item.detail += f" — {desc}"
                item.actions.append(Action("rm_path", entry))
                item.parts.append(env.rel(entry))
                self.add(item)

    @staticmethod
    def _md_description(p: Path) -> str:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")[:2000]
        except Exception:
            return ""
        body = txt
        if txt.startswith("---"):
            end = txt.find("\n---", 3)
            fm = txt[3:end] if end != -1 else txt[3:]
            body = txt[end + 4:] if end != -1 else ""
            for key in ("description", "name"):
                m = re.search(rf"^{key}:\s*(.+)$", fm, re.M)
                if m:
                    return short(m.group(1).strip().strip('"\''), 60)
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return short(line, 60)
        return ""

    def _skill_description(self, d: Path) -> str:
        for cand in ("SKILL.md", "README.md", "plugin.json", ".claude-plugin/plugin.json"):
            p = d / cand
            if p.is_file():
                if p.suffix == ".json":
                    j = load_json(p) or {}
                    return short(str(j.get("description") or j.get("name") or ""), 60)
                return self._md_description(p)
        return ""

    def detect_settings_bits(self) -> None:
        env = self.env
        for sf in (env.claude_dir / "settings.json", env.claude_dir / "settings.local.json"):
            s = load_json(sf)
            if not s:
                continue
            self._hooks_from_settings(sf, s, "Hooki (settings)", scope="")
            if "statusLine" in s:
                sl = s["statusLine"]
                cmd = sl.get("command", "") if isinstance(sl, dict) else str(sl)
                item = Item("Status line / inne ustawienia", "statusLine", tool=self.match_framework("", cmd))
                item.detail = f"{sf.name} · {short(cmd, 60)}"
                item.actions.append(Action("json_del", sf, ["statusLine"]))
                item.parts.append(f"klucz statusLine w {sf.name}")
                self.add(item)
            if "outputStyle" in s:
                item = Item("Status line / inne ustawienia", f"outputStyle = {s['outputStyle']}")
                item.detail = f"{sf.name}"
                item.actions.append(Action("json_del", sf, ["outputStyle"]))
                item.parts.append(f"klucz outputStyle w {sf.name}")
                self.add(item)
        for md in ("CLAUDE.md", "CLAUDE.local.md"):
            p = env.claude_dir / md
            if p.is_file():
                item = Item("Status line / inne ustawienia", f"~/.claude/{md}")
                item.detail = f"globalne instrukcje użytkownika, {human(dir_size(p))} — {self._md_description(p)}"
                fw = ""
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").lower()
                    for f in FRAMEWORKS:
                        if f["hook"] and any(k in txt for k in f["hook"]):
                            fw = f["name"]
                            break
                except Exception:
                    pass
                if fw:
                    item.detail += f" (zawiera odwołania do: {fw})"
                item.actions.append(Action("rm_path", p))
                item.parts.append(env.rel(p))
                self.add(item)

    def _hooks_from_settings(self, sf: Path, s: dict, cat: str, scope: str) -> None:
        hooks = s.get("hooks")
        if not isinstance(hooks, dict):
            return
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                continue
            for g in groups:
                if not isinstance(g, dict):
                    continue
                matcher = g.get("matcher", "")
                for h in g.get("hooks", []) or []:
                    if not isinstance(h, dict):
                        continue
                    cmd = str(h.get("command") or h.get("prompt") or h.get("type", ""))
                    if not cmd:
                        continue
                    item = Item(cat, f"{event}" + (f" [{matcher}]" if matcher else ""), tool=self.match_framework("", cmd))
                    item.detail = (scope + " · " if scope else "") + f"{sf.name} · {short(cmd, 70)}"
                    item.actions.append(Action("json_hook_remove", sf, [event, str(matcher)], cmd))
                    item.parts.append(f"hook {event} w {sf.name}")
                    self.add(item)

    def detect_unknown_entries(self) -> None:
        env = self.env
        if not env.claude_dir.is_dir():
            return
        for entry in sorted(env.claude_dir.iterdir(), key=lambda p: p.name.lower()):
            name = entry.name
            if name in CORE_ENTRIES or any(name.startswith(pfx) for pfx in ("settings.json.", ".claude.json", "cached-")):
                continue
            if name.endswith((".bak", ".backup", ".lock", ".log", ".tmp")):
                continue
            rel = entry.relative_to(env.claude_dir).as_posix()
            tool = self.match_framework(rel)
            item = Item("Nieznane elementy w ~/.claude", name, tool=tool)
            if entry.is_dir():
                n_files = sum(len(f) for _, _, f in os.walk(entry))
                item.detail = f"katalog, {n_files} plik(ów), {human(dir_size(entry))}"
                desc = self._skill_description(entry)
                if desc:
                    item.detail += f" — {desc}"
            else:
                item.detail = f"plik, {human(dir_size(entry))}"
            item.actions.append(Action("rm_path", entry))
            item.parts.append(env.rel(entry))
            self.add(item)

    def detect_home_companions(self) -> None:
        """Katalogi w $HOME należące do znanych narzędzi (np. ~/.gstack, ~/.omc)."""
        env = self.env
        try:
            entries = list(env.user_home.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            fw = self.match_home_framework(entry.name)
            if not fw:
                continue
            item = Item("Katalogi narzędzi w katalogu domowym", "~/" + entry.name, tool=fw)
            item.detail = f"{human(dir_size(entry))}"
            item.actions.append(Action("rm_path", entry))
            item.parts.append(env.rel(entry))
            self.add(item)

    # --- projekty ---
    def run_projects(self, root: Path, progress=None) -> int:
        root = root.resolve()
        found_projects = 0
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dp = Path(dirpath)
            try:
                depth = len(dp.relative_to(root).parts)
            except ValueError:
                depth = 0
            scanned += 1
            if progress and scanned % 50 == 0:
                progress(scanned, dp)
            # przytnij katalogi
            keep = []
            for d in dirnames:
                if d in SKIP_DIRS:
                    continue
                keep.append(d)
            dirnames[:] = keep
            if depth >= MAX_DEPTH:
                dirnames[:] = []
            names = set(dirnames) | set(filenames)
            hits = self._project_hits(dp, names)
            if hits:
                found_projects += 1
                self._add_project_items(dp, root, hits)
                # nie schodź do katalogów narzędzi
                hit_roots = {h[0] for h in hits}
                dirnames[:] = [
                    d for d in dirnames
                    if (dp / d) not in hit_roots
                    and d != ".claude"
                    and not any(str(h).startswith(str(dp / d) + os.sep) for h in hit_roots)
                ]
        return found_projects

    def _project_hits(self, dp: Path, names: set) -> List[Tuple[Path, str, str]]:
        """Zwraca (ścieżka, opis, framework) dla markerów w katalogu dp."""
        hits: List[Tuple[Path, str, str]] = []
        if ".claude" in names and (dp / ".claude").is_dir():
            for rel, desc in PROJECT_MARKERS:
                if not rel.startswith(".claude/"):
                    continue
                p = dp / rel
                if p.exists():
                    hits.append((p, desc, self.match_project_framework(rel)))
        for rel, desc in PROJECT_MARKERS:
            if rel.startswith(".claude/"):
                continue
            if rel in names:
                hits.append((dp / rel, desc, ""))
        for fw in FRAMEWORKS:
            for pat in fw["project"]:
                if "/" in pat:
                    continue
                for n in names:
                    if fnmatch.fnmatch(n.lower(), pat.lower()) and not any(h[0] == dp / n for h in hits):
                        hits.append((dp / n, "katalog narzędzia", fw["name"]))
        return hits

    def _add_project_items(self, dp: Path, root: Path, hits: List[Tuple[Path, str, str]]) -> None:
        env = self.env
        try:
            relp = dp.relative_to(root).as_posix() or "."
        except ValueError:
            relp = str(dp)
        cat = f"Projekt: {relp}"
        for p, desc, fw in hits:
            rel_in_proj = p.relative_to(dp).as_posix()
            if p.name == ".mcp.json" and p.is_file():
                j = load_json(p)
                if j is None:
                    self.notes.append(f"Pominięto nieczytelny {p} (błąd składni JSON).")
                    continue
                servers = j.get("mcpServers", {}) if isinstance(j.get("mcpServers"), dict) else {}
                for name, cfg in servers.items():
                    item = Item(cat, f"MCP: {name}", tool=self.match_framework(name))
                    item.detail = f".mcp.json · {self._mcp_desc(cfg)}"
                    item.actions.append(Action("json_del", p, ["mcpServers", name]))
                    item.parts.append(f"wpis mcpServers w {p}")
                    for proj_key, pcfg in self._claude_json_projects_for(dp):
                        for list_name in ("enabledMcpjsonServers", "disabledMcpjsonServers"):
                            if name in (pcfg.get(list_name) or []):
                                item.actions.append(Action("json_list_remove", env.claude_json, ["projects", proj_key, list_name], name))
                                item.parts.append(f"wpis {list_name} w ~/.claude.json")
                    self.add(item)
                if not servers:
                    item = Item(cat, ".mcp.json", detail="pusty / bez serwerów")
                    item.actions.append(Action("rm_path", p))
                    item.parts.append(str(p))
                    self.add(item)
                continue
            if p.name in ("settings.json", "settings.local.json") and p.parent.name == ".claude":
                s = load_json(p) or {}
                self._hooks_from_settings(p, s, cat, scope="")
                en = s.get("enabledPlugins", {}) if isinstance(s.get("enabledPlugins"), dict) else {}
                for key in en:
                    item = Item(cat, f"plugin: {key}", detail=f"{p.name} · enabledPlugins")
                    item.actions.append(Action("json_del", p, ["enabledPlugins", key]))
                    item.parts.append(f"wpis enabledPlugins w {p}")
                    self.add(item)
                sm = s.get("mcpServers", {}) if isinstance(s.get("mcpServers"), dict) else {}
                for name, cfg in sm.items():
                    item = Item(cat, f"MCP: {name}", detail=f"{p.name} · {self._mcp_desc(cfg)}")
                    item.actions.append(Action("json_del", p, ["mcpServers", name]))
                    item.parts.append(f"wpis mcpServers w {p}")
                    self.add(item)
                if "statusLine" in s:
                    item = Item(cat, "statusLine", detail=f"{p.name}")
                    item.actions.append(Action("json_del", p, ["statusLine"]))
                    item.parts.append(f"klucz statusLine w {p}")
                    self.add(item)
                continue
            if p.is_dir() and p.parent.name == ".claude" and p.name in ("commands", "agents", "skills", "hooks", "output-styles"):
                for entry in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                    item = Item(cat, f"{p.name}/{entry.name}", tool=self.match_framework(f"{p.name}/{entry.name}"))
                    item.detail = (f"katalog, {human(dir_size(entry))}" if entry.is_dir() else f"plik, {human(dir_size(entry))}")
                    d = self._skill_description(entry) if entry.is_dir() else (self._md_description(entry) if entry.suffix == ".md" else "")
                    if d:
                        item.detail += f" — {d}"
                    item.actions.append(Action("rm_path", entry))
                    item.parts.append(str(entry))
                    self.add(item)
                continue
            item = Item(cat, rel_in_proj, tool=fw)
            item.detail = desc + (f", {human(dir_size(p))}" if p.exists() else "")
            if p.is_file() and p.suffix == ".md":
                d = self._md_description(p)
                if d:
                    item.detail += f" — {d}"
            item.actions.append(Action("rm_path", p))
            item.parts.append(str(p))
            self.add(item)

    def _claude_json_projects_for(self, project_dir: Path):
        """Zwraca (klucz_projektu, cfg) z ~/.claude.json dla katalogu projektu (normalizuje separatory)."""
        cj = load_json(self.env.claude_json) or {}
        projects = cj.get("projects", {}) if isinstance(cj.get("projects"), dict) else {}
        want = os.path.normcase(os.path.normpath(str(project_dir)))
        out = []
        for k, v in projects.items():
            if isinstance(v, dict) and os.path.normcase(os.path.normpath(k)) == want:
                out.append((k, v))
        return out

    # --- grupowanie frameworków ---
    def group_frameworks(self) -> None:
        """Elementy z tym samym `tool` z zakresu globalnego zbiera w jeden pakiet."""
        groups: Dict[str, List[Item]] = {}
        for it in self.items:
            if it.tool and not it.category.startswith("Projekt:"):
                groups.setdefault(it.tool, []).append(it)
        new_items: List[Item] = []
        grouped_ids = set()
        for tool, members in groups.items():
            if len(members) < 2:
                continue
            pack = Item("Wykryte narzędzia (pakiety)", tool)
            total = 0
            for m in members:
                grouped_ids.add(id(m))
                pack.actions.extend(m.actions)
                pack.parts.append(f"{m.category}: {m.name}")
                total += sum(dir_size(p) for p in m.all_paths())
            pack.detail = f"{len(members)} element(ów)" + (f", {human(total)}" if total else "")
            new_items.append(pack)
        rest = [it for it in self.items if id(it) not in grouped_ids]
        self.items = new_items + rest

    def finalize(self) -> None:
        for bp in BAD_JSON:
            self.notes.append(f"Nie udało się sparsować {bp} — ten plik NIE został przeanalizowany (błąd składni JSON).")
        self.group_frameworks()
        order = [
            "Wykryte narzędzia (pakiety)", "Pluginy", "Marketplace'y pluginów", "Serwery MCP (globalne)",
            "Serwery MCP (zakres projektu w ~/.claude.json)", CAT_DESKTOP_MCP, CAT_DESKTOP_EXT,
            "Skills", "Komendy (slash)", "Agenci",
            "Hooki (settings)", "Hooki (pliki)", "Style odpowiedzi", "Status line / inne ustawienia",
            "Nieznane elementy w ~/.claude", "Katalogi narzędzi w katalogu domowym",
        ]

        def key(it: Item):
            if it.category in order:
                return (0, order.index(it.category), it.name.lower())
            return (1, it.category.lower(), it.name.lower())

        self.items.sort(key=key)
        for i, it in enumerate(self.items, 1):
            it.index = i

    @staticmethod
    def _is_within(p: Path, parent: Path) -> bool:
        try:
            p.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False


# ---------------------------------------------------------------------------
# Wykonanie: kopia zapasowa + operacje
# ---------------------------------------------------------------------------


class Executor:
    def __init__(self, env: Env, backup_root: Path, dry_run: bool):
        self.env = env
        self.dry_run = dry_run
        self.backup_root = backup_root
        self.manifest: dict = {
            "version": VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "claude_dir": str(env.claude_dir), "claude_json": str(env.claude_json),
            "desktop_dirs": [str(d) for d in env.desktop_dirs],
            "moves": [], "json_backups": [], "json_edits": [],
        }
        self._json_backed: Dict[str, str] = {}
        self._seq = 0

    def _backup_path_for(self, p: Path, sub: str = "files") -> Path:
        """Krótka ścieżka w kopii (numer + nazwa), żeby nie przekroczyć limitu długości ścieżek na Windows.
        Pełna oryginalna ścieżka jest zapisana w manifest.json."""
        self._seq += 1
        safe = re.sub(r"[^A-Za-z0-9._@-]+", "_", p.name)[:80] or "item"
        return self.backup_root / sub / f"{self._seq:03d}_{safe}"

    def _ensure_json_backup(self, f: Path) -> None:
        key = str(f)
        if key in self._json_backed or not f.exists():
            return
        dst = self._backup_path_for(f, "json")
        if not self.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
        self._json_backed[key] = str(dst)
        self.manifest["json_backups"].append({"original": key, "backup": str(dst)})

    def _guard(self, p: Path) -> None:
        """Nigdy nie usuwaj katalogu domowego, samego ~/.claude / ~/.claude.json ani katalogu danych Claude Desktop."""
        rp = Path(os.path.abspath(str(p)))
        forbidden = {Path(os.path.abspath(str(x))) for x in (self.env.user_home, self.env.claude_dir, self.env.claude_json, Path.home(), *self.env.desktop_dirs)}
        if rp in forbidden or rp.parent == rp:
            raise RuntimeError(f"Odmowa: {p} jest chronioną ścieżką.")
        for prot in forbidden:
            if str(prot).lower().startswith(str(rp).lower().rstrip(os.sep) + os.sep):
                raise RuntimeError(f"Odmowa: {p} zawiera chronioną ścieżkę {prot}.")

    def apply(self, action: Action) -> str:
        if action.kind == "rm_path":
            self._guard(action.path)
            if not action.path.exists():
                return "pominięto (nie istnieje)"
            dst = self._backup_path_for(action.path)
            if self.dry_run:
                return f"[dry-run] → {dst}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = dst.with_name(dst.name + f".{int(time.time())}")
            try:
                shutil.move(str(action.path), str(dst))
            except Exception:
                # fallback: kopiuj + usuń (np. między dyskami z blokadami)
                if action.path.is_dir():
                    shutil.copytree(action.path, dst, symlinks=True)
                    shutil.rmtree(action.path)
                else:
                    shutil.copy2(action.path, dst)
                    action.path.unlink()
            self.manifest["moves"].append({"from": str(action.path), "to": str(dst)})
            return f"→ kopia: {dst}"
        if action.kind in ("json_del", "json_list_remove", "json_hook_remove"):
            f = action.path
            data = load_json(f)
            if data is None:
                return "pominięto (brak/uszkodzony JSON)"
            self._ensure_json_backup(f)
            changed = False
            if action.kind == "json_del":
                node = data
                for k in action.keys[:-1]:
                    if not isinstance(node, dict) or k not in node:
                        node = None
                        break
                    node = node[k]
                if isinstance(node, dict) and action.keys[-1] in node:
                    del node[action.keys[-1]]
                    changed = True
            elif action.kind == "json_list_remove":
                node = data
                for k in action.keys:
                    node = node.get(k) if isinstance(node, dict) else None
                if isinstance(node, list) and action.value in node:
                    node.remove(action.value)
                    changed = True
            elif action.kind == "json_hook_remove":
                event = action.keys[0]
                hooks = data.get("hooks", {})
                groups = hooks.get(event) if isinstance(hooks, dict) else None
                if isinstance(groups, list):
                    want = action.keys[1] if len(action.keys) > 1 else None
                    for g in groups:
                        if want is not None and str((g or {}).get("matcher", "")) != want:
                            continue
                        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                            before = len(g["hooks"])
                            g["hooks"] = [h for h in g["hooks"] if str((h or {}).get("command") or (h or {}).get("prompt") or (h or {}).get("type", "")) != action.value]
                            changed |= len(g["hooks"]) != before
                    groups[:] = [g for g in groups if not (isinstance(g, dict) and not g.get("hooks"))]
                    if not groups:
                        del hooks[event]
                    if not hooks:
                        data.pop("hooks", None)
            if not changed:
                return "pominięto (wpis już nie istnieje)"
            if self.dry_run:
                return "[dry-run] edycja JSON"
            # .mcp.json bez serwerów -> usuń pusty plik
            if f.name == ".mcp.json" and not data.get("mcpServers") and set(data.keys()) <= {"mcpServers"}:
                dst = self._backup_path_for(f)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dst))
                self.manifest["moves"].append({"from": str(f), "to": str(dst)})
                return "usunięto pusty .mcp.json (kopia zachowana)"
            save_json(f, data)
            self.manifest["json_edits"].append({"file": str(f), "action": action.describe()})
            return "zapisano"
        return "nieznana operacja"

    def write_manifest(self) -> None:
        if self.dry_run:
            return
        self.backup_root.mkdir(parents=True, exist_ok=True)
        save_json(self.backup_root / "manifest.json", self.manifest)


def restore(backup_dir: Path, force: bool = False, home_override: Optional[str] = None) -> int:
    mf = load_json(backup_dir / "manifest.json")
    if not mf:
        print(col(C.RED, f"Brak manifest.json w {backup_dir}"))
        return 1
    print(col(C.ACCENT, "Przywracam z kopii: ") + str(backup_dir))
    cur = find_env(home_override)
    if mf.get("claude_dir") and os.path.normcase(mf["claude_dir"]) != os.path.normcase(str(cur.claude_dir)):
        print(col(C.YELLOW, f"  ! Kopia pochodzi z innej lokalizacji Claude ({mf['claude_dir']}), tu jest {cur.claude_dir}."))
        print(col(C.YELLOW, "    Pliki wrócą na ścieżki zapisane w manifeście."))
        if not force and is_tty():
            try:
                if input(col(C.ACCENT, "  Kontynuować? ") + col(C.GRAY, "[tak/N] ") + "› ").strip().lower() not in ("tak", "t", "y", "yes"):
                    return 1
            except (EOFError, KeyboardInterrupt):
                return 1
    for jb in mf.get("json_backups", []):
        src, dst = Path(jb["backup"]), Path(jb["original"])
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(col(C.GREEN, "  ✓ ") + f"JSON: {dst}")
    for mv in reversed(mf.get("moves", [])):
        src, dst = Path(mv["to"]), Path(mv["from"])
        if src.exists():
            if dst.exists():
                print(col(C.YELLOW, "  ! ") + f"istnieje, pomijam: {dst}")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            print(col(C.GREEN, "  ✓ ") + f"{dst}")
    print(col(C.GREEN, "Gotowe."))
    return 0


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


def banner(env: Env) -> None:
    w, _ = term_size()
    inner = min(w - 2, 76)
    title = f" ✻ claude-cleaner v{VERSION} "
    print()
    print(col(C.ACCENT, "╭" + "─" * inner + "╮"))
    print(col(C.ACCENT, "│") + col(C.BOLD + C.ACCENT, title) + " " * max(0, inner - len(title)) + col(C.ACCENT, "│"))
    line2 = "   porządkowanie pluginów, MCP, skills, hooków i frameworków Claude Code"
    print(col(C.ACCENT, "│") + col(C.GRAY, line2) + " " * max(0, inner - len(line2)) + col(C.ACCENT, "│"))
    print(col(C.ACCENT, "╰" + "─" * inner + "╯"))
    print()
    print(col(C.GRAY, "  system     ") + f"{platform.system()} {platform.release()} · Python {platform.python_version()}")
    print(col(C.GRAY, "  claude     ") + (env.claude_bin or col(C.YELLOW, "nie znaleziono w PATH (to nie przeszkadza)")))
    ok = "✓" if env.claude_dir.is_dir() else "✗"
    print(col(C.GRAY, "  katalog    ") + f"{env.claude_dir} " + (col(C.GREEN, ok) if ok == "✓" else col(C.RED, ok)))
    ok2 = "✓" if env.claude_json.exists() else "✗"
    print(col(C.GRAY, "  config     ") + f"{env.claude_json} " + (col(C.GREEN, ok2) if ok2 == "✓" else col(C.RED, ok2)))
    if env.desktop_dirs:
        for d in env.desktop_dirs:
            print(col(C.GRAY, "  desktop    ") + f"{d} " + col(C.GREEN, "✓"))
    else:
        print(col(C.GRAY, "  desktop    ") + col(C.GRAY, "aplikacja Claude Desktop nie znaleziona (pomijam)"))
    print()


def spinner_line(msg: str) -> None:
    if NO_COLOR or not is_tty():
        return
    sys.stdout.write("\r" + col(C.ACCENT, "⠋ ") + msg + "\x1b[K")
    sys.stdout.flush()


def clear_line() -> None:
    if NO_COLOR or not is_tty():
        return
    sys.stdout.write("\r\x1b[K")
    sys.stdout.flush()


def ask_projects_folder(no_dialog: bool) -> Optional[Path]:
    print(col(C.BLUE, "▍") + col(C.BOLD, " Skanowanie projektów"))
    print(col(C.GRAY, "  Wskaż główny folder z projektami. Skrypt przeszuka go w głąb (do 8 poziomów),"))
    print(col(C.GRAY, "  pomijając node_modules, .git, venv itp."))
    print()
    if not no_dialog and is_tty():
        print("  " + col(C.ACCENT, "Enter") + " – otwórz okno wyboru folderu   " + col(C.ACCENT, "w") + " – wpisz ścieżkę ręcznie   " + col(C.ACCENT, "s") + " – pomiń")
        while True:
            k = read_key()
            if k == "enter":
                p = pick_folder_dialog()
                if p is None:
                    print(col(C.YELLOW, "  Okno dialogowe niedostępne lub anulowano. Wpisz ścieżkę (pusta = pomiń):"))
                    return prompt_path()
                print(col(C.GRAY, "  wybrano: ") + str(p))
                return p
            if k in ("w", "W"):
                return prompt_path()
            if k in ("s", "S", "esc", "q", "Q", "n", "N"):
                print(col(C.GRAY, "  pominięto skan projektów"))
                return None
    return prompt_path()


def prompt_path() -> Optional[Path]:
    try:
        raw = input("  ścieżka › ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_dir():
        print(col(C.RED, f"  Folder nie istnieje: {p}"))
        return prompt_path()
    return p


def pick_folder_dialog() -> Optional[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        chosen = filedialog.askdirectory(title="claude-cleaner: wskaż główny folder z projektami", mustexist=True)
        root.destroy()
        if chosen:
            return Path(chosen)
        return None
    except Exception:
        pass
    # fallbacki systemowe
    try:
        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "claude-cleaner: folder z projektami")'],
                capture_output=True, text=True, timeout=300,
            )
            if out.returncode == 0 and out.stdout.strip():
                return Path(out.stdout.strip())
        elif os.name == "nt":
            import subprocess

            ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                  "$d.Description='claude-cleaner: folder z projektami'; if($d.ShowDialog() -eq 'OK'){ $d.SelectedPath }")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
            if out.returncode == 0 and out.stdout.strip():
                return Path(out.stdout.strip())
        elif shutil.which("zenity"):
            import subprocess

            out = subprocess.run(["zenity", "--file-selection", "--directory", "--title=claude-cleaner: folder z projektami"],
                                 capture_output=True, text=True, timeout=300)
            if out.returncode == 0 and out.stdout.strip():
                return Path(out.stdout.strip())
    except Exception:
        pass
    return None


def print_list(items: List[Item], notes: List[str]) -> None:
    if notes:
        for n in notes:
            print(col(C.YELLOW, "  ! ") + n)
        print()
    if not items:
        print(col(C.GREEN, "  Nie wykryto żadnych rozszerzeń do usunięcia."))
        return
    cat = None
    for it in items:
        if it.category != cat:
            cat = it.category
            print(col(C.BLUE, "▍") + col(C.BOLD, f" {cat}"))
        tool = col(C.PURPLE, f"  ⟨{it.tool}⟩") if it.tool else ""
        print(f"  {col(C.GRAY, f'{it.index:>3}.')} {col(C.WHITE, it.name)}{tool}")
        if it.detail:
            print(f"       {col(C.GRAY, it.detail)}")
    print()


def interactive_select(items: List[Item], env: Env) -> Optional[List[Item]]:
    """Lista z checkboxami. Zwraca zaznaczone elementy lub None (anulowano)."""
    rows: List[Tuple[str, object]] = []
    cat = None
    for it in items:
        if it.category != cat:
            cat = it.category
            rows.append(("cat", cat))
        rows.append(("item", it))
    item_rows = [i for i, r in enumerate(rows) if r[0] == "item"]
    cursor = 0  # indeks w item_rows
    top = 0
    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l")
    try:
        while True:
            w, h = term_size()
            header_lines = 4
            footer_lines = 4
            body_h = max(5, h - header_lines - footer_lines)
            cur_row = item_rows[cursor]
            if cur_row < top:
                top = max(0, cur_row - 1)
            if cur_row >= top + body_h:
                top = cur_row - body_h + 1
            buf = []
            buf.append("\x1b[H")
            sel_n = sum(1 for it in items if it.selected)
            title = f" ✻ claude-cleaner  ·  wybierz elementy do usunięcia  ·  zaznaczono {sel_n}/{len(items)} "
            buf.append(col(C.ACCENT, "╭" + "─" * (min(w, 100) - 2) + "╮") + "\x1b[K\n")
            buf.append(col(C.ACCENT, "│") + col(C.BOLD + C.ACCENT, title[: min(w, 100) - 2]).ljust(min(w, 100) - 2 + (len(C.BOLD + C.ACCENT + C.RESET) if not NO_COLOR else 0)) + col(C.ACCENT, "│") + "\x1b[K\n")
            buf.append(col(C.ACCENT, "╰" + "─" * (min(w, 100) - 2) + "╯") + "\x1b[K\n")
            buf.append("\x1b[K\n")
            for ri in range(top, min(len(rows), top + body_h)):
                kind, obj = rows[ri]
                if kind == "cat":
                    n_in = sum(1 for it in items if it.category == obj)
                    n_sel = sum(1 for it in items if it.category == obj and it.selected)
                    line = col(C.BLUE, "▍") + col(C.BOLD, f" {obj}") + col(C.GRAY, f"  ({n_sel}/{n_in})")
                    buf.append(line + "\x1b[K\n")
                else:
                    it: Item = obj  # type: ignore[assignment]
                    is_cur = ri == cur_row
                    box = col(C.GREEN, "◉") if it.selected else col(C.GRAY, "○")
                    ptr = col(C.ACCENT, "❯") if is_cur else " "
                    name = it.name
                    tool = f"  ⟨{it.tool}⟩" if it.tool else ""
                    detail = f"  {it.detail}" if it.detail else ""
                    avail = w - 10 - len(name) - len(tool)
                    detail = short(detail, max(0, avail)) if avail > 4 else ""
                    if is_cur:
                        line = f" {ptr} {box} " + col(C.BOLD + C.WHITE, name) + col(C.PURPLE, tool) + col(C.GRAY, detail)
                    else:
                        line = f" {ptr} {box} " + (col(C.WHITE, name) if it.selected else name) + col(C.PURPLE, tool) + col(C.GRAY, detail)
                    buf.append(line + "\x1b[K\n")
            for _ in range(body_h - (min(len(rows), top + body_h) - top)):
                buf.append("\x1b[K\n")
            cur_item: Item = rows[cur_row][1]  # type: ignore[assignment]
            parts = "; ".join(cur_item.parts[:4]) + (f" (+{len(cur_item.parts) - 4})" if len(cur_item.parts) > 4 else "")
            buf.append(col(C.GRAY, "  " + short("zawiera: " + parts, w - 4)) + "\x1b[K\n")
            buf.append("\x1b[K\n")
            keys = ("  " + col(C.ACCENT, "↑/↓") + " nawiguj  " + col(C.ACCENT, "spacja") + " zaznacz  " + col(C.ACCENT, "a") + " kategoria  "
                    + col(C.ACCENT, "A") + " wszystko  " + col(C.ACCENT, "i") + " odwróć  " + col(C.ACCENT, "Enter") + " dalej  " + col(C.ACCENT, "q") + " wyjdź")
            buf.append(keys + "\x1b[K\n")
            buf.append("\x1b[J")
            out.write("".join(buf))
            out.flush()

            k = read_key()
            if k == "up":
                cursor = max(0, cursor - 1)
            elif k == "down":
                cursor = min(len(item_rows) - 1, cursor + 1)
            elif k == "pgup":
                cursor = max(0, cursor - body_h)
            elif k == "pgdn":
                cursor = min(len(item_rows) - 1, cursor + body_h)
            elif k == "home":
                cursor = 0
            elif k == "end":
                cursor = len(item_rows) - 1
            elif k == "space":
                cur_item.selected = not cur_item.selected
                cursor = min(len(item_rows) - 1, cursor + 1)
            elif k == "a":
                c = cur_item.category
                members = [it for it in items if it.category == c]
                val = not all(m.selected for m in members)
                for m in members:
                    m.selected = val
            elif k == "A" or k == "*":
                val = not all(it.selected for it in items)
                for it in items:
                    it.selected = val
            elif k in ("i", "I"):
                for it in items:
                    it.selected = not it.selected
            elif k == "enter":
                chosen = [it for it in items if it.selected]
                return chosen
            elif k in ("q", "Q", "esc"):
                return None
    finally:
        out.write("\x1b[?25h\x1b[?1049l")
        out.flush()
    return None


def numbered_select(items: List[Item]) -> Optional[List[Item]]:
    """Fallback bez TTY: numerki oddzielone przecinkami."""
    print_list(items, [])
    try:
        raw = input("  Numery do usunięcia (np. 1,3,5-7 / all / pusta = anuluj) › ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return parse_selection(raw, items)


def parse_selection(raw: str, items: List[Item]) -> Optional[List[Item]]:
    if not raw:
        return None
    if raw.lower() in ("all", "*", "wszystko"):
        return list(items)
    chosen = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            try:
                for n in range(int(a), int(b) + 1):
                    chosen.add(n)
            except ValueError:
                pass
        else:
            try:
                chosen.add(int(tok))
            except ValueError:
                pass
    return [it for it in items if it.index in chosen]


def confirm(chosen: List[Item], backup_root: Path, dry_run: bool, yes: bool) -> bool:
    print()
    print(col(C.BLUE, "▍") + col(C.BOLD, f" Do usunięcia: {len(chosen)} element(ów)"))
    n_actions = 0
    for it in chosen:
        print(f"  {col(C.RED, '−')} {col(C.WHITE, it.name)}" + (col(C.PURPLE, f"  ⟨{it.tool}⟩") if it.tool else "") + col(C.GRAY, f"   [{it.category}]"))
        for a in it.actions:
            n_actions += 1
            print(col(C.GRAY, f"      · {a.describe()}"))
    print()
    print(col(C.GRAY, "  operacji: ") + str(n_actions) + col(C.GRAY, "   kopia zapasowa: ") + str(backup_root))
    if dry_run:
        print(col(C.YELLOW, "  Tryb --dry-run: nic nie zostanie zmienione."))
        return True
    if yes:
        return True
    print()
    print("  Pliki zostaną " + col(C.BOLD, "przeniesione") + " do folderu kopii (nie kasowane), a pliki JSON zmodyfikowane po zrobieniu kopii.")
    print("  Cofnięcie: " + col(C.ACCENT, f"python {Path(sys.argv[0]).name} --restore \"{backup_root}\""))
    return wait_double_enter()


def wait_double_enter() -> bool:
    """Potwierdzenie: Enter naciśnięty dwa razy pod rząd. Każdy inny klawisz anuluje.

    Bez terminala (potok, testy) czyta dwie linie ze stdin — obie muszą być puste."""
    prompt = col(C.ACCENT, "  Kontynuować? ") + col(C.GRAY, "naciśnij Enter dwa razy, dowolny inny klawisz anuluje ") + "› "
    if is_tty():
        sys.stdout.write(prompt)
        sys.stdout.flush()
        try:
            if read_key() != "enter":
                print(col(C.GRAY, "anulowano"))
                return False
            sys.stdout.write(col(C.GREEN, "↵ ") + col(C.GRAY, "jeszcze raz Enter… "))
            sys.stdout.flush()
            if read_key() != "enter":
                print(col(C.GRAY, "anulowano"))
                return False
            print(col(C.GREEN, "↵"))
            return True
        except (EOFError, KeyboardInterrupt):
            print()
            return False
    try:
        if input(prompt).strip():
            return False
        return not input(col(C.GRAY, "  jeszcze raz Enter › ")).strip()
    except (EOFError, KeyboardInterrupt):
        return False


def run_actions(chosen: List[Item], ex: Executor) -> Tuple[int, int]:
    ok = fail = 0
    print()
    for it in chosen:
        print(col(C.ACCENT, "  ✻ ") + col(C.BOLD, it.name))
        for a in it.actions:
            try:
                res = ex.apply(a)
                mark = col(C.GREEN, "✓") if not res.startswith("pominięto") else col(C.YELLOW, "–")
                print(f"     {mark} {col(C.GRAY, a.describe())}  {col(C.GRAY, res)}")
                ok += 1
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"     {col(C.RED, '✗')} {a.describe()}  {col(C.RED, str(e))}")
                print(col(C.YELLOW, "     ! pomijam pozostałe operacje tego elementu, żeby nie zostawić niespójnego stanu"))
                break
    ex.write_manifest()
    return ok, fail


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    global NO_COLOR
    ap = argparse.ArgumentParser(description="claude-cleaner — porządkowanie rozszerzeń Claude Code", add_help=True)
    ap.add_argument("--home", help="katalog traktowany jako HOME (testy); szuka w nim .claude/ i .claude.json; nadpisuje CLAUDE_CONFIG_DIR")
    ap.add_argument("--projects", help="folder z projektami (pomija okno dialogowe)")
    ap.add_argument("--no-projects", action="store_true", help="nie skanuj projektów")
    ap.add_argument("--no-dialog", action="store_true", help="nie otwieraj okna wyboru folderu (wpisz ścieżkę)")
    ap.add_argument("--list", action="store_true", help="tylko wypisz wykryte elementy i zakończ")
    ap.add_argument("--json", action="store_true", help="z --list: wypisz JSON")
    ap.add_argument("--select", help="numery do usunięcia bez interakcji (np. 1,3,5-7 lub all)")
    ap.add_argument("--yes", "-y", action="store_true", help="nie pytaj o potwierdzenie")
    ap.add_argument("--dry-run", action="store_true", help="pokaż operacje, nic nie zmieniaj")
    ap.add_argument("--backup-dir", help="gdzie składać kopie (domyślnie ~/.claude-cleaner-backups/<data>)")
    ap.add_argument("--restore", help="przywróć z podanego folderu kopii")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--version", action="version", version=f"claude-cleaner {VERSION}")
    args = ap.parse_args(argv)

    enable_vt()
    NO_COLOR = args.no_color or bool(os.environ.get("NO_COLOR"))

    if args.restore:
        return restore(Path(args.restore).expanduser(), force=args.yes, home_override=args.home)

    env = find_env(args.home)
    if not args.json:
        banner(env)

    det = Detector(env)
    if not args.json:
        spinner_line("Skanuję konfigurację Claude Code…")
    det.run_global()
    if not args.json:
        clear_line()
        print(col(C.GREEN, "  ✓ ") + f"konfiguracja globalna: {len(det.items)} element(ów)")

    proj_root: Optional[Path] = None
    if args.projects:
        proj_root = Path(args.projects).expanduser()
        if not proj_root.is_dir():
            print(col(C.RED, f"  Folder projektów nie istnieje: {proj_root}"))
            return 2
    elif not args.no_projects and not args.json and not args.list:
        print()
        proj_root = ask_projects_folder(args.no_dialog)

    if proj_root:
        before = len(det.items)

        def prog(n, p):
            if not args.json:
                spinner_line(f"Skanuję projekty… {n} katalogów  {short(str(p), 60)}")

        n_proj = det.run_projects(proj_root, prog)
        if not args.json:
            clear_line()
            print(col(C.GREEN, "  ✓ ") + f"projekty w {proj_root}: {n_proj} folder(ów) z konfiguracją, {len(det.items) - before} element(ów)")

    det.finalize()

    if args.json:
        payload = [{
            "index": it.index, "category": it.category, "name": it.name, "detail": it.detail, "tool": it.tool,
            "parts": it.parts, "actions": [{"kind": a.kind, "path": str(a.path), "keys": a.keys, "value": a.value} for a in it.actions],
        } for it in det.items]
        print(json.dumps({"claude_dir": str(env.claude_dir), "claude_json": str(env.claude_json),
                          "desktop_dirs": [str(d) for d in env.desktop_dirs], "notes": det.notes, "items": payload},
                         ensure_ascii=False, indent=2))
        return 0

    print()
    if args.list:
        print_list(det.items, det.notes)
        return 0

    if not det.items:
        print_list(det.items, det.notes)
        return 0

    if args.select:
        chosen = parse_selection(args.select, det.items)
        if not chosen:
            print_list(det.items, det.notes)
            print(col(C.YELLOW, "  Nic nie wybrano."))
            return 0
    elif is_tty():
        chosen = interactive_select(det.items, env)
        if chosen is None:
            print(col(C.GRAY, "  Anulowano."))
            return 0
        if not chosen:
            print(col(C.YELLOW, "  Nic nie zaznaczono — nic nie usunięto."))
            return 0
    else:
        chosen = numbered_select(det.items)
        if not chosen:
            print(col(C.GRAY, "  Anulowano."))
            return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_root = Path(args.backup_dir).expanduser() if args.backup_dir else (env.user_home / ".claude-cleaner-backups" / stamp)
    if not confirm(chosen, backup_root, args.dry_run, args.yes):
        print(col(C.GRAY, "  Anulowano."))
        return 0

    ex = Executor(env, backup_root, args.dry_run)
    ok, fail = run_actions(chosen, ex)
    print()
    if args.dry_run:
        print(col(C.YELLOW, f"  [dry-run] zaplanowano {ok} operacji, błędów: {fail}. Nic nie zmieniono."))
    else:
        print(col(C.GREEN, f"  ✓ Wykonano {ok} operacji") + (col(C.RED, f", błędów: {fail}") if fail else ""))
        print(col(C.GRAY, "  kopia zapasowa + manifest: ") + str(backup_root))
        print(col(C.GRAY, "  cofnij: ") + col(C.ACCENT, f"python {Path(sys.argv[0]).name} --restore \"{backup_root}\""))
        print(col(C.GRAY, "  Uruchom Claude Code ponownie, aby zmiany zostały wczytane."))
    print()
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        print("\n  Przerwano.")
        sys.exit(130)
