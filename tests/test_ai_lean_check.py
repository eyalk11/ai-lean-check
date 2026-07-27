import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "ai_lean_check.py"
SPEC = importlib.util.spec_from_file_location("ai_lean_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class AILeanCheckTests(unittest.TestCase):
    def test_openai_payload_uses_strict_schema(self):
        with patch.dict(os.environ, {"AI_LEAN_MODEL": "test-model"}):
            payload = MODULE.openai_payload("prompt")
        self.assertEqual(payload["model"], "test-model")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(
            payload["text"]["format"]["schema"]["required"], ["lean_code"]
        )

    def test_provider_default_models(self):
        with patch.dict(os.environ, {"AI_LEAN_MODEL": ""}):
            self.assertEqual(MODULE.selected_model("github"), "openai/gpt-4o")
            self.assertEqual(MODULE.selected_model("xai"), "grok-4.5")

    def test_output_token_limit_is_forwarded(self):
        with patch.dict(
            os.environ,
            {"AI_LEAN_MODEL": "test-model", "AI_LEAN_MAX_OUTPUT_TOKENS": "1234"},
        ):
            self.assertEqual(MODULE.openai_payload("prompt")["max_output_tokens"], 1234)
            self.assertEqual(MODULE.chat_payload("github", "prompt")["max_tokens"], 1234)
            self.assertEqual(MODULE.anthropic_payload("prompt")["max_tokens"], 1234)

    def test_default_output_token_limit(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(MODULE.max_output_tokens(), 32768)

    def test_chat_payload_uses_strict_schema(self):
        with patch.dict(os.environ, {"AI_LEAN_MODEL": "test-model"}):
            payload = MODULE.chat_payload("github", "prompt")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])

    def test_anthropic_payload_uses_current_output_config(self):
        with patch.dict(os.environ, {"AI_LEAN_MODEL": "test-model"}):
            payload = MODULE.anthropic_payload("prompt")
        self.assertEqual(
            payload["output_config"]["format"]["type"], "json_schema"
        )

    def test_extract_output_text(self):
        response = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": json.dumps({"lean_code": "#check Nat"})}
                    ]
                }
            ]
        }
        self.assertIn("lean_code", MODULE.extract_output_text(response))

    def test_validation_rejects_escape_hatches(self):
        for snippet in (
            "example : True := by sorry",
            "run_cmd IO.println \"bad\"",
            "#eval System.FilePath.pathExists \".\"",
            "axiom invented : False",
        ):
            with self.subTest(snippet=snippet):
                self.assertTrue(MODULE.validate(snippet))

    def test_validation_requires_imports(self):
        with patch.dict(os.environ, {"AI_LEAN_IMPORTS": "Mathlib\nMyProject"}):
            problems = MODULE.validate("import Mathlib\n#check Nat")
        self.assertEqual(problems, ["missing required import: MyProject"])

    def test_dependency_file_pattern(self):
        patterns = ["**/*_deps.lean"]
        self.assertTrue(MODULE.path_allowed("Lean/theorem_3_11_deps.lean", patterns))
        self.assertFalse(MODULE.path_allowed("Lean/theorem_3_11.lean", patterns))

    def test_input_token_limit_uses_conservative_estimate(self):
        with patch.dict(os.environ, {"AI_LEAN_MAX_INPUT_TOKENS": "100"}):
            self.assertEqual(MODULE.context_byte_limit(), 400)

    def test_context_collection_deduplicates_files(self):
        with tempfile.TemporaryDirectory() as directory:
            original = os.getcwd()
            os.chdir(directory)
            try:
                Path("A.lean").write_text("#check Nat\n", encoding="utf-8")
                context = MODULE.collect_context(["*.lean", "A.lean"])
            finally:
                os.chdir(original)
        self.assertEqual(context.count("===== A.lean ====="), 1)


if __name__ == "__main__":
    unittest.main()
