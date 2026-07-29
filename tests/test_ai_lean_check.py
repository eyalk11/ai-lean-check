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

    def test_placeholder_scanner_ignores_comments_and_strings(self):
        source = '''-- sorry
/- outer admit /- nested sorry -/ done -/
def message := "sorry and admit"
example : True := by
  trivial
'''
        cleaned = MODULE.lean_code_without_comments_or_strings(source)
        self.assertNotRegex(cleaned, r"\b(?:sorry|admit)\b")

    def test_placeholder_scanner_preserves_code(self):
        source = "example : True := by\n  sorry\n"
        cleaned = MODULE.lean_code_without_comments_or_strings(source)
        self.assertRegex(cleaned, r"\bsorry\b")

    def test_generated_path_validation(self):
        self.assertTrue(MODULE.safe_generated_path("lean/generated/proof_1.lean"))
        self.assertFalse(MODULE.safe_generated_path("../escape.lean"))
        self.assertFalse(MODULE.safe_generated_path(".git/hooks/pre-commit.lean"))
        self.assertFalse(MODULE.safe_generated_path("lean/bad\noutput.lean"))

    def test_input_token_limit_uses_conservative_estimate(self):
        with patch.dict(os.environ, {"AI_LEAN_MAX_INPUT_TOKENS": "100"}):
            self.assertEqual(MODULE.context_byte_limit(), 400)

    def test_agent_prompt_requires_both_lean_checks(self):
        with patch.dict(
            os.environ,
            {
                "AI_LEAN_OUTPUT_FILE": ".ai-lean-check/Test.lean",
                "AI_LEAN_IMPORTS": "MyProject",
            },
        ):
            prompt = MODULE.build_agent_prompt("diff", "context")
        self.assertIn("run-lean-sanitized.sh check <file>", prompt)
        self.assertIn("run-lean-sanitized.sh build", prompt)
        self.assertIn("import MyProject", prompt)

    def test_agent_prompt_lists_multiple_target_files(self):
        with patch.dict(
            os.environ,
            {
                "AI_LEAN_TARGET_FILES": "Lean/GeneratedA.lean\nLean/GeneratedB.lean",
            },
        ):
            prompt = MODULE.build_agent_prompt("diff", "context")
        self.assertIn("`Lean/GeneratedA.lean`", prompt)
        self.assertIn("`Lean/GeneratedB.lean`", prompt)

    def test_agent_prompt_can_choose_filenames(self):
        with patch.dict(
            os.environ,
            {"AI_LEAN_TARGET_FILES": "", "AI_LEAN_OUTPUT_FILE": ""},
        ):
            prompt = MODULE.build_agent_prompt("diff", "context")
        self.assertIn("Choose clear project-relative", prompt)

    def test_sanitized_environment_removes_agent_credentials(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_CODE_OAUTH_TOKEN": "secret",
                "OPENAI_API_KEY": "secret",
                "GITHUB_TOKEN": "secret",
                "GH_TOKEN": "secret",
                "ACTIONS_RUNTIME_TOKEN": "secret",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "secret",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "secret",
                "SAFE_VALUE": "kept",
            },
        ):
            sanitized = MODULE.sanitized_process_env()
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", sanitized)
        self.assertNotIn("OPENAI_API_KEY", sanitized)
        self.assertNotIn("GITHUB_TOKEN", sanitized)
        self.assertNotIn("GH_TOKEN", sanitized)
        self.assertNotIn("ACTIONS_RUNTIME_TOKEN", sanitized)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", sanitized)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST_URL", sanitized)
        self.assertEqual(sanitized["SAFE_VALUE"], "kept")

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

    def _declared_check_files(self, contents, generated):
        with tempfile.TemporaryDirectory() as directory:
            original = os.getcwd()
            os.chdir(directory)
            try:
                if contents is not None:
                    Path(".ai-lean-check").mkdir()
                    Path(".ai-lean-check/check-files.txt").write_text(
                        contents, encoding="utf-8"
                    )
                return MODULE.declared_check_files(generated)
            finally:
                os.chdir(original)

    def test_no_declaration_checks_every_generated_file(self):
        requested, rejected = self._declared_check_files(None, ["lean/A.lean"])
        self.assertEqual((requested, rejected), ([], []))

    def test_declaration_selects_named_generated_files(self):
        requested, rejected = self._declared_check_files(
            "lean/B.lean\n\n  lean/A.lean  \n", ["lean/A.lean", "lean/B.lean"]
        )
        self.assertEqual(requested, ["lean/B.lean", "lean/A.lean"])
        self.assertEqual(rejected, [])

    def test_declaration_rejects_files_the_agent_did_not_generate(self):
        requested, rejected = self._declared_check_files(
            "lean/A.lean\nlean/pre_existing.lean\n../etc/passwd.lean\n",
            ["lean/A.lean"],
        )
        self.assertEqual(requested, ["lean/A.lean"])
        self.assertEqual(rejected, ["lean/pre_existing.lean", "../etc/passwd.lean"])


if __name__ == "__main__":
    unittest.main()


class LeanDeclarationsTest(unittest.TestCase):
    def test_tracks_namespaces_and_sections(self) -> None:
        source = (
            "namespace FEI.Bd\n"
            "theorem alpha (x : Nat) : x = x := rfl\n"
            "section\n"
            "lemma beta : True := trivial\n"
            "end\n"
            "end FEI.Bd\n"
            "theorem gamma : True := trivial\n"
        )
        self.assertEqual(
            MODULE.lean_declarations(source),
            ["FEI.Bd.alpha", "FEI.Bd.beta", "gamma"],
        )

    def test_ignores_comments_and_defs(self) -> None:
        source = (
            "/-- theorem ghost : False := by sorry -/\n"
            "def helper : Nat := 0\n"
            "protected theorem real : True := trivial\n"
        )
        self.assertEqual(MODULE.lean_declarations(source), ["real"])

    def test_named_end_pops_to_matching_opener(self) -> None:
        source = (
            "namespace Outer\n"
            "namespace Inner\n"
            "end Inner\n"
            "theorem here : True := trivial\n"
            "end Outer\n"
        )
        self.assertEqual(MODULE.lean_declarations(source), ["Outer.here"])


class EditPolicyTest(unittest.TestCase):
    PORCELAIN = (
        " M lean/foo.lean\n"
        " M lakefile.toml\n"
        " M README.md\n"
        " D lean/gone.lean\n"
    )

    def test_edit_mode_allows_lean_and_mapping(self) -> None:
        with patch.dict(os.environ, {"AI_LEAN_EDIT_POLICY": "edit"}, clear=False):
            modified, rejected = MODULE.classify_tracked_changes(self.PORCELAIN)
        self.assertEqual(modified, ["lean/foo.lean", "lakefile.toml"])
        self.assertTrue(any("README.md" in r for r in rejected))
        self.assertTrue(any("deletion" in r for r in rejected))

    def test_add_only_still_allows_project_mapping(self) -> None:
        with patch.dict(os.environ, {"AI_LEAN_EDIT_POLICY": "add-only"}, clear=False):
            modified, rejected = MODULE.classify_tracked_changes(self.PORCELAIN)
        self.assertEqual(modified, ["lakefile.toml"])
        self.assertTrue(any("lean/foo.lean" in r and "add-only" in r for r in rejected))

    def test_renames_rejected(self) -> None:
        with patch.dict(os.environ, {"AI_LEAN_EDIT_POLICY": "edit"}, clear=False):
            modified, rejected = MODULE.classify_tracked_changes("R  a.lean -> b.lean\n")
        self.assertEqual(modified, [])
        self.assertTrue(any("rename" in r for r in rejected))

    def test_extra_mapping_glob_is_honoured(self) -> None:
        env = {
            "AI_LEAN_EDIT_POLICY": "add-only",
            "AI_LEAN_PROJECT_MAPPING_FILES": "lakefile.toml\nlean/FEI.lean",
        }
        with patch.dict(os.environ, env, clear=False):
            modified, _ = MODULE.classify_tracked_changes(" M lean/FEI.lean\n")
        self.assertEqual(modified, ["lean/FEI.lean"])
