#!/usr/bin/env python3
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

    def compacted_row(self, message=""):
        return {
            "timestamp": "2026-07-12T12:00:03Z",
            "type": "compacted",
            "payload": {
                "message": message,
                "replacement_history": [],
                "window_number": 2,
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

    def checkpoint(self):
        return json.loads((self.state / "019f-test--root" / "checkpoint.json").read_text())

    def pending(self):
        return json.loads((self.state / "019f-test--root" / "pending.json").read_text())

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
        self.assertIn("additional local compaction snapshot", output["reason"])
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

    def test_valid_compaction_is_enriched_once(self):
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
        output = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=False,
                last_assistant_message="done",
            )
        )
        self.assertEqual(output["decision"], "block")
        self.assertIn("model-generated compacted summary", output["reason"])
        self.assertIn("first genuinely unresolved step", output["reason"])
        self.assertIn("Finish the full proof without narrowing", output["reason"])
        self.assertIn("Mode: enrichment", output["reason"])
        self.assertIn("Built-in summary health: healthy", output["reason"])

        second = self.invoke(
            self.event(
                "Stop",
                permission_mode="bypassPermissions",
                stop_hook_active=True,
                last_assistant_message="done after enrichment",
            )
        )
        self.assertNotIn("decision", second)

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
        session = self.state / "019f-test--root"
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

        session_state = codex_home / "compaction-guard" / "019f-test--root"
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

    def test_subagent_stop_falls_back_to_root_pending(self):
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

        wrong_parent = self.invoke(
            {
                **subagent_event,
                "transcript_path": str(agent_transcript),
            }
        )
        self.assertNotIn("decision", wrong_parent)
        pending = self.state / "019f-test--root" / "pending.json"
        self.assertTrue(pending.exists())

        output = self.invoke(subagent_event)
        self.assertEqual(output["decision"], "block")
        self.assertFalse(pending.exists())
        consumed = list((self.state / "019f-test--root").glob("consumed-*.json"))
        self.assertEqual(len(consumed), 1)

        second = self.invoke(subagent_event)
        self.assertNotIn("decision", second)
        self.assertEqual(
            len(list((self.state / "019f-test--root").glob("consumed-*.json"))),
            1,
        )

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

    def test_received_pre_tool_use_delivers_enrichment_early_in_same_turn(self):
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

        session_state = self.state / "019f-test--root"
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
        pending_path = self.state / "019f-test--root" / "pending.json"
        self.assertTrue(pending_path.exists())

        output = self.invoke(self.post_tool_use_event())
        self.assertEqual(set(output.keys()), {"hookSpecificOutput"})
        specific = output["hookSpecificOutput"]
        self.assertEqual(set(specific.keys()), {"hookEventName", "additionalContext"})
        self.assertEqual(specific["hookEventName"], "PostToolUse")
        self.assertIn("Finish the full proof without narrowing", specific["additionalContext"])
        self.assertFalse(pending_path.exists())
        consumed = list((self.state / "019f-test--root").glob("consumed-*.json"))
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
        pending_path = self.state / "019f-test--root" / "pending.json"
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
        session_state = self.state / "019f-test--root"
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
        session_state = self.state / "019f-test--root"
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

    def test_subagent_pre_tool_use_is_scoped_to_agent_state(self):
        self.invoke(self.event("PreCompact", trigger="auto"))
        self.rows.append(self.compacted_row())
        self._write_rows()
        self.invoke(self.event("PostCompact", trigger="auto"))
        root_pending = self.state / "019f-test--root" / "pending.json"

        agent_probe = self.invoke(
            self.pre_tool_use_event(agent_id="worker-1", agent_type="reviewer")
        )
        self.assertEqual(agent_probe, {"continue": True})
        self.assertTrue(root_pending.exists())

        self.invoke(
            self.event(
                "PreCompact", trigger="auto", agent_id="worker-1", agent_type="reviewer"
            )
        )
        self.invoke(
            self.event(
                "PostCompact", trigger="auto", agent_id="worker-1", agent_type="reviewer"
            )
        )
        agent_pending = self.state / "019f-test--worker-1" / "pending.json"
        self.assertTrue(agent_pending.exists())

        output = self.invoke(
            self.pre_tool_use_event(agent_id="worker-1", agent_type="reviewer")
        )
        self.assertEqual(
            output["hookSpecificOutput"]["hookEventName"], "PreToolUse"
        )
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
        self.rows[0]["payload"]["goal"]["objective"] = "goal-detail " * 5_000
        self.rows.extend(
            {
                "timestamp": f"2026-07-12T12:01:{index:02d}Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": f"progress-{index} " + "x" * 2_350}],
                },
            }
            for index in range(20)
        )
        (self.repo / ".codex" / "proof-ledger.jsonl").parent.mkdir()
        (self.repo / ".codex" / "proof-ledger.jsonl").write_text("proof " * 5_000, encoding="utf-8")
        self._write_rows()
        self.invoke(self.event("PreCompact", trigger="auto"))
        restore = self.checkpoint()["restore_context"]
        self.assertLessEqual(len(restore), 40_000)
        self.assertIn("## Continuation contract", restore)
        self.assertTrue(restore.endswith("</codex_local_compaction_enrichment>"))


if __name__ == "__main__":
    unittest.main()
