"""Published agent texts match the packaged source, and nothing is hidden."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from connectthebots_server import agent_texts


def _generator():
    spec = importlib.util.spec_from_file_location("sync_agent_texts", ROOT / "site" / "tools" / "sync_agent_texts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentTextSiteTests(unittest.TestCase):
    def test_prompt_viewer_agents_json_and_llms_txt_are_current(self):
        for name, content in _generator().render().items():
            with self.subTest(file=name):
                self.assertEqual((ROOT / "site" / name).read_text(encoding="utf-8"), content,
                                 "Run python3 site/tools/sync_agent_texts.py")

    def test_viewer_shows_every_text_and_its_hash_without_scripts(self):
        page = (ROOT / "site" / "prompts.html").read_text(encoding="utf-8")
        self.assertNotIn("<script", page)
        for name, item in agent_texts.catalogue()["texts"].items():
            self.assertIn(f'id="{name}"', page)
            self.assertIn(item["sha256"], page)
        self.assertIn('id="onboarding"', page)

    def test_agents_json_lists_real_routes_and_the_approval_rule(self):
        manifest = json.loads((ROOT / "site" / "agents.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["texts"]["version"], agent_texts.VERSION)
        self.assertTrue(any("approval" in rule for rule in manifest["deal"]["rules"]))
        if importlib.util.find_spec("starlette") is None:
            self.skipTest("optional server extra not installed")
        from connectthebots_server.app import create_app
        from connectthebots_server.config import Settings
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_env({"APP_ENV": "staging", "PUBLIC_BASE_URL": "https://catalogue.example.test",
                "DATABASE_PATH": str(Path(directory) / "catalogue.db"), "SESSION_SECRET": "fictional-route-catalogue-" + "x" * 40})
            app = create_app(settings)
            registered = {(method, route.path) for route in app.router.routes for method in (getattr(route, "methods", None) or ())}
            for endpoint in manifest["api"]["endpoints"]:
                with self.subTest(endpoint=endpoint["path"]):
                    self.assertIn((endpoint["method"], endpoint["path"]), registered)

    def test_texts_keep_the_core_promises(self):
        texts = {name: text for name, (_, text) in agent_texts.TEXTS.items()}
        self.assertIn("approval before sharing their code", texts["deals"])
        self.assertIn("no hidden instructions", texts["deals"].lower())
        self.assertIn("Politics starts as ask after separate processing consent", texts["interview_guide"])
        self.assertIn("omit them from saved answers or skip that topic", texts["interview_guide"])
        self.assertIn("visibility choices, not permission to store", texts["interview_guide"])
        self.assertIn("No links, emails, phone numbers", texts["social_board_rules"])
        self.assertIn("never\n  the number of posts", texts["board_rules"])
        self.assertIn("deals.html", (ROOT / "site" / "about.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
