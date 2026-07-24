"""Frontmatter and structure validation for the codex-retrospective skill."""

import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SKILL_DIR = REPO_ROOT / "skill" / "codex-retrospective"


class SkillFrontmatterTest(unittest.TestCase):
    """Validate SKILL.md frontmatter matches repo expectations."""

    def setUp(self) -> None:
        self.skill_md = SKILL_DIR / "SKILL.md"
        if not self.skill_md.exists():
            self.skipTest(f"{self.skill_md} not found — not in a skill directory")

    def _load_frontmatter(self) -> dict:
        text = self.skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError("SKILL.md does not start with frontmatter")
        parts = text.split("---", 2)
        # Parse simple YAML without PyYAML dependency
        meta: dict = {}
        for line in parts[1].strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                meta[key] = value
        return meta

    def test_name_is_codex_retrospective(self) -> None:
        """Frontmatter name must match the skill directory name."""
        meta = self._load_frontmatter()
        self.assertEqual("codex-retrospective", meta.get("name"))

    def test_description_is_non_empty(self) -> None:
        """Frontmatter must have a non-empty description."""
        meta = self._load_frontmatter()
        desc = meta.get("description", "")
        self.assertTrue(desc, "description is empty or missing")

    def test_folder_name_matches(self) -> None:
        """The skill directory basename must match the frontmatter name."""
        meta = self._load_frontmatter()
        folder_name = SKILL_DIR.name
        self.assertEqual(folder_name, meta.get("name"))

    def test_agents_openai_yaml_exists(self) -> None:
        """agents/openai.yaml must exist alongside SKILL.md."""
        agent_yaml = SKILL_DIR / "agents" / "openai.yaml"
        self.assertTrue(agent_yaml.exists(), f"Missing {agent_yaml}")


if __name__ == "__main__":
    unittest.main()
