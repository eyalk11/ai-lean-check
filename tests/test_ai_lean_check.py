import importlib.util
import json
import os
from pathlib import Path
import sys
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


class PorcelainParsingTest(unittest.TestCase):
    def test_all_status_paddings_keep_the_full_path(self) -> None:
        cases = [
            " M lean/foo.lean",   # modified, unstaged
            "M  lean/foo.lean",   # modified, staged
            "MM lean/foo.lean",   # staged and modified again
            "A  lean/foo.lean",   # added and staged
            " M  lean/foo.lean",  # extra padding
        ]
        with patch.dict(os.environ, {"AI_LEAN_EDIT_POLICY": "edit"}, clear=False):
            for line in cases:
                modified, rejected = MODULE.classify_tracked_changes(line + "\n")
                self.assertEqual(modified, ["lean/foo.lean"], msg=repr(line))
                self.assertEqual(rejected, [], msg=repr(line))


PREPARE = Path(__file__).parents[1] / "scripts" / "prepare_agent.py"
# prepare_agent.py imports its sibling the way the action runs it, with the
# scripts directory on sys.path.
sys.path.insert(0, str(PREPARE.parent))
PREPARE_SPEC = importlib.util.spec_from_file_location("prepare_agent", PREPARE)
PREPARE_MODULE = importlib.util.module_from_spec(PREPARE_SPEC)
assert PREPARE_SPEC.loader
PREPARE_SPEC.loader.exec_module(PREPARE_MODULE)


class AgentPromptPolicyTests(unittest.TestCase):
    def test_turn_limit_is_stated_to_the_agent(self) -> None:
        with patch.dict(os.environ, {"AI_LEAN_AGENT_MAX_TURNS": "30"}, clear=False):
            prompt = PREPARE_MODULE.build_prompt("base", "head")
        self.assertIn("## Turn limit", prompt)
        self.assertIn("You have 30 turns", prompt)

    def test_turn_limit_is_omitted_when_unset_or_invalid(self) -> None:
        for value in ("0", "", "not-a-number"):
            with self.subTest(value=value):
                with patch.dict(
                    os.environ, {"AI_LEAN_AGENT_MAX_TURNS": value}, clear=False
                ):
                    self.assertEqual(PREPARE_MODULE.turns_block(), "")
                    self.assertNotIn(
                        "## Turn limit", PREPARE_MODULE.build_prompt("base", "head")
                    )

    def test_edit_policy_forbids_non_mathematical_fixes(self) -> None:
        with patch.dict(os.environ, {"AI_LEAN_EDIT_POLICY": "edit"}, clear=False):
            prompt = PREPARE_MODULE.build_prompt("base", "head")
        self.assertIn("essential to the mathematics", prompt)
        self.assertIn("not mathematically necessary", prompt)


class ClaudeSandboxConfigurationTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]

    def test_action_uses_supported_main_seam(self) -> None:
        action = (self.ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn("anthropics/claude-code-base-action@main", action)
        self.assertIn("path_to_claude_code_executable:", action)
        self.assertIn("claude-bwrap.sh", action)
        self.assertIn("claude_args:", action)
        self.assertIn("--setting-sources user", action)
        self.assertNotIn("claude-code-base-action@beta", action)
        self.assertNotIn("\n        max_turns:", action)
        self.assertNotIn("\n        allowed_tools:", action)

    def test_action_blanks_github_actions_credentials(self) -> None:
        action = (self.ROOT / "action.yml").read_text(encoding="utf-8")
        for name in (
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "ACTIONS_RUNTIME_TOKEN",
            "ACTIONS_CACHE_URL",
            "ACTIONS_RESULTS_URL",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            "ACTIONS_ID_TOKEN_REQUEST_URL",
        ):
            self.assertIn(f'{name}: ""', action)

    def test_wrapper_enforces_requested_boundaries(self) -> None:
        wrapper = (self.ROOT / "scripts" / "claude-bwrap.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--unshare-pid", wrapper)
        self.assertIn("--clearenv", wrapper)
        self.assertIn('--ro-bind "$workspace/.git" "$workspace/.git"', wrapper)
        self.assertIn('--tmpfs "$actions_root"', wrapper)
        self.assertIn('--bind "$workspace" "$workspace"', wrapper)
        self.assertIn('timeout --foreground --kill-after=10 "$timeout_seconds"', wrapper)

    def test_sandbox_timeout_scales_with_the_turn_budget(self) -> None:
        """A fixed timeout would silently cap a raised agent-max-turns.

        At the observed rate of well under a minute per turn, a hardcoded 30
        minutes hard-kills a 120-turn run around turn 47 instead of letting
        max_turns end it cleanly.
        """
        wrapper = (self.ROOT / "scripts" / "claude-bwrap.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AI_LEAN_AGENT_MAX_TURNS", wrapper)
        self.assertIn("timeout_seconds=$(( turns * 60 ))", wrapper)
        self.assertIn("timeout_seconds < 1800", wrapper)
        action = (self.ROOT / "action.yml").read_text(encoding="utf-8")
        # The wrapper can only scale if the agent step actually exports it.
        self.assertIn(
            "AI_LEAN_AGENT_MAX_TURNS: ${{ inputs.agent-max-turns }}", action
        )

    def test_root_bind_precedes_proc_and_dev(self) -> None:
        """The ordering is the isolation.

        bwrap applies mounts in sequence with recursive binds, so a `--ro-bind
        / /` placed after `--proc` silently replaces the sandbox procfs with the
        host's and PID hiding stops working. Asserting the flags merely exist
        cannot see that, so assert their order.
        """
        for script in ("claude-bwrap.sh", "setup_claude_sandbox.sh"):
            with self.subTest(script=script):
                text = (self.ROOT / "scripts" / script).read_text(encoding="utf-8")
                root = text.index("--ro-bind / /")
                self.assertLess(root, text.index("--proc /proc"))
                self.assertLess(root, text.index("--dev /dev"))
                self.assertNotIn("--dev-bind /dev /dev", text)

    def test_toolchain_lookup_survives_the_home_redirect(self) -> None:
        wrapper = (self.ROOT / "scripts" / "claude-bwrap.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('--setenv ELAN_HOME "$HOME/.elan"', wrapper)
        # Paths must derive from the real HOME, not a hardcoded runner account.
        self.assertNotIn("/home/runner", wrapper)

    def test_preflight_verifies_isolation_not_just_exit_status(self) -> None:
        setup = (self.ROOT / "scripts" / "setup_claude_sandbox.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("visible_pids", setup)
        self.assertIn("PID isolation is not in effect", setup)


STATUSLINE = Path(__file__).parents[1] / "scripts" / "token_usage_statusline.py"
STATUSLINE_SPEC = importlib.util.spec_from_file_location(
    "token_usage_statusline", STATUSLINE
)
STATUSLINE_MODULE = importlib.util.module_from_spec(STATUSLINE_SPEC)
assert STATUSLINE_SPEC.loader
STATUSLINE_SPEC.loader.exec_module(STATUSLINE_MODULE)


class TokenUsageStatusLineTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]

    def _transcript(self, directory: str) -> str:
        path = Path(directory) / "transcript.jsonl"
        path.write_text(
            json.dumps(
                {
                    "message": {
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "cache_creation_input_tokens": 10,
                            "cache_read_input_tokens": 1000,
                        }
                    }
                }
            )
            + "\n"
            + json.dumps({"message": {"content": "no usage block"}})
            + "\nnot json at all\n"
            + json.dumps(
                {"message": {"usage": {"input_tokens": 200, "output_tokens": 80}}}
            )
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    def test_usage_is_summed_cumulatively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            usage = STATUSLINE_MODULE.sum_usage(self._transcript(directory))
        self.assertEqual(usage["input"], 300)
        self.assertEqual(usage["output"], 130)
        self.assertEqual(usage["cache_write"], 10)
        self.assertEqual(usage["cache_read"], 1000)
        self.assertEqual(usage["total"], 1440)

    def test_missing_or_malformed_transcript_yields_zero(self) -> None:
        self.assertEqual(STATUSLINE_MODULE.sum_usage("")["total"], 0)
        self.assertEqual(
            STATUSLINE_MODULE.sum_usage("/nonexistent/x.jsonl")["total"], 0
        )

    def test_it_reports_only_and_never_enforces(self) -> None:
        """Reporting only: no budget, no warning, and nothing that kills."""
        source = STATUSLINE.read_text(encoding="utf-8")
        for forbidden in ("SIGTERM", "os.kill", "budget", "::error", "::warning"):
            self.assertNotIn(forbidden, source)

    def test_wrapper_stages_the_status_line_into_the_sandbox(self) -> None:
        wrapper = (self.ROOT / "scripts" / "claude-bwrap.sh").read_text(
            encoding="utf-8"
        )
        # The action directory is tmpfs'd inside the sandbox, so the script has
        # to be staged somewhere that is actually bind-mounted.
        self.assertIn("token_usage_statusline.py", wrapper)
        self.assertIn('"$sandbox_home/.claude/settings.json"', wrapper)
        self.assertIn('"statusLine"', wrapper)
        staged = wrapper.index('statusline="$sandbox_home')
        self.assertLess(staged, wrapper.index("--tmpfs \"$actions_root\""))

    def test_usage_file_is_uploaded_with_the_artifact(self) -> None:
        action = (self.ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn(".ai-lean-check/token-usage.json", action)
        # --setting-sources user is what makes the staged settings load at all.
        self.assertIn("--setting-sources user", action)


class AllowedToolsTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]

    def test_bash_is_unrestricted_and_webfetch_is_not_granted(self) -> None:
        """The sandbox is the boundary, not the per-command allowlist.

        A restricted allowlist refused commands the prompt itself asks for
        while providing no containment, since the agent can rewrite the one
        script it permitted. WebFetch stays out because bubblewrap does not
        restrict network egress.
        """
        import yaml

        action = yaml.safe_load(
            (self.ROOT / "action.yml").read_text(encoding="utf-8")
        )
        step = next(
            s for s in action["runs"]["steps"] if s.get("id") == "claude-agent"
        )
        args = step["with"]["claude_args"]
        self.assertIn("--allowed-tools Read,Glob,Grep,Write,Edit,Bash", args)
        # Assert against the rendered arguments, not the file: the surrounding
        # comment explains why WebFetch is withheld and would match the text.
        self.assertNotIn("Bash(", args)
        self.assertNotIn("WebFetch", args)


class CredentialProbeTests(unittest.TestCase):
    ROOT = Path(__file__).parents[1]

    def test_both_arms_of_the_probe_exist(self) -> None:
        """An "absent" reading is only meaningful against a control.

        The Bash-tool arm alone cannot distinguish a credential that was
        stripped from one that was never in the environment, so the wrapper
        that launches Claude records what it was handed.
        """
        prepare = (self.ROOT / "scripts" / "prepare_agent.py").read_text(
            encoding="utf-8"
        )
        bwrap = (self.ROOT / "scripts" / "claude-bwrap.sh").read_text(encoding="utf-8")
        self.assertIn("bash-tool-child $name=present", prepare)
        self.assertIn("handed-to-claude $name=present", bwrap)

    def test_probe_records_presence_never_a_value(self) -> None:
        prepare = (self.ROOT / "scripts" / "prepare_agent.py").read_text(
            encoding="utf-8"
        )
        bwrap = (self.ROOT / "scripts" / "claude-bwrap.sh").read_text(encoding="utf-8")
        for text in (prepare, bwrap):
            # The probe value is tested for emptiness, never echoed.
            self.assertNotIn('$probe_value"', text.replace('-n "$probe_value"', ""))
            self.assertIn("=present", text)
            self.assertIn("=absent", text)

    def test_probe_writes_only_once_per_run(self) -> None:
        """The agent invokes the wrapper many times; the probe must not grow."""
        prepare = (self.ROOT / "scripts" / "prepare_agent.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("grep -q '^bash-tool-child'", prepare)

    def test_regression_is_surfaced_and_uploaded(self) -> None:
        action = (self.ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn("credential visibility probe", action)
        self.assertIn(
            "::warning::a provider credential reached a Bash tool child", action
        )
        self.assertIn(".ai-lean-check/credential-probe.txt", action)
