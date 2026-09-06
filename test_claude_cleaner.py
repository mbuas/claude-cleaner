#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testy akceptacyjne dla claude-cleaner.py.

Uruchomienie:
    python test_claude_cleaner.py -v

Bezpieczeństwo:
  * każdy przebieg skryptu dostaje `--home <fixture>` oraz `--projects`/`--no-projects`,
  * wszystkie fixture'y i kopie zapasowe powstają wyłącznie w katalogu tymczasowym systemu (podkatalog claude-cleaner-tests),
  * _assert_safe() blokuje przekazanie do skryptu jakiejkolwiek ścieżki spoza tego drzewa.

Testy z prefiksem bug_ dokumentują błędy znalezione w przeglądzie kodu; po poprawkach wszystkie przechodzą.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).resolve().parent / "claude-cleaner.py"
SANDBOX = Path(tempfile.gettempdir()) / "claude-cleaner-tests"
KEEP = bool(os.environ.get("KEEP_FIXTURES"))

# Ścieżki, których testy nie mogą dotknąć nawet przez pomyłkę.
REAL_CLAUDE_DIR = Path.home() / ".claude"
REAL_CLAUDE_JSON = Path.home() / ".claude.json"


def _assert_safe(p) -> str:
    """Każda ścieżka przekazywana skryptowi musi leżeć w piaskownicy."""
    rp = Path(str(p)).resolve()
    try:
        rp.relative_to(SANDBOX.resolve())
    except ValueError:
        raise AssertionError(f"ODMOWA: ścieżka poza piaskownicą: {rp}")
    return str(rp)


def run_cleaner(*args: str, expect_rc=None, timeout=300, stdin_text: str = "") -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), *args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CLAUDE_CONFIG_DIR", None)  # nigdy nie dziedziczymy zmiennej globalnej
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", input=stdin_text,
                        errors="replace", env=env, timeout=timeout)
    if expect_rc is not None and cp.returncode != expect_rc:
        raise AssertionError(
            f"rc={cp.returncode} (oczekiwano {expect_rc})\ncmd={cmd}\n--- stdout ---\n{cp.stdout}\n--- stderr ---\n{cp.stderr}")
    return cp


def list_json(home: Path, projects=None) -> dict:
    args = ["--home", _assert_safe(home), "--list", "--json"]
    if projects is None:
        args.append("--no-projects")
    else:
        args += ["--projects", _assert_safe(projects)]
    cp = run_cleaner(*args, expect_rc=0)
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"Niepoprawny JSON na stdout:\n{cp.stdout[:4000]}\nSTDERR:\n{cp.stderr[:2000]}")


def find_item(payload: dict, *, category=None, name=None, name_contains=None, tool=None):
    out = []
    for it in payload["items"]:
        if category is not None and it["category"] != category:
            continue
        if name is not None and it["name"] != name:
            continue
        if name_contains is not None and name_contains not in it["name"]:
            continue
        if tool is not None and it["tool"] != tool:
            continue
        out.append(it)
    return out


def one_item(payload: dict, **kw) -> dict:
    hits = find_item(payload, **kw)
    if len(hits) != 1:
        names = [f"{i['category']} | {i['name']}" for i in payload["items"]]
        raise AssertionError(f"Oczekiwano 1 elementu dla {kw}, jest {len(hits)}.\nWykryte:\n" + "\n".join(names))
    return hits[0]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _w(p: Path, content=""):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        p.write_text(json.dumps(content, indent=2), encoding="utf-8")
    else:
        p.write_text(content, encoding="utf-8")


def build_fixture(root: Path) -> tuple:
    """Buduje sztuczny HOME + folder projektów. Zwraca (home, projects)."""
    root = Path(root)
    if root.exists():
        shutil.rmtree(root)
    home = root / "home"
    proj = root / "projects"
    cd = home / ".claude"

    # --- pliki rdzenia Claude Code (NIE mogą być wykryte jako rozszerzenia)
    _w(cd / "history.jsonl", "{}\n")
    _w(cd / ".credentials.json", {"x": 1})
    _w(cd / "projects/C--fake/mem.md", "x")
    _w(cd / "sessions/a.json", "{}")
    _w(cd / "shell-snapshots/s.sh", "#")
    _w(cd / "statsig/x", "x")
    _w(cd / "todos/x.json", "[]")
    _w(cd / "settings.json.bak-old", "{}")

    # --- pluginy
    _w(cd / "plugins/installed_plugins.json", {"version": 2, "plugins": {
        "superpowers@claude-plugins-official": [
            {"scope": "user", "installPath": str(cd / "plugins/cache/claude-plugins-official/superpowers/6.3.0"),
             "version": "6.3.0"}],
        "karpathy@karpathy-skills": [
            {"scope": "user", "installPath": str(cd / "plugins/cache/karpathy-skills/karpathy/1.0.0"),
             "version": "1.0.0"}],
    }})
    _w(cd / "plugins/known_marketplaces.json", {
        "claude-plugins-official": {"source": {"source": "github", "repo": "anthropics/claude-plugins-official"}},
        "karpathy-skills": {"source": {"source": "github", "repo": "forrestchang/andrej-karpathy-skills"}},
    })
    _w(cd / "plugins/cache/claude-plugins-official/superpowers/6.3.0/.claude-plugin/plugin.json",
       {"name": "superpowers", "description": "Superpowers skills"})
    _w(cd / "plugins/cache/claude-plugins-official/superpowers/6.3.0/skills/brainstorming/SKILL.md",
       "---\nname: brainstorming\n---\n")
    _w(cd / "plugins/cache/karpathy-skills/karpathy/1.0.0/plugin.json", {"name": "karpathy"})
    _w(cd / "plugins/cache/orphan-market/orphan-plugin/0.1.0/README.md", "# orphan")
    _w(cd / "plugins/marketplaces/claude-plugins-official/.claude-plugin/marketplace.json",
       {"name": "claude-plugins-official"})
    _w(cd / "plugins/marketplaces/karpathy-skills/.claude-plugin/marketplace.json", {"name": "karpathy-skills"})
    _w(cd / "plugins/data/superpowers-claude-plugins-official/state.json", {})

    # --- settings.json: hooki, statusLine, enabledPlugins, extraKnownMarketplaces
    _w(cd / "settings.json", {
        "permissions": {"defaultMode": "auto"},
        "model": "opus",
        "enabledPlugins": {"superpowers@claude-plugins-official": True,
                           "karpathy@karpathy-skills": False,
                           "design@inline": False},
        "extraKnownMarketplaces": {
            "karpathy-skills": {"source": {"source": "github", "repo": "forrestchang/andrej-karpathy-skills"}}},
        "hooks": {
            "Stop": [{"hooks": [{"type": "command",
                                 "command": "bash ~/.claude/skills/gstack/hosts/claude/hooks/timeline-stop-hook",
                                 "timeout": 5}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "node ~/.claude/hooks/gsd-check-update.js"},
                                        {"type": "command", "command": "echo custom-hook"}]}],
            "UserPromptSubmit": [{"matcher": "", "hooks": [
                {"type": "command", "command": "node ~/.claude/hooks/gsd-context-monitor.js"}]}],
        },
        "statusLine": {"type": "command", "command": "node ~/.claude/hooks/gsd-statusline.js"},
        "theme": "dark",
    })
    _w(cd / "settings.local.json", {"enabledPlugins": {"localplug@some-market": True}})

    # --- GSD
    _w(cd / "get-shit-done/bin/install.js", "//")
    _w(cd / "get-shit-done/workflows/plan.md", "# plan")
    _w(cd / "commands/gsd/plan-phase.md", "---\ndescription: Plan a phase\n---\n")
    _w(cd / "commands/gsd/execute-phase.md", "x")
    _w(cd / "agents/gsd-planner.md", "---\ndescription: GSD planner agent\n---\n")
    _w(cd / "agents/gsd-executor.md", "x")
    _w(cd / "hooks/gsd-check-update.js", "//")
    _w(cd / "hooks/gsd-context-monitor.js", "//")
    _w(cd / "hooks/gsd-statusline.js", "//")

    # --- gstack
    _w(cd / "skills/gstack/SKILL.md", "---\nname: gstack\ndescription: gstack umbrella skill\n---\n")
    _w(cd / "skills/gstack/hosts/claude/hooks/timeline-stop-hook", "#!/bin/bash")
    _w(home / ".gstack/config.json", {})

    # --- własne rozszerzenia
    _w(cd / "skills/my-skill/SKILL.md", "---\nname: my-skill\ndescription: Moja własna umiejętność\n---\n")
    _w(cd / "commands/review.md", "---\ndescription: Custom review command\n---\n")
    _w(cd / "agents/reviewer.md", "---\ndescription: Reviewer\n---\n")
    _w(cd / "output-styles/concise.md", "---\nname: concise\n---\n")
    _w(cd / "CLAUDE.md", "# Global\nUse SuperClaude framework\n")

    # --- SuperClaude + nieznane elementy
    _w(cd / "SuperClaude/core.md", "x")
    _w(cd / "commands/sc/analyze.md", "x")
    _w(cd / "some-random-tool/data.bin", "x")
    _w(cd / "notes.txt", "x")

    # --- ~/.claude.json
    _w(home / ".claude.json", {
        "numStartups": 5,
        "mcpServers": {
            "pal": {"type": "stdio", "command": "npx", "args": ["-y", "pal-mcp"]},
            "context7": {"type": "http", "url": "https://mcp.context7.com/mcp"},
            "serena": {"type": "stdio", "command": "uvx", "args": ["serena"]},
        },
        "projects": {
            str(proj / "app1"): {"allowedTools": [],
                                 "mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp"]}},
                                 "enabledMcpjsonServers": ["db"], "disabledMcpjsonServers": []},
            "C:/somewhere/else": {"allowedTools": [], "mcpServers": {}},
        },
    })

    # --- projekty
    _w(proj / "app1/.mcp.json", {"mcpServers": {"db": {"command": "npx", "args": ["db-mcp"]},
                                                "gh": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}}})
    _w(proj / "app1/.claude/settings.json",
       {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}]},
        "enabledPlugins": {"x@y": True}})
    _w(proj / "app1/.claude/settings.local.json", {"permissions": {"allow": ["Bash(ls:*)"]}})
    _w(proj / "app1/.claude/commands/deploy.md", "---\ndescription: Deploy app\n---\n")
    _w(proj / "app1/.claude/agents/tester.md", "x")
    _w(proj / "app1/.claude/skills/career-ops/SKILL.md", "---\nname: career-ops\n---\n")
    _w(proj / "app1/CLAUDE.md", "# app1 instructions")
    _w(proj / "app1/.planning/PROJECT.md", "# gsd planning")
    _w(proj / "app1/src/main.py", "print(1)")
    # to MUSI zostać pominięte (node_modules)
    _w(proj / "app1/node_modules/.claude/settings.json", {"hooks": {}})
    _w(proj / "app1/node_modules/foo/.mcp.json", {"mcpServers": {"NODEMODULESLEAK": {}}})
    _w(proj / "nested/deeper/app2/.gstack/state.json", {})
    _w(proj / "nested/deeper/app2/.claude-flow/config.json", {})
    _w(proj / "nested/deeper/app2/.specify/memory/constitution.md", "x")
    _w(proj / "nested/deeper/app2/CLAUDE.local.md", "local")
    _w(proj / "nested/deeper/app2/.claude/ccpm/x.md", "x")
    _w(proj / "plain/README.md", "nothing here")
    _w(proj / "app3/.mcp.json", {"mcpServers": {"only": {"command": "x"}}})
    _w(proj / "app4/.claude/settings.json",
       {"statusLine": {"type": "command", "command": "x"}, "mcpServers": {"insettings": {"command": "y"}}})
    return home, proj


# ---------------------------------------------------------------------------
# Snapshot drzewa
# ---------------------------------------------------------------------------


def snapshot(root: Path) -> dict:
    """Mapa: ścieżka względna -> sha256 pliku / '<dir>' / '<link:target>'."""
    root = Path(root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        for d in list(dirnames):
            p = dp / d
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = "<link:" + str(os.readlink(p)) + ">"
                dirnames.remove(d)
            else:
                out[rel] = "<dir>"
        for f in filenames:
            p = dp / f
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = "<link:" + str(os.readlink(p)) + ">"
            else:
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def compare_trees(tc: unittest.TestCase, before: dict, after: dict, root: Path):
    """Porównanie: JSON semantycznie, reszta bajtowo."""
    missing = sorted(set(before) - set(after))
    extra = sorted(set(after) - set(before))
    tc.assertEqual([], missing, "brakujące ścieżki po restore")
    tc.assertEqual([], extra, "nadmiarowe ścieżki po restore")
    for rel in sorted(before):
        if before[rel] == after[rel]:
            continue
        p = root / rel
        if p.suffix == ".json" and p.is_file():
            # semantycznie
            a = json.loads((root / rel).read_text(encoding="utf-8"))
            tc.fail(f"plik JSON {rel} różni się bajtowo; treść po restore: {json.dumps(a)[:300]}")
        tc.fail(f"plik {rel} różni się po restore ({before[rel]} != {after[rel]})")


def json_equal_tree(tc: unittest.TestCase, root: Path, before_json: dict):
    for rel, data in before_json.items():
        p = root / rel
        tc.assertTrue(p.is_file(), f"brak pliku {rel}")
        tc.assertEqual(data, json.loads(p.read_text(encoding="utf-8")), f"treść JSON {rel} inna niż przed")


def collect_json(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix == ".json" or p.name == ".mcp.json":
                try:
                    out[str(p.relative_to(root))] = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
    return out


# ---------------------------------------------------------------------------
# Baza testów
# ---------------------------------------------------------------------------


class CleanerTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not SCRIPT.is_file():
            raise unittest.SkipTest(f"brak skryptu {SCRIPT}")
        SANDBOX.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="t", dir=str(SANDBOX)))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if KEEP:
            return
        shutil.rmtree(self.root, ignore_errors=True)

    def fixture(self, name="fx"):
        home, proj = build_fixture(self.root / name)
        return home, proj

    def backup_dir(self, name="bk"):
        return self.root / name


# ---------------------------------------------------------------------------
# a) wykrywanie
# ---------------------------------------------------------------------------


class TestA_Detection(CleanerTest):

    def setUp(self):
        super().setUp()
        self.home, self.proj = self.fixture()
        self.payload = list_json(self.home, self.proj)

    def test_a01_pakiety_gsd_i_gstack(self):
        one_item(self.payload, category="Wykryte narzędzia (pakiety)", name="GSD (Get Shit Done)")
        one_item(self.payload, category="Wykryte narzędzia (pakiety)", name="gstack")

    def test_a02_plugin_superpowers(self):
        it = one_item(self.payload, category="Pluginy", name="superpowers@claude-plugins-official")
        self.assertTrue(any(a["kind"] == "rm_path" for a in it["actions"]))
        self.assertTrue(any(a["kind"] == "json_del" and a["keys"][:1] == ["plugins"] for a in it["actions"]))

    def test_a03_marketplace_karpathy(self):
        one_item(self.payload, category="Marketplace'y pluginów", name="karpathy-skills")

    def test_a04_mcp_globalne_pal_i_context7(self):
        one_item(self.payload, category="Serwery MCP (globalne)", name="pal")
        one_item(self.payload, category="Serwery MCP (globalne)", name="context7")

    def test_a05_mcp_projektowy_playwright(self):
        it = one_item(self.payload, category="Serwery MCP (zakres projektu w ~/.claude.json)", name="playwright")
        self.assertEqual("json_del", it["actions"][0]["kind"])
        self.assertEqual(["projects"], it["actions"][0]["keys"][:1])

    def test_a06_hook_custom_osobny_element(self):
        hooks = find_item(self.payload, category="Hooki (settings)")
        names = {h["name"] for h in hooks}
        self.assertIn("SessionStart", names, f"hook 'echo custom-hook' powinien być osobnym elementem; jest: {names}")
        it = one_item(self.payload, category="Hooki (settings)", name="SessionStart")
        self.assertIn("echo custom-hook", it["detail"])
        self.assertEqual("", it["tool"], "hook użytkownika nie może być przypisany do żadnego pakietu")

    def test_a07_planning_w_app1(self):
        it = one_item(self.payload, category="Projekt: app1", name=".planning")
        self.assertEqual("GSD (Get Shit Done)", it["tool"])

    def test_a08_mcp_db_i_gh_z_mcp_json(self):
        one_item(self.payload, category="Projekt: app1", name="MCP: db")
        one_item(self.payload, category="Projekt: app1", name="MCP: gh")

    def test_a09_nic_z_node_modules(self):
        blob = json.dumps(self.payload, ensure_ascii=False)
        self.assertNotIn("NODEMODULESLEAK", blob)
        self.assertNotIn("node_modules", blob)

    def test_a10_pliki_core_nie_sa_na_liscie(self):
        blob = json.dumps(self.payload, ensure_ascii=False)
        for core in ("history.jsonl", "settings.json.bak-old", ".credentials.json"):
            self.assertNotIn(core, blob, f"element rdzenia {core} nie powinien być zgłoszony")
        # projects/ i sessions/ nie mogą być zgłoszone jako element do usunięcia
        for it in self.payload["items"]:
            for a in it["actions"]:
                if a["kind"] != "rm_path":
                    continue
                p = Path(a["path"])
                self.assertNotIn(p.name, {"projects", "sessions", "todos", "statsig", "shell-snapshots"},
                                 f"element rdzenia zgłoszony do usunięcia: {a['path']}")


# ---------------------------------------------------------------------------
# b) dry-run
# ---------------------------------------------------------------------------


class TestB_DryRun(CleanerTest):

    def test_b01_dry_run_nic_nie_zmienia(self):
        home, proj = self.fixture()
        root = self.root / "fx"
        before = snapshot(root)
        cp = run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                         "--select", "all", "--yes", "--dry-run", "--no-color",
                         "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)
        self.assertIn("dry-run", cp.stdout)
        after = snapshot(root)
        self.assertEqual(before, after, "tryb --dry-run zmodyfikował pliki")
        self.assertFalse(self.backup_dir().exists(), "--dry-run utworzył katalog kopii zapasowej")


# ---------------------------------------------------------------------------
# c) pełne usunięcie + restore
# ---------------------------------------------------------------------------


class TestC_RoundTrip(CleanerTest):

    def test_c01_select_all_potem_restore(self):
        home, proj = self.fixture()
        root = self.root / "fx"
        before = snapshot(root)
        before_json = collect_json(root)
        bk = self.backup_dir()

        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", "all", "--yes", "--no-color", "--backup-dir", _assert_safe(bk), expect_rc=0)

        mid = snapshot(root)
        self.assertNotEqual(before, mid, "usuwanie nic nie zmieniło")
        self.assertTrue((bk / "manifest.json").is_file(), "brak manifest.json w kopii")
        mf = json.loads((bk / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(mf["moves"], "manifest bez przeniesień")
        self.assertTrue(mf["json_backups"], "manifest bez kopii JSON")

        run_cleaner("--restore", _assert_safe(bk), "--no-color", expect_rc=0)

        after = snapshot(root)
        compare_trees(self, before, after, root)
        json_equal_tree(self, root, before_json)


# ---------------------------------------------------------------------------
# d) częściowe usunięcie pakietu GSD
# ---------------------------------------------------------------------------


class TestD_PartialGSD(CleanerTest):

    def test_d01_usuniecie_pakietu_gsd(self):
        home, proj = self.fixture()
        payload = list_json(home, proj)
        gsd = one_item(payload, category="Wykryte narzędzia (pakiety)", name="GSD (Get Shit Done)")

        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", str(gsd["index"]), "--yes", "--no-color",
                    "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)

        s = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
        cmds = []
        for event, groups in s.get("hooks", {}).items():
            for g in groups:
                self.assertTrue(g.get("hooks"), f"pusta grupa hooków w evencie {event}: {g}")
                for h in g["hooks"]:
                    cmds.append((event, h.get("command", "")))
        flat = [c for _, c in cmds]
        self.assertNotIn("node ~/.claude/hooks/gsd-check-update.js", flat, "hook gsd nie został usunięty")
        self.assertNotIn("node ~/.claude/hooks/gsd-context-monitor.js", flat, "hook gsd nie został usunięty")
        self.assertIn("echo custom-hook", flat, "usunięto hook użytkownika!")
        self.assertIn("bash ~/.claude/skills/gstack/hosts/claude/hooks/timeline-stop-hook", flat,
                      "usunięto hook gstack, choć wybrano tylko GSD")
        self.assertNotIn("UserPromptSubmit", s.get("hooks", {}), "pusty event UserPromptSubmit został w settings.json")
        self.assertNotIn("statusLine", s, "statusLine GSD nie został usunięty")

        # pliki GSD zniknęły, pliki gstack zostały
        self.assertFalse((home / ".claude/commands/gsd").exists())
        self.assertFalse((home / ".claude/agents/gsd-planner.md").exists())
        self.assertFalse((home / ".claude/hooks/gsd-statusline.js").exists())
        self.assertFalse((home / ".claude/get-shit-done").exists())
        self.assertTrue((home / ".claude/skills/gstack/SKILL.md").exists())
        self.assertTrue((home / ".claude/commands/review.md").exists())
        # ustawienia niezwiązane nietknięte
        self.assertEqual("opus", s.get("model"))
        self.assertEqual("dark", s.get("theme"))


# ---------------------------------------------------------------------------
# e) usunięcie MCP db z projektu
# ---------------------------------------------------------------------------


class TestE_ProjectMcp(CleanerTest):

    def test_e01_usuniecie_db_zostawia_gh(self):
        home, proj = self.fixture()
        payload = list_json(home, proj)
        db = one_item(payload, category="Projekt: app1", name="MCP: db")

        kinds = sorted(a["kind"] for a in db["actions"])
        self.assertEqual(["json_del", "json_list_remove"], kinds,
                         "element MCP db powinien czyścić .mcp.json i enabledMcpjsonServers")

        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", str(db["index"]), "--yes", "--no-color",
                    "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)

        mcp = json.loads((proj / "app1/.mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(["gh"], list(mcp["mcpServers"].keys()))
        cj = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        pcfg = cj["projects"][str(proj / "app1")]
        self.assertEqual([], pcfg["enabledMcpjsonServers"], "'db' nie zniknęło z enabledMcpjsonServers")
        # globalne MCP nietknięte
        self.assertEqual({"pal", "context7", "serena"}, set(cj["mcpServers"].keys()))


# ---------------------------------------------------------------------------
# f) usunięcie jedynego MCP kasuje pusty plik
# ---------------------------------------------------------------------------


class TestF_EmptyMcpFile(CleanerTest):

    def test_f01_jedyny_mcp_usuwa_plik(self):
        home, proj = self.fixture()
        payload = list_json(home, proj)
        only = one_item(payload, category="Projekt: app3", name="MCP: only")
        bk = self.backup_dir()

        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", str(only["index"]), "--yes", "--no-color",
                    "--backup-dir", _assert_safe(bk), expect_rc=0)

        self.assertFalse((proj / "app3/.mcp.json").exists(), "pusty .mcp.json nie został usunięty")
        mf = json.loads((bk / "manifest.json").read_text(encoding="utf-8"))
        moved = [m for m in mf["moves"] if m["from"].endswith(".mcp.json")]
        self.assertTrue(moved, "brak wpisu przeniesienia .mcp.json w manifeście")
        self.assertTrue(Path(moved[0]["to"]).exists(), "kopia .mcp.json nie istnieje")
        # kopia sprzed edycji zawiera oryginalny serwer
        backups = [b for b in mf["json_backups"] if b["original"].endswith("app3\\.mcp.json")
                   or b["original"].endswith("app3/.mcp.json")]
        self.assertTrue(backups, "brak kopii JSON pliku .mcp.json")
        orig = json.loads(Path(backups[0]["backup"]).read_text(encoding="utf-8"))
        self.assertIn("only", orig["mcpServers"])

        # restore odtwarza plik z oryginalną treścią
        run_cleaner("--restore", _assert_safe(bk), "--no-color", expect_rc=0)
        restored = json.loads((proj / "app3/.mcp.json").read_text(encoding="utf-8"))
        self.assertIn("only", restored["mcpServers"])


# ---------------------------------------------------------------------------
# g) odporność
# ---------------------------------------------------------------------------


class TestG_Robustness(CleanerTest):

    def test_g01_uszkodzony_settings_json_nie_wywala(self):
        home, proj = self.fixture()
        (home / ".claude/settings.json").write_text('{ "hooks": [broken,,\n', encoding="utf-8")
        cp = run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                         "--list", "--json", expect_rc=0)
        payload = json.loads(cp.stdout)
        self.assertTrue(payload["items"], "po uszkodzeniu settings.json nic nie wykryto")
        self.assertNotIn("Traceback", cp.stderr)

    def test_g02_uszkodzony_claude_json_nie_wywala(self):
        home, proj = self.fixture()
        (home / ".claude.json").write_text("NOT JSON AT ALL", encoding="utf-8")
        cp = run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                         "--list", "--json", expect_rc=0)
        json.loads(cp.stdout)
        self.assertNotIn("Traceback", cp.stderr)

    def test_g03_pusty_home_czytelny_komunikat_i_rc0(self):
        empty = self.root / "empty"
        empty.mkdir(parents=True)
        cp = run_cleaner("--home", _assert_safe(empty), "--no-projects", "--list", "--no-color", expect_rc=0)
        self.assertIn("Nie znaleziono katalogu Claude Code", cp.stdout)
        cp2 = run_cleaner("--home", _assert_safe(empty), "--no-projects", "--list", "--json", expect_rc=0)
        payload = json.loads(cp2.stdout)
        self.assertEqual([], payload["items"])
        self.assertTrue(payload["notes"])

    def test_g04_brak_katalogu_projektow_rc2(self):
        home, _ = self.fixture()
        missing = self.root / "nie-ma-takiego"
        cp = run_cleaner("--home", _assert_safe(home), "--projects", str(missing), "--list", "--no-color")
        self.assertEqual(2, cp.returncode, cp.stdout + cp.stderr)
        self.assertIn("nie istnieje", cp.stdout)

    def test_g05_select_999_nic_nie_usuwa(self):
        home, proj = self.fixture()
        root = self.root / "fx"
        before = snapshot(root)
        cp = run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                         "--select", "999", "--yes", "--no-color",
                         "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)
        self.assertIn("Nic nie wybrano", cp.stdout)
        self.assertEqual(before, snapshot(root), "--select 999 coś zmienił")
        self.assertFalse(self.backup_dir().exists())

    def test_g06_restore_bez_manifestu_rc1(self):
        bad = self.root / "nomanifest"
        bad.mkdir(parents=True)
        cp = run_cleaner("--restore", _assert_safe(bad), "--no-color")
        self.assertEqual(1, cp.returncode)
        self.assertIn("manifest.json", cp.stdout)


# ---------------------------------------------------------------------------
# h) bezpieczeństwo i przypadki brzegowe
# ---------------------------------------------------------------------------


class TestH_Safety(CleanerTest):

    def _guard_fixture(self):
        root = self.root / "guard"
        home = root / "home"
        cd = home / ".claude"
        _w(cd / "settings.json", {})
        _w(home / ".claude.json", {"mcpServers": {}})
        _w(cd / "plugins/installed_plugins.json", {"version": 2, "plugins": {
            "evil-home@m": [{"installPath": str(home), "version": "1"}],
            "evil-claude@m": [{"installPath": str(cd), "version": "1"}],
            "evil-json@m": [{"installPath": str(home / ".claude.json"), "version": "1"}],
        }})
        return home

    def test_h01_guard_chroni_home_claude_i_claude_json(self):
        home = self._guard_fixture()
        cd = home / ".claude"
        cp = run_cleaner("--home", _assert_safe(home), "--no-projects", "--select", "all", "--yes", "--no-color",
                         "--backup-dir", _assert_safe(self.backup_dir()))
        self.assertEqual(1, cp.returncode, "przy zablokowanych operacjach oczekiwany rc=1")
        self.assertEqual(3, cp.stdout.count("jest chronioną ścieżką"), cp.stdout)
        self.assertTrue(home.is_dir(), "katalog HOME został przeniesiony!")
        self.assertTrue(cd.is_dir(), "katalog .claude został przeniesiony!")
        self.assertTrue((home / ".claude.json").is_file(), ".claude.json został przeniesiony!")

    def test_h02_guard_w_kodzie(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("def _guard", src)
        self.assertIn("self._guard(action.path)", src)

    def test_h03_plik_zamiast_katalogu_w_skills(self):
        home, proj = self.fixture()
        _w(home / ".claude/skills/plik-skill.md", "---\ndescription: Plik zamiast katalogu\n---\n")
        payload = list_json(home, proj)
        it = one_item(payload, category="Skills", name="plik-skill.md")
        self.assertIn("plik", it["detail"])
        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", str(it["index"]), "--yes", "--no-color",
                    "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)
        self.assertFalse((home / ".claude/skills/plik-skill.md").exists())
        self.assertTrue((home / ".claude/skills/my-skill/SKILL.md").exists())

    def test_h04_symlink_w_skills(self):
        home, proj = self.fixture()
        link = home / ".claude/skills/link-skill"
        target = home / ".claude/skills/my-skill"
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as e:
            self.skipTest(f"brak uprawnień do tworzenia dowiązań: {e}")
        payload = list_json(home, proj)
        it = one_item(payload, category="Skills", name="link-skill")
        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", str(it["index"]), "--yes", "--no-color",
                    "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)
        self.assertFalse(link.exists() or link.is_symlink(), "dowiązanie nie zostało usunięte")
        self.assertTrue((target / "SKILL.md").is_file(), "usunięto CEL dowiązania, nie samo dowiązanie!")

    def test_h05_projekt_z_polskimi_znakami_i_spacja(self):
        home, proj = self.fixture()
        p = proj / "mój projekt ąćę"
        _w(p / ".mcp.json", {"mcpServers": {"zażółć": {"command": "x"}}})
        _w(p / ".claude/commands/żółw.md", "---\ndescription: Żółw\n---\n")
        payload = list_json(home, proj)
        cat = "Projekt: mój projekt ąćę"
        one_item(payload, category=cat, name="MCP: zażółć")
        it = one_item(payload, category=cat, name="commands/żółw.md")
        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", str(it["index"]), "--yes", "--no-color",
                    "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)
        self.assertFalse((p / ".claude/commands/żółw.md").exists())


# ---------------------------------------------------------------------------
# i) ścieżki ze spacjami i polskimi znakami
# ---------------------------------------------------------------------------


class TestI_UnicodePaths(CleanerTest):

    def test_i01_home_w_katalogu_ze_spacja_i_ogonkami(self):
        home, proj = build_fixture(self.root / "zażółć gęślą")
        root = self.root / "zażółć gęślą"
        payload = list_json(home, proj)
        one_item(payload, category="Wykryte narzędzia (pakiety)", name="GSD (Get Shit Done)")
        before = snapshot(root)
        before_json = collect_json(root)
        bk = self.root / "kopia zapasowa ąę"
        run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                    "--select", "all", "--yes", "--no-color", "--backup-dir", _assert_safe(bk), expect_rc=0)
        self.assertTrue((bk / "manifest.json").is_file())
        run_cleaner("--restore", _assert_safe(bk), "--no-color", expect_rc=0)
        compare_trees(self, before, snapshot(root), root)
        json_equal_tree(self, root, before_json)


# ---------------------------------------------------------------------------
# j) wydajność
# ---------------------------------------------------------------------------


class TestJ_Performance(CleanerTest):

    def test_j01_2000_projektow_ponizej_20s(self):
        root = self.root / "perf"
        home = root / "home"
        _w(home / ".claude/settings.json", {})
        _w(home / ".claude.json", {})
        proj = root / "projects"
        for i in range(2000):
            d = proj / f"p{i:04d}"
            _w(d / "src/a.py", "x")
            _w(d / "node_modules" / f"pkg{i % 7}" / "deep/deeper/.mcp.json",
               {"mcpServers": {"NODEMODULESLEAK": {"command": "x"}}})
            if i % 100 == 0:
                _w(d / ".claude/settings.json", {"statusLine": {"type": "command", "command": "x"}})
        t0 = time.time()
        cp = run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                         "--list", "--json", expect_rc=0, timeout=120)
        elapsed = time.time() - t0
        payload = json.loads(cp.stdout)
        self.assertNotIn("NODEMODULESLEAK", cp.stdout, "skan wszedł do node_modules")
        self.assertEqual(20, len(find_item(payload, name="statusLine")), "nie wykryto wszystkich 20 projektów")
        self.assertLess(elapsed, 20.0, f"skan trwał {elapsed:.1f}s (limit 20s)")


# ---------------------------------------------------------------------------
# BŁĘDY — testy celowo czerwone (dokumentują realne usterki skryptu)
# ---------------------------------------------------------------------------


class TestZ_KnownBugs(CleanerTest):

    def test_bug01_hook_z_tym_samym_poleceniem_w_dwoch_matcherach(self):
        """BŁĄD: json_hook_remove dopasowuje hooki tylko po treści polecenia, ignorując matcher.
        Usunięcie hooka PreToolUse[Bash] kasuje też PreToolUse[Edit] o tym samym poleceniu."""
        root = self.root / "dup"
        home = root / "home"
        cd = home / ".claude"
        _w(home / ".claude.json", {"mcpServers": {}})
        _w(cd / "settings.json", {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo dup"}]},
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo dup"}]},
        ]}})
        payload = list_json(home)
        bash_hook = one_item(payload, category="Hooki (settings)", name="PreToolUse [Bash]")
        one_item(payload, category="Hooki (settings)", name="PreToolUse [Edit]")
        run_cleaner("--home", _assert_safe(home), "--no-projects", "--select", str(bash_hook["index"]),
                    "--yes", "--no-color", "--backup-dir", _assert_safe(self.backup_dir()), expect_rc=0)
        s = json.loads((cd / "settings.json").read_text(encoding="utf-8"))
        groups = s.get("hooks", {}).get("PreToolUse", [])
        matchers = [g.get("matcher") for g in groups]
        self.assertEqual(["Edit"], matchers,
                         "usunięto hook z matcherem Edit, choć wybrano tylko Bash (settings.json = %s)" % json.dumps(s))

    def test_bug02_uszkodzony_mcp_json_zglaszany_jako_pusty(self):
        """BŁĄD: .mcp.json z błędem składni jest opisany jako 'pusty / bez serwerów'
        i proponowany do skasowania w całości, choć zawiera konfigurację użytkownika."""
        root = self.root / "brokenmcp"
        home = root / "home"
        proj = root / "projects"
        _w(home / ".claude/settings.json", {})
        _w(home / ".claude.json", {"mcpServers": {}})
        (proj / "broken").mkdir(parents=True)
        (proj / "broken/.mcp.json").write_text('{"mcpServers": {"a": {"command": "x"}\n', encoding="utf-8")
        payload = list_json(home, proj)
        hits = find_item(payload, category="Projekt: broken", name=".mcp.json")
        if hits:
            self.assertNotIn("pusty", hits[0]["detail"],
                             "uszkodzony .mcp.json opisany jako pusty i zgłoszony do usunięcia: %s" % hits[0])

    def test_bug03_uszkodzone_json_bez_ostrzezenia(self):
        """BŁĄD/RYZYKO: uszkodzony settings.json i ~/.claude.json są po cichu traktowane
        jak puste — użytkownik nie dostaje żadnego ostrzeżenia, że część konfiguracji
        nie została przeanalizowana (a więc nie zostanie posprzątana)."""
        home, proj = self.fixture()
        (home / ".claude/settings.json").write_text("{ broken", encoding="utf-8")
        (home / ".claude.json").write_text("{ broken", encoding="utf-8")
        cp = run_cleaner("--home", _assert_safe(home), "--projects", _assert_safe(proj),
                         "--list", "--json", expect_rc=0)
        payload = json.loads(cp.stdout)
        notes = " ".join(payload["notes"]).lower()
        self.assertTrue(any(w in notes for w in ("uszkodz", "błąd", "blad", "nie udało")),
                        f"brak ostrzeżenia o uszkodzonych plikach JSON; notes={payload['notes']}")


# ---------------------------------------------------------------------------
# k) aplikacja Claude Desktop (claude_desktop_config.json, Claude Extensions)
# ---------------------------------------------------------------------------


DESKTOP_CFG = {
    "mcpServers": {
        "gemini-cli": {"command": "npx", "args": ["-y", "gemini-mcp-tool"]},
        "pal": {"command": "python", "args": ["server.py"]},
    },
    "coworkUserFilesPath": "X:\\Claude",
    "preferences": {"sidebarMode": "epitaxy"},
}


def build_desktop(home: Path) -> tuple:
    """Dwie kopie danych Claude Desktop jak na Windows (Roaming + MSIX LocalCache)."""
    roaming = home / "AppData/Roaming/Claude"
    msix = home / "AppData/Local/Packages/Claude_abc123/LocalCache/Roaming/Claude"
    _w(roaming / "claude_desktop_config.json", DESKTOP_CFG)
    _w(roaming / "config.json", {"locale": "pl"})
    _w(roaming / "Claude Extensions/ant.dir.acme.notes/manifest.json",
       {"name": "notes", "display_name": "Acme Notes", "version": "1.2.0", "description": "Notatki"})
    _w(roaming / "Claude Extensions/ant.dir.acme.notes/server/index.js", "//")
    _w(roaming / "Claude Extensions Settings/ant.dir.acme.notes.json", {"enabled": True})
    # kopia MSIX: ten sam gemini-cli, brak pal, to samo rozszerzenie
    _w(msix / "claude_desktop_config.json", {"mcpServers": {"gemini-cli": DESKTOP_CFG["mcpServers"]["gemini-cli"]}})
    _w(msix / "Claude Extensions/ant.dir.acme.notes/manifest.json", {"name": "notes"})
    return roaming, msix


class TestK_ClaudeDesktop(CleanerTest):

    def setUp(self):
        super().setUp()
        self.home, self.proj = self.fixture()
        self.roaming, self.msix = build_desktop(self.home)

    def test_k01_wykrywa_oba_katalogi_i_laczy_wpisy(self):
        payload = list_json(self.home)
        self.assertEqual(len(payload["desktop_dirs"]), 2, payload["desktop_dirs"])
        gem = one_item(payload, category="Serwery MCP (Claude Desktop)", name="gemini-cli")
        self.assertEqual(len(gem["actions"]), 2, "gemini-cli powinien mieć wpis z obu plików")
        self.assertTrue(all(a["kind"] == "json_del" and a["keys"] == ["mcpServers", "gemini-cli"] for a in gem["actions"]))
        pal = one_item(payload, category="Serwery MCP (Claude Desktop)", name="pal")
        self.assertEqual(len(pal["actions"]), 1)
        ext = one_item(payload, category="Rozszerzenia Claude Desktop", name="ant.dir.acme.notes")
        self.assertIn("Acme Notes", ext["detail"])
        self.assertIn("v1.2.0", ext["detail"])
        paths = [a["path"] for a in ext["actions"]]
        self.assertEqual(len(paths), 3, paths)  # 2 katalogi + plik ustawień
        self.assertTrue(any(p.endswith("ant.dir.acme.notes.json") for p in paths))
        # pliki rdzenia aplikacji nie są na liście
        self.assertFalse(find_item(payload, name_contains="config.json"))

    def test_k02_usuniecie_mcp_z_obu_plikow_zostawia_reszte(self):
        payload = list_json(self.home)
        gem = one_item(payload, category="Serwery MCP (Claude Desktop)", name="gemini-cli")
        bk = self.backup_dir()
        run_cleaner("--home", _assert_safe(self.home), "--no-projects", "--select", str(gem["index"]),
                    "--yes", "--no-color", "--backup-dir", _assert_safe(bk), expect_rc=0)
        r = json.loads((self.roaming / "claude_desktop_config.json").read_text(encoding="utf-8"))
        m = json.loads((self.msix / "claude_desktop_config.json").read_text(encoding="utf-8"))
        self.assertNotIn("gemini-cli", r["mcpServers"])
        self.assertIn("pal", r["mcpServers"])
        self.assertEqual(r["preferences"], {"sidebarMode": "epitaxy"})
        self.assertEqual(r["coworkUserFilesPath"], "X:\\Claude")
        self.assertEqual(m["mcpServers"], {})
        self.assertTrue((self.msix / "claude_desktop_config.json").exists(), "plik konfiguracyjny nie może zniknąć")
        mf = json.loads((bk / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(mf["desktop_dirs"]), 2)
        self.assertEqual(len(mf["json_backups"]), 2)
        run_cleaner("--restore", _assert_safe(bk), "--no-color", expect_rc=0)
        r2 = json.loads((self.roaming / "claude_desktop_config.json").read_text(encoding="utf-8"))
        self.assertIn("gemini-cli", r2["mcpServers"])

    def test_k03_usuniecie_rozszerzenia(self):
        payload = list_json(self.home)
        ext = one_item(payload, category="Rozszerzenia Claude Desktop", name="ant.dir.acme.notes")
        bk = self.backup_dir()
        run_cleaner("--home", _assert_safe(self.home), "--no-projects", "--select", str(ext["index"]),
                    "--yes", "--no-color", "--backup-dir", _assert_safe(bk), expect_rc=0)
        self.assertFalse((self.roaming / "Claude Extensions/ant.dir.acme.notes").exists())
        self.assertFalse((self.msix / "Claude Extensions/ant.dir.acme.notes").exists())
        self.assertFalse((self.roaming / "Claude Extensions Settings/ant.dir.acme.notes.json").exists())
        self.assertTrue((self.roaming / "Claude Extensions").is_dir())
        self.assertTrue((self.roaming / "config.json").exists())
        run_cleaner("--restore", _assert_safe(bk), "--no-color", expect_rc=0)
        self.assertTrue((self.roaming / "Claude Extensions/ant.dir.acme.notes/manifest.json").exists())

    def test_k04_guard_nie_usunie_katalogu_desktop(self):
        cp = run_cleaner("--home", _assert_safe(self.home), "--no-projects", "--select", "all", "--yes", "--no-color",
                         "--backup-dir", _assert_safe(self.backup_dir()))
        self.assertIn(cp.returncode, (0, 1))
        self.assertTrue(self.roaming.is_dir())
        self.assertTrue(self.msix.is_dir())
        self.assertTrue((self.roaming / "config.json").exists())

    def test_k05_desktop_bez_claude_code(self):
        home = self.root / "only-desktop"
        build_desktop(home)
        payload = list_json(home)
        self.assertTrue(find_item(payload, category="Serwery MCP (Claude Desktop)", name="pal"))
        self.assertTrue(any("Nie znaleziono katalogu Claude Code" in n for n in payload["notes"]))


# ---------------------------------------------------------------------------
# l) potwierdzenie: dwa razy Enter
# ---------------------------------------------------------------------------


class TestL_DoubleEnter(CleanerTest):

    def _prep(self):
        home, proj = self.fixture()
        payload = list_json(home)
        it = one_item(payload, category="Serwery MCP (globalne)", name="pal")
        return home, it

    def _run(self, home, it, stdin_text):
        return run_cleaner("--home", _assert_safe(home), "--no-projects", "--select", str(it["index"]),
                           "--no-color", "--backup-dir", _assert_safe(self.backup_dir()), stdin_text=stdin_text)

    def test_l01_dwa_entery_potwierdzaja(self):
        home, it = self._prep()
        cp = self._run(home, it, "\n\n")
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        self.assertIn("Wykonano", cp.stdout)
        cj = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        self.assertNotIn("pal", cj.get("mcpServers", {}))

    def test_l02_jeden_enter_anuluje(self):
        home, it = self._prep()
        cp = self._run(home, it, "\n")
        self.assertEqual(cp.returncode, 0)
        self.assertIn("Anulowano", cp.stdout)
        cj = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        self.assertIn("pal", cj["mcpServers"])

    def test_l03_tak_juz_nie_potwierdza(self):
        home, it = self._prep()
        for text in ("tak\n", "tak\n\n", "\nx\n", "y\n\n"):
            cp = self._run(home, it, text)
            self.assertIn("Anulowano", cp.stdout, repr(text))
        cj = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        self.assertIn("pal", cj["mcpServers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
