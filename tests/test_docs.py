"""The documentation promises commands and examples. These tests keep those promises true."""

import argparse
import json
import re
import unittest
from pathlib import Path

from money_mom.cli import _build_parser
from money_mom.importing import parse_mapping

from test_cli import CliTestCase

ROOT = Path(__file__).resolve().parent.parent
FULL_CHECKOUT = (ROOT / "site" / "index.html").is_file() and (ROOT / "npm" / "package.json").is_file()
full_checkout_only = unittest.skipUnless(FULL_CHECKOUT, "needs the whole repository, not just a source distribution")
DOCS = [
    ROOT / "skills/money-mom/SKILL.md",
    ROOT / "skills/money-mom/references/commands.md",
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "INSTALL.md",
]


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def code_text(path: Path) -> list[str]:
    """Every line of a fenced block and every inline `code span` of a markdown file."""
    text = path.read_text(encoding="utf-8")
    fenced = re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    without = re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.S)
    lines = [line for block in fenced for line in block.splitlines()]
    return lines + re.findall(r"`([^`\n]+)`", without)


class CommandsExist(unittest.TestCase):
    def test_every_command_the_docs_use_is_real(self):
        top = subcommands(_build_parser())
        checked = 0
        for path in DOCS:
            for line in code_text(path):
                for match in re.finditer(r"(?<![\w/-])money-mom\s+((?:--?[\w-]+(?:\s+(?!import\b)[^\s-][^\s]*)?\s+)*)([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?", line):
                    word, nxt = match.group(2), match.group(3)
                    if word in {"is", "command", "skill", "engine", "to", "the", "for", "and", "or"}:
                        continue
                    self.assertIn(word, top, f"{path.name}: `{line.strip()[:80]}` uses an unknown command {word!r}")
                    checked += 1
                    nested = subcommands(top[word]) if any(isinstance(a, argparse._SubParsersAction) for a in top[word]._actions) else {}
                    if nested and nxt and not nxt.startswith("-"):
                        if nxt in nested or word in ("alias", "rates", "import"):
                            self.assertIn(nxt, nested, f"{path.name}: `{line.strip()[:80]}`: {word} has no action {nxt!r}")
        self.assertGreater(checked, 60)  # the scan really looked at the documents


class CommandReferenceTable(unittest.TestCase):
    """references/commands.md lists commands without the `money-mom` prefix, so it needs its own scan."""

    def test_every_row_names_a_real_command_and_action(self):
        top = subcommands(_build_parser())
        path = ROOT / "skills/money-mom/references/commands.md"
        rows = [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("| `")]
        self.assertGreater(len(rows), 25)
        for line in rows:
            first_cell = re.split(r"(?<!\\)\|", line)[1]
            for span in re.findall(r"`([^`]+)`", first_cell):
                words = span.replace("\\|", "|").split()
                if words[0].startswith("-"):
                    continue  # the first table lists global options, not commands
                self.assertTrue(words[0] in top, f"commands.md: `{span}`: unknown command {words[0]!r}")
                nested = [a for a in top[words[0]]._actions if isinstance(a, argparse._SubParsersAction)]
                if nested:
                    self.assertTrue(words[1] in nested[0].choices, f"commands.md: `{span}`: {words[0]} has no action {words[1]!r}")


class Examples(CliTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.js("init", "--template", "cn", "--date", "2026-01-01")

    def block(self, path: Path, language: str, containing: str) -> str:
        text = path.read_text(encoding="utf-8")
        for body in re.findall(rf"```{language}\n(.*?)```", text, re.S):
            if containing in body:
                return body
        raise AssertionError(f"no {language} block containing {containing!r} in {path.name}")

    def test_the_mapping_in_skill_md_is_a_valid_mapping(self):
        toml = self.block(ROOT / "skills/money-mom/SKILL.md", "toml", "[columns]")
        mapping = parse_mapping(toml, "from-docs")
        self.assertEqual((mapping.sign, mapping.columns["balance"], mapping.direction_ignore), ("unsigned", "余额", ("不计收支",)))
        saved = self.js("import", "save-map", "from-docs", "-", stdin=toml)
        self.assertTrue(saved["ok"])

    def test_the_batch_example_in_skill_md_runs(self):
        body = self.block(ROOT / "skills/money-mom/SKILL.md", "bash", "add --from-json")
        payload = body.split("<<'EOF'\n", 1)[1].rsplit("EOF", 1)[0]
        json.loads(payload)  # valid JSON
        data = self.js("add", "--from-json", "-", "--actor", "agent:claude-code", stdin=payload)["data"]
        self.assertEqual([d["status"] for d in data], ["posted"])
        again = self.js("add", "--from-json", "-", "--actor", "agent:claude-code", stdin=payload, expect=1)
        self.assertEqual(again["error"]["code"], "duplicate_import")  # the example's import_hash really guards against repeats

    def test_the_mapping_example_in_the_import_section_covers_the_documented_keys(self):
        toml = self.block(ROOT / "skills/money-mom/SKILL.md", "toml", "[columns]")
        for key in ("[[skip]]", "[direction]", "[format]", "id =", "balance ="):
            self.assertIn(key, toml)


class Consistency(unittest.TestCase):
    def test_error_codes_in_the_skill_are_ones_the_program_can_raise(self):
        skill = (ROOT / "skills/money-mom/SKILL.md").read_text(encoding="utf-8")
        source = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "src/money_mom").glob("*.py"))
        table = skill.split("## 7. When a command is refused", 1)[1].split("## Voice", 1)[0]
        for code in re.findall(r"`([a-z_]+)`", "\n".join(l.split("|")[1] for l in table.splitlines() if l.startswith("| `"))):
            self.assertRegex(source, rf"[\"']{code}[\"']", f"SKILL.md documents the code {code!r} but no code raises it")

    @full_checkout_only
    def test_the_version_is_the_same_everywhere(self):
        from money_mom import __version__
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', pyproject)
        self.assertIn(f'version: "{__version__}"', (ROOT / "skills/money-mom/SKILL.md").read_text(encoding="utf-8"))
        self.assertIn(f"money_mom-{__version__}-py3-none-any.whl", (ROOT / "INSTALL.md").read_text(encoding="utf-8"))
        for name in ("INSTALL.md", "README.md", "README.en.md", "README.pypi.md", "npm/README.md", "site/index.html"):
            text = (ROOT / name).read_text(encoding="utf-8")
            stray = {v for v in re.findall(r"\b0\.\d+\.\d+(?:a|b|rc)\d+\b", text) if v != __version__}
            self.assertEqual(stray, set(), f"{name} still mentions another pre-release version")


@full_checkout_only
class Packaging(unittest.TestCase):
    def test_the_npm_package_pins_this_exact_engine(self):
        from money_mom import __version__
        pkg = json.loads((ROOT / "npm/package.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["moneyMomVersion"], __version__)
        match = re.fullmatch(r"(\d+\.\d+\.\d+)-(alpha|beta|rc)\.(\d+)", pkg["version"])
        self.assertIsNotNone(match, "an npm pre-release looks like 0.1.0-alpha.4")
        letter = {"alpha": "a", "beta": "b", "rc": "rc"}[match.group(2)]
        self.assertEqual(f"{match.group(1)}{letter}{match.group(3)}", __version__)

    def test_the_pypi_description_renders_on_pypi(self):
        from money_mom import __version__
        text = (ROOT / "README.pypi.md").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"<(picture|img|p |div)", "PyPI strips raw HTML")
        self.assertEqual(re.findall(r"\]\((?!https?://)[^)]*\)", text), [], "every link on PyPI must be absolute")
        self.assertIn(f'"money-mom=={__version__}"', text)
        self.assertEqual(re.findall(r"\]\((?!https?://)[^)]*\)", (ROOT / "npm/README.md").read_text(encoding="utf-8")), [])

    def test_there_are_release_notes_for_this_version(self):
        from money_mom import __version__
        notes = ROOT / "docs/release-notes" / f"v{__version__}.md"
        self.assertTrue(notes.is_file(), f"the release workflow publishes {notes.name}")
        text = notes.read_text(encoding="utf-8")
        self.assertIn(f"money_mom-{__version__}-py3-none-any.whl", text)
        self.assertFalse("__WHEEL_SHA256__" in text, "the checksum must be filled in before releasing")

    def test_the_license_is_in_every_package(self):
        for path in ("LICENSE", "npm/LICENSE"):
            self.assertEqual((ROOT / path).read_text(encoding="utf-8"), (ROOT / "LICENSE").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
