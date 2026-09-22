#!/usr/bin/env python3
"""Offline tests for agy_attach: the proofs, never the reply.

Every case here is one of the 2026-09-18 measurements replayed against a fake
agy. The fake is the point: it writes the same three things the real one writes
— a JSON envelope on stdout, brain/<cid>/.tempmediaStorage/ copies, and tool
steps in conversations/<cid>.db — so the module is tested on the evidence it
claims to read, not on a mock of its own helpers.

    python -m unittest discover -s tests/qc -t .

No network, no agy, no books.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import agy_attach  # noqa: E402

REAL_QUOTA_LINE = (
    "ERROR: logging before google.Init: E0901 23:12:07.484793 484 "
    "errorreport.go:223] agent executor error: calling model: "
    "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade "
    "your subscription to increase your limits. Resets in 13h33m26s.")

# A fake agy. It ignores the prompt (a judge prompt is 10k of rules and none of
# it changes what has to be on disk afterwards) and takes its whole behaviour
# from FAKE_AGY_CONF, one entry per call so a retry can behave differently from
# the attempt before it — which is how "discard and retry" gets tested at all.
FAKE_AGY = r'''
import json, os, pathlib, sqlite3, sys, time

SCHEMA = ("CREATE TABLE `steps` (`idx` integer,`step_type` integer NOT NULL "
          "DEFAULT 0,`status` integer NOT NULL DEFAULT 0,`has_subtrajectory` "
          "numeric NOT NULL DEFAULT false,`metadata` blob,`error_details` blob,"
          "`permissions` blob,`task_details` blob,`render_info` blob,"
          "`step_payload` blob,`step_format` integer NOT NULL DEFAULT 0,"
          "PRIMARY KEY (`idx`))")


def varint(n):
    out = bytearray()
    while True:
        c = n & 0x7F
        n >>= 7
        out.append(c | 0x80 if n else c)
        if not n:
            return bytes(out)


def field(num, payload):
    return varint((num << 3) | 2) + varint(len(payload)) + payload


def files_here():
    return [p for p in sorted(pathlib.Path.cwd().iterdir()) if p.is_file()]


def main():
    if "--version" in sys.argv:
        sys.stdout.write("agy version 9.9.9-fake\n")
        return 0
    conf = json.loads(pathlib.Path(os.environ["FAKE_AGY_CONF"]).read_text("utf-8"))
    root = pathlib.Path(conf["root"])
    root.mkdir(parents=True, exist_ok=True)
    counter = root / "fake_calls.txt"
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    call = conf["calls"][min(n, len(conf["calls"]) - 1)]
    cid = "%s-%d" % (call.get("cid", "conv"), n)

    seen = root / "seen"
    seen.mkdir(parents=True, exist_ok=True)
    (seen / ("%d.json" % n)).write_text(json.dumps({
        "cwd": os.getcwd(),
        "auto_update": os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE"),
        "argv": sys.argv[1:],
    }), encoding="utf-8")

    time.sleep(call.get("sleep", 0))

    if call.get("ingest", True):
        media = root / "brain" / cid / ".tempmediaStorage"
        media.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(files_here()):
            (media / ("media_%d%s" % (i, p.suffix))).write_bytes(p.read_bytes())

    tools = call.get("tools")
    if tools is not None:
        convs = root / "conversations"
        convs.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(convs / ("%s.db" % cid)))
        con.execute(SCHEMA)
        idx = 0
        for i, tool in enumerate(tools):
            name = tool if isinstance(tool, str) else tool["name"]
            args = {} if isinstance(tool, str) else dict(tool.get("args") or {})
            if name == "view_file" and "AbsolutePath" not in args:
                here = files_here()
                args["AbsolutePath"] = str(here[0]) if here else os.getcwd()
            body = (field(1, ("call_%d" % i).encode())
                    + field(2, name.encode())
                    + field(3, json.dumps(args).encode()))
            payload = field(9, field(4, body))
            for step_type in (15, 132):
                con.execute("insert into steps (idx, step_type, status, "
                            "step_payload) values (?,?,?,?)",
                            (idx, step_type, 3, payload))
                idx += 1
        con.commit()
        con.close()

    if call.get("log"):
        logs = root / "log"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / ("cli-%s.log" % time.strftime("%Y%m%d_%H%M%S"))).write_text(
            call["log"] + "\n", encoding="utf-8")

    envelope = {"conversation_id": cid,
                "status": call.get("status", "SUCCESS"),
                "response": call.get("response", "{}")}
    if call.get("error"):
        envelope["error"] = call["error"]
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False))
    if call.get("stderr"):
        sys.stderr.write(call["stderr"])
    return call.get("rc", 0)


raise SystemExit(main())
'''


class AgyAttachCase(unittest.TestCase):
    """A private agy home and a private workspace per test.

    Per test, not per class: quota_log_line reads the NEWEST cli-*.log in the
    root, so a log written by the quota test would otherwise still be the
    newest one when the "p. 429 is a page number" test runs seconds later, and
    that test would pass for the wrong reason.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="qc-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "gemini"          # stands in for ~/.gemini/antigravity-cli
        self.root.mkdir(parents=True)
        self.fake = self.tmp / "fake_agy.py"
        self.fake.write_text(FAKE_AGY, encoding="utf-8")
        self.exe = [sys.executable, str(self.fake)]
        agy_attach._VERSION_CACHE.clear()

        self.ws = agy_attach.make_workspace("study-042-catatonia", "A",
                                            base=self.tmp)
        self.bundle = self.tmp / "src" / "bundle.pdf"
        self.bundle.parent.mkdir(parents=True, exist_ok=True)
        self.bundle.write_bytes(b"%PDF-1.4\nprinted page 135 three (or more)\n%%EOF")
        self.artifacts = self.tmp / "src" / "artifacts.md"
        self.artifacts.write_text("### Q2\nקטטוניה — שלושה או יותר\n",
                                  encoding="utf-8")

    def conf(self, *calls) -> None:
        path = self.tmp / "conf.json"
        path.write_text(json.dumps({"root": str(self.root), "calls": list(calls)}),
                        encoding="utf-8")
        os.environ["FAKE_AGY_CONF"] = str(path)
        self.addCleanup(os.environ.pop, "FAKE_AGY_CONF", None)

    def ask(self, **kw):
        kw.setdefault("attempts", 2)
        return agy_attach.ask_with_files(
            "Judge this.", [self.bundle, self.artifacts], workspace=self.ws,
            exe=self.exe, gemini_root=self.root, **kw)

    def calls_made(self) -> int:
        counter = self.root / "fake_calls.txt"
        return int(counter.read_text()) if counter.exists() else 0


class TestSuccessPath(AgyAttachCase):

    def test_success(self):
        self.conf({"response": '{"canary": "ok"}', "tools": ["view_file"]})
        res = self.ask()
        self.assertTrue(res.ok, res.diagnostic)
        self.assertEqual(res.data, {"canary": "ok"})
        self.assertEqual(res.attempts, 1)
        self.assertTrue(res.tempmedia_ok)
        self.assertEqual(res.tempmedia, {"bundle.pdf": True, "artifacts.md": True})
        self.assertEqual(res.tool_audit, ["view_file"])
        self.assertEqual(res.agy_version, "agy version 9.9.9-fake")
        self.assertEqual(os.path.normcase(os.path.realpath(res.workspace)),
                         os.path.normcase(os.path.realpath(self.ws)))
        self.assertGreater(res.argv_len, 0)
        self.assertIsNotNone(res.db_bytes)

    def test_subprocess_environment_and_flags(self):
        """§4/§5.6 on the wire: no auto-update, cwd is the workspace, the model
        and the print timeout are passed explicitly rather than defaulted."""
        self.conf({"response": '{"canary": "ok"}', "tools": ["view_file"]})
        self.ask()
        seen = json.loads((self.root / "seen" / "0.json").read_text(encoding="utf-8"))
        self.assertEqual(seen["auto_update"], "1")
        self.assertEqual(os.path.normcase(os.path.realpath(seen["cwd"])),
                         os.path.normcase(os.path.realpath(self.ws)))
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.1-pro-high")
        self.assertEqual(argv[argv.index("--print-timeout") + 1], "25m")
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        # The refs agy needs: absolute, forward slashes, inside the workspace.
        body = argv[argv.index("-p") + 1]
        self.assertIn("@" + (self.ws.resolve() / "bundle.pdf").as_posix(), body)
        self.assertIn("ATTACHED to this message", body)

    def test_validate_rejection_retries(self):
        self.conf({"response": '{"canary": "wrong"}', "tools": ["view_file"]})
        res = self.ask(validate=lambda d: None if d.get("canary") == "ok"
                       else "canary mismatch")
        self.assertFalse(res.ok)
        self.assertEqual(res.attempts, 2)
        self.assertIn("canary mismatch", res.diagnostic)


class TestQuota(AgyAttachCase):

    def test_quota_from_envelope(self):
        self.conf({"status": "ERROR",
                   "error": "RESOURCE_EXHAUSTED (code 429): Individual quota "
                            "reached. Resets in 2h30m10s.",
                   "ingest": False, "rc": 1})
        t0 = time.time()
        with self.assertRaises(agy_attach.QuotaPaused) as cm:
            self.ask()
        self.assertEqual(cm.exception.reset_seconds, 9010)
        self.assertGreaterEqual(cm.exception.resume_after_epoch, t0 + 9010)
        self.assertIn("RESOURCE_EXHAUSTED", cm.exception.raw)
        # Raised on the first attempt: no sleeping, no burnt content retry.
        self.assertEqual(self.calls_made(), 1)

    def test_quota_from_cli_log(self):
        """agy retries a 429 internally and can still hand back a plausible
        envelope; the server's own words only survive in the cli log."""
        self.conf({"response": '{"canary": "ok"}', "tools": ["view_file"],
                   "log": REAL_QUOTA_LINE})
        with self.assertRaises(agy_attach.QuotaPaused) as cm:
            self.ask()
        self.assertEqual(cm.exception.reset_seconds, 13 * 3600 + 33 * 60 + 26)

    def test_quota_from_stderr(self):
        self.conf({"status": "ERROR", "ingest": False,
                   "stderr": "agent executor error: RESOURCE_EXHAUSTED "
                             "(code 429): reset after 45m"})
        with self.assertRaises(agy_attach.QuotaPaused) as cm:
            self.ask()
        self.assertEqual(cm.exception.reset_seconds, 2700)

    def test_page_429_in_the_answer_is_not_quota(self):
        """The 042 quiz cites "StahlNeuro p. 429". The old `"429" in stdout`
        test would have paused the whole backlog on a page number."""
        self.conf({"response": '{"said": "StahlNeuro p. 429", "n": 429}',
                   "tools": ["view_file"]})
        res = self.ask()
        self.assertTrue(res.ok, res.diagnostic)
        self.assertEqual(res.data["n"], 429)

    def test_quota_reason_sources(self):
        self.assertIsNone(agy_attach.quota_reason(
            {"status": "SUCCESS", "response": "quota reached, 429, "
                                              "RESOURCE_EXHAUSTED"}, "", None))
        self.assertIsNotNone(agy_attach.quota_reason(
            {"status": "SUCCESS"}, "", "x RESOURCE_EXHAUSTED y"))
        self.assertIsNotNone(agy_attach.quota_reason(
            {"error": {"code": 429, "msg": "quota reached"}}, "", None))

    def test_parse_reset_seconds(self):
        p = agy_attach.parse_reset_seconds
        self.assertEqual(p("Resets in 13h33m26s."), 13 * 3600 + 33 * 60 + 26)
        self.assertEqual(p("quota will reset after 3h45m10s"), 13510)
        self.assertEqual(p("Resets in 45m"), 2700)
        self.assertEqual(p("RESETS IN 30S"), 30)
        self.assertEqual(p("resets in 2h"), 7200)
        self.assertIsNone(p("no reset time here"))
        self.assertIsNone(p(""))


class TestIngestionProof(AgyAttachCase):

    def test_missing_tempmedia_retries_then_fails(self):
        """The 2026-09-18 run that answered from memory: SUCCESS, well-formed
        JSON, an invented verbatim quote — and no .tempmediaStorage."""
        self.conf({"response": '{"canary": "invented"}', "ingest": False,
                   "tools": []})
        res = self.ask()
        self.assertFalse(res.ok)
        self.assertFalse(res.tempmedia_ok)
        self.assertEqual(res.attempts, 2)
        self.assertEqual(self.calls_made(), 2)
        self.assertIn("not ingested", res.diagnostic)
        self.assertIn("bundle.pdf", res.diagnostic)
        self.assertEqual(len(res.history), 2)

    def test_retry_after_failed_ingestion_can_succeed(self):
        self.conf({"response": '{"canary": "invented"}', "ingest": False,
                   "tools": []},
                  {"response": '{"canary": "ok"}', "tools": ["view_file"]})
        res = self.ask()
        self.assertTrue(res.ok, res.diagnostic)
        self.assertEqual(res.attempts, 2)

    def test_a_different_pdf_does_not_count(self):
        """sha256, not "a file of about the right size appeared"."""
        other = self.tmp / "src" / "other.pdf"
        other.write_bytes(b"%PDF-1.4\nsome other book\n%%EOF")
        proof = agy_attach.tempmedia_proof("nope", [other], gemini_root=self.root)
        self.assertEqual(proof, {"other.pdf": False})
        self.assertFalse(agy_attach.tempmedia_ok(proof))

    def test_non_pdf_is_recorded_not_required(self):
        media = self.root / "brain" / "c1" / ".tempmediaStorage"
        media.mkdir(parents=True)
        (media / "media_0.pdf").write_bytes(self.bundle.read_bytes())
        proof = agy_attach.tempmedia_proof("c1", [self.bundle, self.artifacts],
                                           gemini_root=self.root)
        self.assertEqual(proof, {"bundle.pdf": True, "artifacts.md": None})
        self.assertTrue(agy_attach.tempmedia_ok(proof))


class TestToolAudit(AgyAttachCase):

    def test_forbidden_tool_retries(self):
        self.conf({"response": '{"canary": "ok"}',
                   "tools": ["view_file", "run_command"]})
        res = self.ask()
        self.assertFalse(res.ok)
        self.assertEqual(res.attempts, 2)
        self.assertIn("forbidden tool run_command", res.diagnostic)
        self.assertEqual(res.tool_audit, ["view_file", "run_command"])

    def test_view_file_outside_the_workspace_is_forbidden(self):
        """What conversation 93c1ced2 did when its ref did not attach: read its
        own brain/ directory, then hunt through C:\\Users for the PDF."""
        outside = str(self.tmp / "src" / "bundle.pdf")
        self.conf({"response": '{"canary": "ok"}',
                   "tools": [{"name": "view_file",
                              "args": {"AbsolutePath": outside}}]})
        res = self.ask()
        self.assertFalse(res.ok)
        self.assertIn("outside the workspace", res.diagnostic)

    def test_unreadable_db_fails_closed(self):
        """"We could not check" is not "it was clean" (§2.3)."""
        names, problems = agy_attach.audit_tools("no-such-conversation",
                                                 gemini_root=self.root)
        self.assertEqual(names, [])
        self.assertTrue(problems)

    def test_no_tool_steps_is_clean(self):
        self.conf({"response": '{"canary": "ok"}', "tools": []})
        res = self.ask()
        self.assertTrue(res.ok, res.diagnostic)
        self.assertEqual(res.tool_audit, [])


class TestCommandLineGuard(AgyAttachCase):

    def test_argv_length_guard(self):
        """Measured on the real thing: CreateProcess truncates at 32,767 and
        says nothing, so a judge would answer with half its schema."""
        many = self.tmp / "many"
        many.mkdir()
        files = []
        for i in range(400):
            p = many / ("bundle_%03d_%s.pdf" % (i, "x" * 48))
            p.write_bytes(b"%PDF-1.4\n")
            files.append(p)
        with self.assertRaises(ValueError) as cm:
            agy_attach.ask_with_files("short prompt", files, workspace=self.ws,
                                      exe=self.exe, gemini_root=self.root)
        self.assertIn("command line", str(cm.exception))
        self.assertIn(str(agy_attach.MAX_CMDLINE), str(cm.exception))
        self.assertEqual(self.calls_made(), 0)

    def test_oversized_prompt_is_refused(self):
        with self.assertRaises(ValueError):
            self.ask_prompt("x" * (agy_attach.MAX_INLINE_PROMPT + 1))

    def ask_prompt(self, prompt):
        return agy_attach.ask_with_files(prompt, [self.bundle], workspace=self.ws,
                                         exe=self.exe, gemini_root=self.root)


class TestTimeout(AgyAttachCase):

    def test_timeout_kills_the_process_tree(self):
        self.conf({"sleep": 30, "ingest": False, "tools": []})
        killed = []
        real_kill = agy_attach._kill_tree

        def spy(proc):
            killed.append(proc.pid)
            real_kill(proc)          # still has to die, or communicate() hangs

        self.addCleanup(setattr, agy_attach, "_kill_tree", real_kill)
        agy_attach._kill_tree = spy

        res = self.ask(attempts=1, timeout_s=2)
        self.assertFalse(res.ok)
        self.assertEqual(len(killed), 1)
        self.assertIn("killed the process tree", res.diagnostic)
        self.assertLess(res.duration_s, 25)

    def test_kill_argv(self):
        argv = agy_attach._kill_argv(1234)
        if os.name == "nt":
            self.assertEqual(argv, ["taskkill", "/T", "/F", "/PID", "1234"])
        else:
            self.assertIsNone(argv)


class TestWorkspace(AgyAttachCase):

    def test_workspace_is_fresh_and_outside_the_repo(self):
        a = agy_attach.make_workspace("study-042-catatonia", "A", base=self.tmp)
        b = agy_attach.make_workspace("study-042-catatonia", "A", base=self.tmp)
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_dir() and b.is_dir())
        self.assertEqual(a.parent, self.tmp / "qc" / "study-042-catatonia")
        repo = Path(agy_attach.__file__).resolve().parents[1]
        for ws in (a, b):
            self.assertNotIn(str(repo).lower(), str(ws).lower())

    def test_tag_is_reduced_to_a_directory_name(self):
        ws = agy_attach.make_workspace("../../etc/passwd", "A", base=self.tmp)
        self.assertEqual(ws.parent.name, "etc-passwd")

    def test_stage_copies_exactly_the_call_files(self):
        staged = agy_attach.stage([self.bundle, self.artifacts], self.ws)
        self.assertEqual([p.name for p in staged], ["bundle.pdf", "artifacts.md"])
        self.assertEqual(sorted(p.name for p in self.ws.iterdir()),
                         ["artifacts.md", "bundle.pdf"])
        self.assertEqual((self.ws / "bundle.pdf").read_bytes(),
                         self.bundle.read_bytes())

    def test_stage_refuses_a_basename_collision(self):
        clash = self.tmp / "other" / "bundle.pdf"
        clash.parent.mkdir()
        clash.write_bytes(b"%PDF-1.4\nanother chapter\n")
        with self.assertRaises(ValueError):
            agy_attach.stage([self.bundle, clash], self.ws)

    def test_missing_attachment_is_reported_not_run(self):
        res = agy_attach.ask_with_files("Judge.", [self.tmp / "gone.pdf"],
                                        workspace=self.ws, exe=self.exe,
                                        gemini_root=self.root)
        self.assertFalse(res.ok)
        self.assertIn("attachment missing", res.diagnostic)
        self.assertEqual(self.calls_made(), 0)


class TestCleanup(AgyAttachCase):

    def test_cleanup_removes_the_conversation_and_workspace(self):
        brain = self.root / "brain" / "c1" / ".tempmediaStorage"
        brain.mkdir(parents=True)
        (brain / "media_0.pdf").write_bytes(b"x" * 1000)
        convs = self.root / "conversations"
        convs.mkdir()
        (convs / "c1.db").write_bytes(b"y" * 500)
        (convs / "c1.db-wal").write_bytes(b"z" * 100)
        freed = agy_attach.cleanup("c1", gemini_root=self.root, workspace=self.ws)
        self.assertGreaterEqual(freed, 1600)
        self.assertFalse((self.root / "brain" / "c1").exists())
        self.assertFalse((convs / "c1.db").exists())
        self.assertFalse((convs / "c1.db-wal").exists())
        self.assertFalse(self.ws.exists())

    def test_cleanup_of_nothing_is_not_an_error(self):
        self.assertEqual(agy_attach.cleanup("never-existed", gemini_root=self.root), 0)


class TestExecutableDiscovery(AgyAttachCase):
    """The .cmd shim path: agy on this machine is found through PATH.

    Only short arguments go through cmd.exe here. A judge prompt carries
    newlines and quotes, which a .cmd wrapper would mangle — the module is
    always given a real argv, and the tests inject one.
    """

    def test_agy_exe_and_version_through_a_cmd_shim(self):
        if os.name != "nt":
            self.skipTest("the .cmd shim is a Windows path")
        shim_dir = self.tmp / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "agy.cmd"
        shim.write_text('@echo off\r\n"%s" "%s" %%*\r\n'
                        % (sys.executable, self.fake), encoding="utf-8")
        old = os.environ["PATH"]
        os.environ["PATH"] = str(shim_dir) + os.pathsep + old
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        self.assertEqual(Path(agy_attach.agy_exe()).resolve(), shim.resolve())
        self.assertEqual(agy_attach.agy_version(agy_attach.agy_exe()),
                         "agy version 9.9.9-fake")

    def test_version_is_read_once_per_process(self):
        self.assertEqual(agy_attach.agy_version(self.exe), "agy version 9.9.9-fake")
        self.fake.write_text("raise SystemExit(7)", encoding="utf-8")
        self.assertEqual(agy_attach.agy_version(self.exe), "agy version 9.9.9-fake")

    def test_unreadable_version_is_recorded_as_unknown(self):
        self.assertEqual(agy_attach.agy_version([sys.executable, "-c", "raise SystemExit(3)"]),
                         "unknown")


if __name__ == "__main__":
    unittest.main()
