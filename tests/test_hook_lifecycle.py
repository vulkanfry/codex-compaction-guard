#!/usr/bin/env python3
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(
    os.environ.get(
        "CODEX_COMPACTION_GUARD_EXECUTABLE",
        PROJECT_ROOT / "target" / "debug" / "codex-compaction-guard",
    )
)


class CompactionGuardTests(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.is_file():
            self.fail(f"guard executable not found: {SCRIPT}; run cargo build first")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        (self.repo / "file.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "file.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True)
        (self.repo / "file.txt").write_text("changed\n", encoding="utf-8")
        (self.repo / ".codex.log").write_text("PROOF surface=code status=failed evidence=still-red\n", encoding="utf-8")
        self.transcript = self.root / "rollout.jsonl"
        self.rows = [
            {
                "timestamp": "2026-07-12T12:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "thread_goal_updated",
                    "goal": {"objective": "Finish the full proof without narrowing", "status": "active"},
                },
            },
            {
                "timestamp": "2026-07-12T12:00:01Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect every missing slot first"}],
                },
            },
            {
                "timestamp": "2026-07-12T12:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "I found slot 36 and am checking its source"}],
                },
            },
        ]
        self._write_rows()

    def tearDown(self):
        self.temp.cleanup()

    def _write_rows(self):
        self.transcript.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows),
            encoding="utf-8",
        )

    def event(self, name, **extra):
        value = {
            "session_id": "019f-test",
            "turn_id": "turn-1",
            "cwd": str(self.repo),
            "transcript_path": str(self.transcript),
            "model": "test-model",
            "hook_event_name": name,
        }
        value.update(extra)
        return value

    def pre_tool_use_event(self, **extra):
        value = self.event(
            "PreToolUse",
            permission_mode="bypassPermissions",
            tool_name="Bash",
            tool_input={"command": "git status --short"},
            tool_use_id="call-0001",
        )
        value.update(extra)
        return value

    def post_tool_use_event(self, **extra):
        value = self.event(
            "PostToolUse",
            permission_mode="bypassPermissions",
            tool_name="Bash",
            tool_input={"command": "git status --short"},
            tool_response={"output": "clean", "exit_code": 0},
            tool_use_id="call-0001",
        )
        value.update(extra)
        return value

    def compacted_row(
        self,
        message="",
        *,
        timestamp="2026-07-12T12:00:03Z",
        window_number=2,
    ):
        return {
            "timestamp": timestamp,
            "type": "compacted",
            "payload": {
                "message": message,
                "replacement_history": [],
                "window_number": window_number,
            },
        }

    def child_session_meta(self, child_id="worker-1", agent_path="/root/test-child"):
        return {
            "timestamp": "2026-07-12T11:59:59Z",
            "type": "session_meta",
            "payload": {
                "id": child_id,
                "parent_thread_id": "019f-test",
                "thread_source": "subagent",
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": "019f-test",
                            "depth": 1,
                            "agent_path": agent_path,
                        }
                    }
                },
            },
        }

    def invoke(self, event, *, guard_dir=True, extra_env=None):
        env = {**os.environ}
        if guard_dir:
            env["CODEX_COMPACTION_GUARD_DIR"] = str(self.state)
        else:
            env.pop("CODEX_COMPACTION_GUARD_DIR", None)
        if extra_env:
            env.update(extra_env)
        completed = subprocess.run(
            [str(SCRIPT)],
            input=json.dumps(event),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=True,
        )
        self.assertEqual(completed.stderr, "")
        return json.loads(completed.stdout)

    def invoke_parallel(self, events):
        env = {**os.environ, "CODEX_COMPACTION_GUARD_DIR": str(self.state)}
        processes = [
            subprocess.Popen(
                [str(SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for _ in events
        ]
        results = [
            process.communicate(json.dumps(event), timeout=10)
            for process, event in zip(processes, events)
        ]
        self.assertTrue(all(stderr == "" for _, stderr in results))
        return [json.loads(stdout) for stdout, _ in results]

    def transcript_state(self, transcript_path=None, *, state_root=None, session_id="019f-test"):
        transcript = Path(transcript_path or self.transcript).resolve()
        fingerprint = hashlib.sha256(str(transcript).encode()).hexdigest()[:32]
        root = state_root or self.state
        return root / f"{session_id}--transcript-{fingerprint}"

    def turn_state(self, turn_id, *, state_root=None, session_id="019f-test"):
        root = state_root or self.state
        return root / f"{session_id}--turn-{turn_id}"

    def checkpoint(self):
        return json.loads((self.transcript_state() / "checkpoint.json").read_text())

    def pending(self):
        return json.loads((self.transcript_state() / "pending.json").read_text())

    def inflate_checkpoint_context(self):
        self.rows[0]["payload"]["goal"]["objective"] = "goal-detail " * 5_000
        self.rows.extend(
            {
                "timestamp": f"2026-07-12T12:01:{index:02d}Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": f"progress-{index} " + "x" * 2_350,
                        }
                    ],
                },
            }
            for index in range(20)
        )
        (self.repo / ".codex" / "proof-ledger.jsonl").parent.mkdir()
        (self.repo / ".codex" / "proof-ledger.jsonl").write_text(
            "proof " * 5_000, encoding="utf-8"
        )
        self._write_rows()

    def test_checkpoint_schema_and_scope_metadata(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint["schema_version"], 3)
        self.assertTrue(checkpoint["checkpoint_id"])
        self.assertEqual(checkpoint["scope_path"], str(self.transcript.resolve()))
        self.assertTrue(checkpoint["scope_key"].startswith("transcript-"))
        self.assertTrue(checkpoint["cross_turn_safe"])
        self.assertFalse(checkpoint["is_subagent"])

    def test_subagent_metadata_without_agent_id_bypasses_local_compaction(self):
        child_transcript = self.root / "metadata-child.jsonl"
        child_rows = [self.child_session_meta(), *self.rows]
        child_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in child_rows),
            encoding="utf-8",
        )
        compact = {
            "turn_id": "child-turn",
            "transcript_path": str(child_transcript),
        }

        pre = self.invoke(self.event("PreCompact", trigger="auto", **compact))
        self.assertEqual(pre, {"continue": True})
        child_rows.append(self.compacted_row())
        child_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in child_rows),
            encoding="utf-8",
        )
        post = self.invoke(self.event("PostCompact", trigger="auto", **compact))
        self.assertEqual(post, {"continue": True})
        self.assertFalse(self.transcript_state(child_transcript).exists())

        tool = self.invoke(self.pre_tool_use_event(**compact))
        self.assertEqual(tool, {"continue": True})

    def test_first_root_session_meta_wins_over_later_copied_subagent_meta(self):
        root_transcript = self.root / "root-with-copied-meta.jsonl"
        root_meta = {
            "timestamp": "2026-07-12T11:59:58Z",
            "type": "session_meta",
            "payload": {
                "id": "019f-test",
                "source": "vscode",
                "thread_source": "vscode",
            },
        }
        rows = [root_meta, self.child_session_meta(), *self.rows]
        root_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        self.invoke(
            self.event(
                "PreCompact",
                trigger="auto",
                transcript_path=str(root_transcript),
            )
        )
        checkpoint = json.loads(
            (self.transcript_state(root_transcript) / "checkpoint.json").read_text()
        )
        self.assertFalse(checkpoint["is_subagent"])

    def test_empty_compaction_is_restored_through_stop(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {"message": "", "replacement_history": [], "window_number": 2},
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        output = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="What should I work on?",
            )
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("Finish the full proof without narrowing", output["reason"])
        self.assertIn("Inspect every missing slot first", output["reason"])
        self.assertIn("slot 36", output["reason"])
        self.assertIn("file.txt", output["reason"])
        self.assertIn("still-red", output["reason"])
        self.assertIn("recovery-only local compaction snapshot", output["reason"])
        self.assertIn("inherited parent history", output["reason"])
        self.assertIn("PAST steps", output["reason"])
        self.assertIn("Mode: recovery", output["reason"])
        self.assertIn("Built-in summary health: empty", output["reason"])

        second = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=True,
                last_assistant_message="done",
            )
        )
        self.assertNotIn("decision", second)

    def test_valid_compaction_uses_builtin_summary_without_local_injection(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        healthy_summary = (
            "Objective: finish the full proof without narrowing. "
            "Completed: inspected the initial slots and preserved the current worktree. "
            "Active: trace slot 36 to its source and verify the remaining economics coverage. "
            "Next move: inspect the live files and continue from the first unresolved check. "
        ) * 5
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {
                    "message": healthy_summary,
                    "replacement_history": [],
                    "window_number": 2,
                },
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        state = self.transcript_state()
        self.assertFalse((state / "pending.json").exists())
        self.assertEqual(len(list(state.glob("claimed-generation-*.json"))), 1)
        audit = [
            json.loads(line)
            for line in (state / "audit.jsonl").read_text().splitlines()
            if json.loads(line)["event"] == "restore_suppressed"
        ][-1]
        self.assertEqual(audit["via"], "PostCompact")
        self.assertEqual(audit["mode"], "enrichment")
        self.assertEqual(audit["reason"], "healthy_compaction_uses_builtin_summary")

        output = self.invoke(self.pre_tool_use_event())
        self.assertEqual(output, {"continue": True})
        stop = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="done",
            )
        )
        self.assertEqual(stop, {"continue": True})

    def test_oversized_healthy_compaction_never_becomes_model_visible(self):
        self.inflate_checkpoint_context()
        self.invoke(self.event("PreCompact", trigger="auto"))
        checkpoint_context = self.checkpoint()["restore_context"]
        self.assertGreater(len(checkpoint_context), 16_000)

        healthy_summary = (
            "Objective: finish the full proof without narrowing. "
            "Completed: inspected the initial slots and preserved the current worktree. "
            "Active: trace slot 36 to its source and verify the remaining economics coverage. "
            "Next move: inspect the live files and continue from the first unresolved check. "
        ) * 5
        self.rows.append(
            self.compacted_row(
                healthy_summary,
                timestamp="2026-07-12T12:02:00Z",
            )
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        output = self.invoke(self.pre_tool_use_event())
        self.assertEqual(output, {"continue": True})
        state = self.transcript_state()
        self.assertFalse((state / "pending.json").exists())
        self.assertEqual(len(list(state.glob("consumed-*.json"))), 0)
        audit = [
            json.loads(line)
            for line in (state / "audit.jsonl").read_text().splitlines()
            if json.loads(line)["event"] == "restore_suppressed"
        ][-1]
        self.assertEqual(audit["via"], "PostCompact")
        self.assertEqual(audit["mode"], "enrichment")
        self.assertEqual(audit["reason"], "healthy_compaction_uses_builtin_summary")

    def test_oversized_recovery_uses_larger_bounded_model_visible_budget(self):
        self.inflate_checkpoint_context()
        self.invoke(self.event("PreCompact", trigger="auto"))
        checkpoint_context = self.checkpoint()["restore_context"]
        self.assertGreater(len(checkpoint_context), 16_000)

        self.rows.append(
            self.compacted_row("", timestamp="2026-07-12T12:02:00Z")
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        output = self.invoke(self.pre_tool_use_event())
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 16_000)
        self.assertGreater(len(context), 15_000)
        self.assertIn("<codex_compaction_assessment>", context)
        self.assertIn("</codex_compaction_assessment>", context)
        self.assertIn("Mode: recovery", context)
        self.assertIn("## Temporal semantics", context)
        self.assertIn("Checkpoint created at", context)
        self.assertIn("## Continuation contract", context)
        self.assertTrue(context.endswith("</codex_local_compaction_enrichment>"))

        state = self.transcript_state()
        consumed = json.loads(next(state.glob("consumed-*.json")).read_text())
        self.assertEqual(consumed["injected_chars"], len(context))
        self.assertEqual(consumed["injection_budget_chars"], 16_000)
        audit = [
            json.loads(line)
            for line in (state / "audit.jsonl").read_text().splitlines()
            if json.loads(line)["event"] == "restore_consumed"
        ][-1]
        self.assertEqual(audit["mode"], "recovery")
        self.assertEqual(audit["injected_chars"], len(context))
        self.assertEqual(audit["injection_budget_chars"], 16_000)

    def test_malformed_legacy_pending_is_suppressed_without_model_context(self):
        self.inflate_checkpoint_context()
        self.invoke(self.event("PreCompact", trigger="auto"))
        healthy_summary = (
            "Objective and completed work remain intact after compaction. "
            "Continue from the first unresolved verification step using live files. "
        ) * 10
        self.rows.append(
            self.compacted_row(
                healthy_summary,
                timestamp="2026-07-12T12:02:00Z",
            )
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        state = self.transcript_state()
        checkpoint = self.checkpoint()
        pending_path = state / "pending.json"
        pending = {
            "schema_version": 3,
            "armed_at": "2099-01-01T00:00:00Z",
            "armed_at_unix": 4_070_908_800.0,
            "session_id": "019f-test",
            "turn_id": "turn-1",
            "scope_key": checkpoint["scope_key"],
            "scope_path": checkpoint["scope_path"],
            "cross_turn_safe": True,
            "agent_id": None,
            "cwd": str(self.repo),
            "checkpoint_id": checkpoint["checkpoint_id"],
            "mode": "unexpected-mode-" + "m" * 20_000,
            "health": {
                "level": "unexpected-level-" + "l" * 20_000,
                "message_length": "not-a-number-" + "s" * 20_000,
                "window_number": "not-a-number-" + "w" * 20_000,
            },
        }
        pending_path.write_text(json.dumps(pending), encoding="utf-8")

        output = self.invoke(self.pre_tool_use_event())
        self.assertEqual(output, {"continue": True})
        self.assertFalse(pending_path.exists())
        suppressed = json.loads(next(state.glob("suppressed-*.json")).read_text())
        self.assertEqual(suppressed["suppressed_via"], "PreToolUse")
        self.assertEqual(
            suppressed["suppression_reason"],
            "healthy_compaction_uses_builtin_summary",
        )
        audit = [
            json.loads(line)
            for line in (state / "audit.jsonl").read_text().splitlines()
            if json.loads(line)["event"] == "restore_suppressed"
        ][-1]
        self.assertEqual(audit["via"], "PreToolUse")
        self.assertEqual(audit["reason"], "healthy_compaction_uses_builtin_summary")

    def test_new_root_precompact_retains_suppressed_legacy_healthy_pending(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(
            self.compacted_row("", timestamp="2026-07-12T12:02:00Z")
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        state = self.transcript_state()
        pending_path = state / "pending.json"
        pending = json.loads(pending_path.read_text())
        old_checkpoint_id = pending["checkpoint_id"]
        pending["mode"] = "enrichment"
        pending_path.write_text(json.dumps(pending), encoding="utf-8")

        output = self.invoke(
            self.event("PreCompact", turn_id="turn-2", trigger="auto")
        )
        self.assertEqual(output, {"continue": True})
        self.assertFalse(pending_path.exists())
        suppressed = json.loads(next(state.glob("suppressed-*.json")).read_text())
        self.assertEqual(suppressed["checkpoint_id"], old_checkpoint_id)
        self.assertEqual(suppressed["suppressed_via"], "PreCompact")
        self.assertEqual(
            suppressed["suppression_reason"],
            "healthy_compaction_uses_builtin_summary",
        )

        checkpoint = self.checkpoint()
        self.assertEqual(checkpoint["turn_id"], "turn-2")
        self.assertNotEqual(checkpoint["checkpoint_id"], old_checkpoint_id)
        policy_events = [
            row
            for row in map(
                json.loads, (state / "audit.jsonl").read_text().splitlines()
            )
            if row["event"] in {"restore_suppressed", "checkpoint_saved"}
        ]
        self.assertEqual(policy_events[-2]["event"], "restore_suppressed")
        self.assertEqual(policy_events[-2]["via"], "PreCompact")
        self.assertEqual(policy_events[-1]["event"], "checkpoint_saved")
        self.assertEqual(policy_events[-1]["turn_id"], "turn-2")

    def test_weak_compaction_is_classified_as_recovery(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {
                    "message": "Summary: ready for the task",
                    "replacement_history": [],
                    "window_number": 2,
                },
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        pending = self.pending()
        health = pending["health"]
        self.assertEqual(pending["mode"], "recovery")
        self.assertEqual(health["level"], "weak")
        self.assertEqual(health["reason"], "weak_compaction")

    def test_recent_tail_is_chronological(self):
        self.rows.extend(
            [
                {
                    "timestamp": "2026-07-12T12:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": "git status --short",
                    },
                },
                {
                    "timestamp": "2026-07-12T12:00:04Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Now verify economics coverage"}],
                    },
                },
            ]
        )
        self._write_rows()
        self.invoke(self.event("PreCompact", trigger="auto"))
        checkpoint = self.checkpoint()
        restore = checkpoint["restore_context"]
        timeline = restore.split("## Recent chronological tail preserved locally", 1)[1].split(
            "## Live git/worktree state", 1
        )[0]
        positions = [
            timeline.index("Inspect every missing slot first"),
            timeline.index("I found slot 36 and am checking its source"),
            timeline.index("git status --short"),
            timeline.index("Now verify economics coverage"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_fresh_diff_is_included(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        checkpoint = self.checkpoint()
        files = checkpoint["fresh_recent_files"]
        self.assertEqual([item["path"] for item in files], ["file.txt"])
        self.assertEqual(files[0]["kind"], "current diff against HEAD")
        self.assertIn("-base", files[0]["content"])
        self.assertIn("+changed", files[0]["content"])

    def test_state_permissions_are_private(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        session = self.transcript_state()
        checkpoint = session / "checkpoint.json"
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(session.stat().st_mode & 0o777, 0o700)
        self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o600)

    def test_codex_home_controls_default_state_location(self):
        codex_home = self.root / "custom-codex-home"
        fallback_home = self.root / "fallback-home"
        hook_env = {
            "CODEX_HOME": str(codex_home),
            "HOME": str(fallback_home),
        }
        self.invoke(
            self.event("PreCompact", trigger="auto"),
            guard_dir=False,
            extra_env=hook_env,
        )
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {"message": "", "replacement_history": [], "window_number": 2},
            }
        )
        self._write_rows()
        self.invoke(
            self.event("PostCompact", trigger="auto"),
            guard_dir=False,
            extra_env=hook_env,
        )

        session_state = self.transcript_state(
            state_root=codex_home / "compaction-guard"
        )
        checkpoint = session_state / "checkpoint.json"
        pending = session_state / "pending.json"
        audit = session_state / "audit.jsonl"
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(pending.is_file())
        self.assertTrue(audit.is_file())
        self.assertEqual((codex_home / "compaction-guard").stat().st_mode & 0o777, 0o700)
        self.assertFalse((fallback_home / ".codex" / "compaction-guard").exists())

        output = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="stopped after compaction",
            ),
            guard_dir=False,
            extra_env=hook_env,
        )
        self.assertEqual(output["decision"], "block")
        self.assertFalse(pending.exists())
        self.assertEqual(len(list(session_state.glob("consumed-*.json"))), 1)

    def test_staged_and_untracked_files_are_included(self):
        (self.repo / "staged.txt").write_text("staged content\n", encoding="utf-8")
        subprocess.run(["git", "add", "staged.txt"], cwd=self.repo, check=True)
        (self.repo / "untracked.txt").write_text("untracked content\n", encoding="utf-8")
        self.invoke(self.event("PreCompact", trigger="auto"))
        paths = {item["path"] for item in self.checkpoint()["fresh_recent_files"]}
        self.assertIn("staged.txt", paths)
        self.assertIn("untracked.txt", paths)

    def test_stop_hook_active_does_not_consume_pending(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {"message": "", "replacement_history": [], "window_number": 2},
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        recursive = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=True,
                last_assistant_message="continuation generated by hook",
            )
        )
        self.assertNotIn("decision", recursive)

        first_real_stop = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="stopped after compaction",
            )
        )
        self.assertEqual(first_real_stop["decision"], "block")

    def test_subagent_stop_does_not_consume_root_pending(self):
        self.invoke(self.event("PreCompact", turn_id="root-turn", trigger="auto"))
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {"message": "", "replacement_history": [], "window_number": 2},
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", turn_id="root-turn", trigger="auto"))

        agent_transcript = self.root / "agent-rollout.jsonl"
        agent_transcript.write_text("", encoding="utf-8")
        subagent_event = self.event(
            "SubagentStop",
            turn_id="child-turn",
            agent_id="worker-1",
            agent_type="reviewer",
            agent_transcript_path=str(agent_transcript),
            permission_mode="bypassPermissions",
            stop_hook_active=False,
            last_assistant_message="subagent stopped",
        )

        root_state = self.transcript_state()
        pending = root_state / "pending.json"
        self.assertTrue(pending.exists())

        output = self.invoke(subagent_event)
        self.assertNotIn("decision", output)
        self.assertTrue(pending.exists())
        self.assertEqual(
            len(list(root_state.glob("consumed-*.json"))),
            0,
        )

    def test_transcript_scope_isolates_compactions_without_agent_id(self):
        child_transcript = self.root / "child-rollout.jsonl"
        child_rows = [self.child_session_meta(), *self.rows]
        child_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in child_rows),
            encoding="utf-8",
        )

        root_pre = self.event("PreCompact", turn_id="root-turn", trigger="auto")
        root_post = self.event("PostCompact", turn_id="root-turn", trigger="auto")
        child_pre = self.event(
            "PreCompact",
            turn_id="child-turn",
            trigger="auto",
            transcript_path=str(child_transcript),
        )
        child_post = self.event(
            "PostCompact",
            turn_id="child-turn",
            trigger="auto",
            transcript_path=str(child_transcript),
        )

        self.invoke(root_pre)
        self.invoke(child_pre)
        self.rows.append(self.compacted_row())
        self._write_rows()
        child_rows.append(self.compacted_row())
        child_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in child_rows),
            encoding="utf-8",
        )
        self.invoke(root_post)
        self.invoke(child_post)

        state_dirs = [path for path in self.state.iterdir() if path.is_dir()]
        pending_dirs = [path for path in state_dirs if (path / "pending.json").is_file()]
        self.assertEqual(pending_dirs, [self.transcript_state()])
        self.assertFalse(self.transcript_state(child_transcript).exists())

        root_output = self.invoke(
            self.pre_tool_use_event(turn_id="root-turn", transcript_path=str(self.transcript))
        )
        self.assertEqual(root_output["hookSpecificOutput"]["hookEventName"], "PreToolUse")

        child_output = self.invoke(
            self.pre_tool_use_event(
                turn_id="child-turn",
                transcript_path=str(child_transcript),
            )
        )
        self.assertEqual(child_output, {"continue": True})

    def test_transcript_scope_canonicalizes_symlink_aliases(self):
        transcript_alias = self.root / "rollout-alias.jsonl"
        transcript_alias.symlink_to(self.transcript)

        self.invoke(
            self.event(
                "PreCompact",
                trigger="auto",
                transcript_path=str(transcript_alias),
            )
        )
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(
            self.event(
                "PostCompact",
                trigger="auto",
                transcript_path=str(self.transcript),
            )
        )

        state = self.transcript_state()
        self.assertTrue((state / "pending.json").exists())
        output = self.invoke(
            self.pre_tool_use_event(transcript_path=str(transcript_alias))
        )
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertFalse((state / "pending.json").exists())

    def test_subagent_stop_suppresses_legacy_child_pending_only(self):
        self.invoke(self.event("PreCompact", turn_id="root-turn", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", turn_id="root-turn", trigger="auto"))
        root_pending = self.transcript_state() / "pending.json"

        child_transcript = self.root / "child-stop-rollout.jsonl"
        child_rows = [
            *self.rows[:-1],
            {
                "timestamp": "2026-07-12T12:00:02.500Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Child scope only"}],
                },
            },
        ]
        child_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in child_rows),
            encoding="utf-8",
        )
        child_compact = {
            "turn_id": "child-turn",
            "transcript_path": str(child_transcript),
        }
        self.invoke(self.event("PreCompact", trigger="auto", **child_compact))
        child_rows.append(self.compacted_row())
        child_transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in child_rows),
            encoding="utf-8",
        )
        self.invoke(self.event("PostCompact", trigger="auto", **child_compact))
        child_state = self.transcript_state(child_transcript)
        child_pending = child_state / "pending.json"
        self.assertTrue(root_pending.exists())
        self.assertTrue(child_pending.exists())

        output = self.invoke(
            self.event(
                "SubagentStop",
                turn_id="child-turn",
                transcript_path=str(self.transcript),
                agent_transcript_path=str(child_transcript),
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="child finished",
            )
        )
        self.assertEqual(output, {"continue": True})
        self.assertFalse(child_pending.exists())
        self.assertTrue(root_pending.exists())
        self.assertEqual(len(list(child_state.glob("consumed-*.json"))), 0)
        child_suppressed = list(child_state.glob("suppressed-*.json"))
        self.assertEqual(len(child_suppressed), 1)
        self.assertEqual(
            json.loads(child_suppressed[0].read_text())["suppressed_via"],
            "SubagentStop",
        )
        self.assertEqual(
            json.loads(child_suppressed[0].read_text())["suppression_reason"],
            "subagent_local_compaction_disabled",
        )

        root_output = self.invoke(
            self.event(
                "Stop",
                turn_id="root-turn",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="root finished",
            )
        )
        self.assertEqual(root_output["decision"], "block")
        self.assertFalse(root_pending.exists())

    def test_two_children_compact_concurrently_without_agent_id(self):
        child_a = self.root / "child-a.jsonl"
        child_b = self.root / "child-b.jsonl"
        base_a = json.dumps(self.child_session_meta("child-a", "/root/child-a")) + "\n" + self.transcript.read_text(encoding="utf-8")
        base_b = json.dumps(self.child_session_meta("child-b", "/root/child-b")) + "\n" + self.transcript.read_text(encoding="utf-8")
        child_a.write_text(base_a, encoding="utf-8")
        child_b.write_text(base_b, encoding="utf-8")
        pre_events = [
            self.event(
                "PreCompact",
                turn_id="child-a-turn",
                trigger="auto",
                transcript_path=str(child_a),
            ),
            self.event(
                "PreCompact",
                turn_id="child-b-turn",
                trigger="auto",
                transcript_path=str(child_b),
            ),
        ]
        self.invoke_parallel(pre_events)
        compacted = json.dumps(self.compacted_row()) + "\n"
        child_a.write_text(base_a + compacted, encoding="utf-8")
        child_b.write_text(base_b + compacted, encoding="utf-8")
        post_events = [
            self.event(
                "PostCompact",
                turn_id="child-a-turn",
                trigger="auto",
                transcript_path=str(child_a),
            ),
            self.event(
                "PostCompact",
                turn_id="child-b-turn",
                trigger="auto",
                transcript_path=str(child_b),
            ),
        ]
        self.invoke_parallel(post_events)

        states = [self.transcript_state(child_a), self.transcript_state(child_b)]
        self.assertNotEqual(states[0], states[1])
        self.assertTrue(all(not state.exists() for state in states))
        outputs = self.invoke_parallel(
            [
                self.pre_tool_use_event(
                    turn_id="child-a-turn",
                    transcript_path=str(child_a),
                ),
                self.pre_tool_use_event(
                    turn_id="child-b-turn",
                    transcript_path=str(child_b),
                ),
            ]
        )
        self.assertEqual(outputs, [{"continue": True}, {"continue": True}])

    def test_child_tool_and_stop_race_suppresses_legacy_pending_once(self):
        self.invoke(self.event("PreCompact", turn_id="root-turn", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", turn_id="root-turn", trigger="auto"))
        root_pending = self.transcript_state() / "pending.json"

        child_transcript = self.root / "child-race.jsonl"
        child_transcript.write_text(self.transcript.read_text(encoding="utf-8"), encoding="utf-8")
        child_context = {
            "turn_id": "child-turn",
            "transcript_path": str(child_transcript),
        }
        self.invoke(self.event("PreCompact", trigger="auto", **child_context))
        child_transcript.write_text(
            child_transcript.read_text(encoding="utf-8")
            + json.dumps(
                self.compacted_row(
                    timestamp="2026-07-12T12:00:04Z",
                    window_number=3,
                )
            )
            + "\n",
            encoding="utf-8",
        )
        self.invoke(self.event("PostCompact", trigger="auto", **child_context))
        child_state = self.transcript_state(child_transcript)
        self.assertTrue((child_state / "pending.json").exists())
        delivery_context = {
            **child_context,
            "agent_id": "worker-1",
            "agent_type": "reviewer",
        }

        outputs = self.invoke_parallel(
            [
                self.pre_tool_use_event(**delivery_context),
                self.event(
                    "SubagentStop",
                    turn_id="child-turn",
                    agent_id="worker-1",
                    agent_type="reviewer",
                    transcript_path=str(self.transcript),
                    agent_transcript_path=str(child_transcript),
                    permission_mode="bypassPermissions",
                    stop_hook_active=False,
                    last_assistant_message="child finished",
                ),
            ]
        )
        injections = sum(
            "hookSpecificOutput" in output or output.get("decision") == "block"
            for output in outputs
        )
        self.assertEqual(injections, 0)
        self.assertTrue(all(output == {"continue": True} for output in outputs))
        self.assertFalse((child_state / "pending.json").exists())
        self.assertEqual(len(list(child_state.glob("consumed-*.json"))), 0)
        self.assertEqual(len(list(child_state.glob("suppressed-*.json"))), 1)
        self.assertTrue(root_pending.exists())

    def test_null_transcript_is_same_turn_only(self):
        compact = {"turn_id": "turn-null", "transcript_path": None}
        self.invoke(self.event("PreCompact", trigger="auto", **compact))
        self.invoke(self.event("PostCompact", trigger="auto", **compact))
        state = self.turn_state("turn-null")
        pending = state / "pending.json"
        self.assertTrue(pending.exists())

        later_prompt = self.invoke(
            self.event(
                "UserPromptSubmit",
                turn_id="turn-later",
                transcript_path=None,
                permission_mode="bypassPermissions",
                prompt="continue",
            )
        )
        self.assertEqual(later_prompt, {"continue": True})
        later_start = self.invoke(
            self.event(
                "SessionStart",
                turn_id=None,
                transcript_path=None,
                permission_mode="bypassPermissions",
                source="resume",
            )
        )
        self.assertEqual(later_start, {"continue": True})
        self.assertTrue(pending.exists())

        output = self.invoke(
            self.pre_tool_use_event(turn_id="turn-null", transcript_path=None)
        )
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertFalse(pending.exists())

    def test_legacy_state_migrates_into_transcript_scope_once(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        scoped = self.transcript_state()
        legacy = self.state / "019f-test--root"
        scoped.rename(legacy)
        for name in ["checkpoint.json", "pending.json"]:
            path = legacy / name
            value = json.loads(path.read_text())
            value["schema_version"] = 2
            value.pop("scope_key", None)
            value.pop("scope_path", None)
            value.pop("cross_turn_safe", None)
            path.write_text(json.dumps(value), encoding="utf-8")

        output = self.invoke(self.pre_tool_use_event())
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertFalse(legacy.exists())
        self.assertFalse((scoped / "pending.json").exists())
        self.assertEqual(len(list(scoped.glob("consumed-*.json"))), 1)

    def test_delayed_post_compact_cannot_arm_newer_checkpoint(self):
        self.invoke(self.event("PreCompact", turn_id="turn-a", trigger="auto"))
        self.invoke(self.event("PreCompact", turn_id="turn-b", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()

        self.invoke(self.event("PostCompact", turn_id="turn-b", trigger="auto"))
        pending_path = self.transcript_state() / "pending.json"
        pending_before = pending_path.read_bytes()
        checkpoint_id = json.loads(pending_before)["checkpoint_id"]

        delayed = self.invoke(self.event("PostCompact", turn_id="turn-a", trigger="auto"))
        self.assertEqual(delayed, {"continue": True})
        self.assertEqual(pending_path.read_bytes(), pending_before)
        self.assertEqual(json.loads(pending_path.read_text())["checkpoint_id"], checkpoint_id)

        consumed = self.invoke(self.pre_tool_use_event(turn_id="turn-b"))
        self.assertEqual(consumed["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertFalse(pending_path.exists())

    def test_same_turn_stale_post_cannot_arm_or_mutate_newer_generation(self):
        self.rows.append(
            self.compacted_row(
                timestamp="2026-07-12T12:00:02.500Z",
                window_number=1,
            )
        )
        self._write_rows()
        compact_event = self.event("PreCompact", turn_id="same-turn", trigger="auto")

        self.invoke(compact_event)
        self.rows.append(
            self.compacted_row(
                timestamp="2026-07-12T12:00:03Z",
                window_number=2,
            )
        )
        self._write_rows()
        self.invoke(compact_event)

        delayed_before_new_compaction = self.invoke(
            self.event("PostCompact", turn_id="same-turn", trigger="auto")
        )
        self.assertEqual(delayed_before_new_compaction, {"continue": True})
        pending_path = self.transcript_state() / "pending.json"
        self.assertFalse(pending_path.exists())

        self.rows.append(
            self.compacted_row(
                timestamp="2026-07-12T12:00:04Z",
                window_number=3,
            )
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", turn_id="same-turn", trigger="auto"))
        pending_before = pending_path.read_bytes()

        delayed_after_new_compaction = self.invoke(
            self.event("PostCompact", turn_id="same-turn", trigger="auto")
        )
        self.assertEqual(delayed_after_new_compaction, {"continue": True})
        self.assertEqual(pending_path.read_bytes(), pending_before)

        consumed = self.invoke(self.pre_tool_use_event(turn_id="same-turn"))
        self.assertEqual(consumed["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertFalse(pending_path.exists())
        consumed_paths = list(self.transcript_state().glob("consumed-*.json"))
        self.assertEqual(len(consumed_paths), 1)

        delayed_after_consumption = self.invoke(
            self.event("PostCompact", turn_id="same-turn", trigger="auto")
        )
        self.assertEqual(delayed_after_consumption, {"continue": True})
        self.assertFalse(pending_path.exists())
        self.assertEqual(
            list(self.transcript_state().glob("consumed-*.json")),
            consumed_paths,
        )

    def test_compaction_generation_falls_back_to_timestamp(self):
        self.rows.append(
            self.compacted_row(
                timestamp="2026-07-12T12:00:02.500Z",
                window_number=None,
            )
        )
        self._write_rows()
        event = self.event("PreCompact", turn_id="timestamp-turn", trigger="auto")
        self.invoke(event)

        stale = self.invoke(
            self.event("PostCompact", turn_id="timestamp-turn", trigger="auto")
        )
        pending_path = self.transcript_state() / "pending.json"
        self.assertEqual(stale, {"continue": True})
        self.assertFalse(pending_path.exists())

        self.rows.append(
            self.compacted_row(
                timestamp="2026-07-12T12:00:03Z",
                window_number=None,
            )
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", turn_id="timestamp-turn", trigger="auto"))
        self.assertTrue(pending_path.exists())
        self.assertEqual(
            json.loads(pending_path.read_text())["health"]["timestamp"],
            "2026-07-12T12:00:03Z",
        )

    def test_concurrent_post_compact_claims_generation_once(self):
        self.invoke(self.event("PreCompact", turn_id="claim-turn", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        post_event = self.event("PostCompact", turn_id="claim-turn", trigger="auto")

        outputs = self.invoke_parallel([post_event] * 12)
        self.assertTrue(all(output == {"continue": True} for output in outputs))
        state = self.transcript_state()
        self.assertTrue((state / "pending.json").exists())
        self.assertEqual(len(list(state.glob("claimed-generation-*.json"))), 1)
        audit_events = [
            json.loads(line)["event"]
            for line in (state / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(audit_events.count("restore_armed"), 1)

        raced = self.invoke_parallel(
            [self.pre_tool_use_event(turn_id="claim-turn")] * 8
            + [post_event] * 8
        )
        self.assertEqual(
            sum("hookSpecificOutput" in output for output in raced),
            1,
        )
        self.assertFalse((state / "pending.json").exists())
        self.assertEqual(len(list(state.glob("consumed-*.json"))), 1)

        self.invoke_parallel([post_event] * 8)
        self.assertFalse((state / "pending.json").exists())
        self.assertEqual(len(list(state.glob("consumed-*.json"))), 1)

    def test_session_start_is_fallback_injection(self):
        self.invoke(self.event("PreCompact", trigger="manual"))
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {"message": "", "replacement_history": [], "window_number": 2},
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="manual"))
        output = self.invoke(
            {
                "session_id": "019f-test",
                "cwd": str(self.repo),
                "transcript_path": str(self.transcript),
                "model": "test-model",
                "hook_event_name": "SessionStart",
                "permission_mode": "bypassPermissions",
                "source": "compact",
            }
        )
        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "SessionStart")
        self.assertIn("Finish the full proof", specific["additionalContext"])

    def test_concurrent_stop_injects_exactly_once(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:03Z",
                "type": "compacted",
                "payload": {"message": "", "replacement_history": [], "window_number": 2},
            }
        )
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        event = self.event(
            "Stop",
            permission_mode="bypassPermissions",
            stop_hook_active=False,
            last_assistant_message="stopped after compaction",
        )
        env = {**os.environ, "CODEX_COMPACTION_GUARD_DIR": str(self.state)}
        processes = [
            subprocess.Popen(
                [str(SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for _ in range(8)
        ]
        results = [process.communicate(json.dumps(event), timeout=10) for process in processes]
        self.assertTrue(all(stderr == "" for _, stderr in results))
        outputs = [json.loads(stdout) for stdout, _ in results]
        self.assertEqual(sum(output.get("decision") == "block" for output in outputs), 1)

    def test_received_pre_tool_use_delivers_recovery_early_in_same_turn(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        before_arming = self.invoke(self.pre_tool_use_event())
        self.assertEqual(before_arming, {"continue": True})

        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        output = self.invoke(self.pre_tool_use_event())
        self.assertEqual(set(output.keys()), {"hookSpecificOutput"})
        specific = output["hookSpecificOutput"]
        self.assertEqual(set(specific.keys()), {"hookEventName", "additionalContext"})
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        context = specific["additionalContext"]
        self.assertIn("Finish the full proof without narrowing", context)
        self.assertIn("Mode: recovery", context)
        self.assertIn("PAST steps", context)

        session_state = self.transcript_state()
        self.assertFalse((session_state / "pending.json").exists())
        consumed = list(session_state.glob("consumed-*.json"))
        self.assertEqual(len(consumed), 1)
        self.assertEqual(
            json.loads(consumed[0].read_text())["consumed_via"], "PreToolUse"
        )

        post_after_delivery = self.invoke(self.post_tool_use_event())
        self.assertNotIn("hookSpecificOutput", post_after_delivery)

        stop_after_delivery = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="turn finished after early delivery",
            )
        )
        self.assertNotIn("decision", stop_after_delivery)
        self.assertEqual(len(list(session_state.glob("consumed-*.json"))), 1)

    def test_post_tool_use_delivers_write_stdin_fallback(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        wrong_turn = self.invoke(self.post_tool_use_event(turn_id="turn-2"))
        self.assertEqual(wrong_turn, {"continue": True})
        session_state = self.transcript_state()
        pending_path = session_state / "pending.json"
        self.assertTrue(pending_path.exists())

        output = self.invoke(self.post_tool_use_event())
        self.assertEqual(set(output.keys()), {"hookSpecificOutput"})
        specific = output["hookSpecificOutput"]
        self.assertEqual(set(specific.keys()), {"hookEventName", "additionalContext"})
        self.assertEqual(specific["hookEventName"], "PostToolUse")
        self.assertIn("Finish the full proof without narrowing", specific["additionalContext"])
        self.assertFalse(pending_path.exists())
        consumed = list(session_state.glob("consumed-*.json"))
        self.assertEqual(len(consumed), 1)
        self.assertEqual(
            json.loads(consumed[0].read_text())["consumed_via"], "PostToolUse"
        )

        stop_after_delivery = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="turn finished after write_stdin",
            )
        )
        self.assertNotIn("decision", stop_after_delivery)

    def test_pre_tool_use_is_turn_bound(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        other_turn = self.invoke(self.pre_tool_use_event(turn_id="turn-2"))
        self.assertEqual(other_turn, {"continue": True})
        pending_path = self.transcript_state() / "pending.json"
        self.assertTrue(pending_path.exists())

        same_turn = self.invoke(self.pre_tool_use_event())
        self.assertEqual(
            same_turn["hookSpecificOutput"]["hookEventName"], "PreToolUse"
        )
        self.assertFalse(pending_path.exists())

    def test_user_prompt_submit_delivers_after_manual_compact(self):
        self.invoke(self.event("PreCompact", trigger="manual"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="manual"))

        output = self.invoke(
            self.event(
                "UserPromptSubmit",
                turn_id="turn-2",
                permission_mode="bypassPermissions",
                prompt="continue the task",
            )
        )
        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "UserPromptSubmit")
        self.assertIn("Finish the full proof", specific["additionalContext"])
        session_state = self.transcript_state()
        self.assertFalse((session_state / "pending.json").exists())
        consumed = list(session_state.glob("consumed-*.json"))
        self.assertEqual(len(consumed), 1)
        self.assertEqual(
            json.loads(consumed[0].read_text())["consumed_via"], "UserPromptSubmit"
        )

    def test_concurrent_tool_events_inject_exactly_once_before_stop(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))

        events = [self.pre_tool_use_event(), self.post_tool_use_event()] * 4
        env = {**os.environ, "CODEX_COMPACTION_GUARD_DIR": str(self.state)}
        processes = [
            subprocess.Popen(
                [str(SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for _ in events
        ]
        results = [
            process.communicate(json.dumps(event), timeout=10)
            for process, event in zip(processes, events)
        ]
        self.assertTrue(all(stderr == "" for _, stderr in results))
        outputs = [json.loads(stdout) for stdout, _ in results]
        injections = sum("hookSpecificOutput" in output for output in outputs)
        self.assertEqual(injections, 1)
        session_state = self.transcript_state()
        self.assertFalse((session_state / "pending.json").exists())
        self.assertEqual(len(list(session_state.glob("consumed-*.json"))), 1)

        stop_after_race = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="stopped after compaction",
            )
        )
        self.assertNotIn("decision", stop_after_race)
        self.assertEqual(len(list(session_state.glob("consumed-*.json"))), 1)

    def test_subagent_pre_tool_use_never_consumes_root_or_creates_child_state(self):
        agent_transcript = self.root / "agent-rollout.jsonl"
        agent_transcript.write_text(self.transcript.read_text(encoding="utf-8"), encoding="utf-8")
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        root_pending = self.transcript_state() / "pending.json"

        agent_probe = self.invoke(
            self.pre_tool_use_event(
                agent_id="worker-1",
                agent_type="reviewer",
                transcript_path=str(agent_transcript),
            )
        )
        self.assertEqual(agent_probe, {"continue": True})
        self.assertTrue(root_pending.exists())

        self.invoke(
            self.event(
                "PreCompact",
                trigger="auto",
                agent_id="worker-1",
                agent_type="reviewer",
                transcript_path=str(agent_transcript),
            )
        )
        agent_transcript.write_text(
            agent_transcript.read_text(encoding="utf-8")
            + json.dumps(
                self.compacted_row(
                    timestamp="2026-07-12T12:00:04Z",
                    window_number=3,
                )
            )
            + "\n",
            encoding="utf-8",
        )
        self.invoke(
            self.event(
                "PostCompact",
                trigger="auto",
                agent_id="worker-1",
                agent_type="reviewer",
                transcript_path=str(agent_transcript),
            )
        )
        agent_pending = self.transcript_state(agent_transcript) / "pending.json"
        self.assertFalse(agent_pending.exists())
        self.assertFalse(self.transcript_state(agent_transcript).exists())

        output = self.invoke(
            self.pre_tool_use_event(
                agent_id="worker-1",
                agent_type="reviewer",
                transcript_path=str(agent_transcript),
            )
        )
        self.assertEqual(output, {"continue": True})
        self.assertFalse(agent_pending.exists())
        self.assertTrue(root_pending.exists())

    def test_secrets_are_redacted(self):
        openai_key = "sk-" + "abcdefghijklmnopqrstuvwxyz"
        github_key = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
        (self.repo / ".env.local").write_text("TOKEN=anothersecretvalue\n", encoding="utf-8")
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:04Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": f'"token": "supersecretvalue", API {openai_key} {github_key}',
                        }
                    ],
                },
            }
        )
        self.rows.append(
            {
                "timestamp": "2026-07-12T12:00:05Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": "*** Begin Patch\n*** Update File: .env.local\n@@\n-TOKEN=old\n+TOKEN=anothersecretvalue\n*** End Patch",
                },
            }
        )
        self._write_rows()
        self.invoke(self.event("PreCompact", trigger="auto"))
        checkpoint = self.checkpoint()
        restore = checkpoint["restore_context"]
        self.assertNotIn("supersecretvalue", restore)
        self.assertNotIn(openai_key, restore)
        self.assertNotIn(github_key, restore)
        self.assertNotIn("anothersecretvalue", restore)
        self.assertNotIn(".env.local", [item["path"] for item in checkpoint["fresh_recent_files"]])
        self.assertIn("[REDACTED", restore)

    def test_restore_footer_survives_budget_truncation(self):
        self.inflate_checkpoint_context()
        self.invoke(self.event("PreCompact", trigger="auto"))
        restore = self.checkpoint()["restore_context"]
        self.assertLessEqual(len(restore), 40_000)
        self.assertIn("## Continuation contract", restore)
        self.assertTrue(restore.endswith("</codex_local_compaction_enrichment>"))


if __name__ == "__main__":
    unittest.main()
