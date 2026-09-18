import http.server
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import sys
import threading
import unittest
from unittest.mock import patch

import monitor


class FakeGitHub:
    repo = "owner/repo"

    def __init__(self, issues=()):
        self.issues = list(issues)
        self.calls = []

    def open_issues(self):
        return self.issues

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))


TARGET = {"id": "test-v4", "type": "icmp", "host": "127.0.0.1", "family": 4, "timeout": 3, "attempts": 2}


def incident(number=1):
    return {"number": number, "body": "<!-- server-monitor:v1:test-v4 -->\nIncident", "user": {"type": "Bot"}}


class ConfigTests(unittest.TestCase):
    def load(self, targets):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(json.dumps({"targets": targets}))
            return monitor.load_targets(path)

    def test_defaults(self):
        result = self.load([{"id": "web", "type": "http", "url": "https://example.com", "family": 6}])[0]
        self.assertEqual(result["expected_status"], [200])
        self.assertEqual(result["attempts"], 2)

    def test_empty_and_disabled(self):
        self.assertEqual(self.load([]), [])
        self.assertEqual(self.load([dict(TARGET, enabled=False)]), [])

    def test_reject_duplicates_and_invalid_fields(self):
        with self.assertRaises(ValueError):
            self.load([TARGET, TARGET])
        for fields in ({"family": 6}, {"timeout": 0}, {"attempts": 4}, {"host": "--help"},
                       {"type": "tcp", "port": 0}, {"enabled": "false"}, {"unexpected": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.load([dict(TARGET, **fields)])

    def test_reject_bad_http(self):
        for url in ("file:///etc/passwd", "https://user:pass@example.com", "https://example.com:bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.load([{"id": "web", "type": "http", "url": url, "family": 4}])


class IncidentTests(unittest.TestCase):
    def test_failure_creates_issue(self):
        api = FakeGitHub()
        monitor.reconcile(api, [(TARGET, "DOWN", "timeout")], "https://example.com/run")
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(api.calls[0][2]["body"].startswith("<!-- server-monitor:v1:test-v4 -->"))

    def test_persistent_failure_does_not_duplicate(self):
        api = FakeGitHub([incident()])
        monitor.reconcile(api, [(TARGET, "DOWN", "timeout")], "run")
        self.assertEqual(api.calls, [])

    def test_recovery_closes_only_matching_bot_issues(self):
        other = dict(incident(3), body="<!-- server-monitor:v1:other -->")
        human = dict(incident(4), user={"type": "User"})
        api = FakeGitHub([incident(), incident(2), other, human])
        monitor.reconcile(api, [(TARGET, "UP", "ok")], "run")
        self.assertEqual([c[1] for c in api.calls if c[0] == "PATCH"],
                         ["/repos/owner/repo/issues/1", "/repos/owner/repo/issues/2"])

    def test_unknown_preserves_incident(self):
        api = FakeGitHub([incident()])
        monitor.reconcile(api, [(TARGET, "UNKNOWN", "no route")], "run")
        self.assertEqual(api.calls, [])

    def test_pagination_excludes_pull_requests(self):
        api = object.__new__(monitor.GitHub)
        api.repo = "owner/repo"
        first = [{"number": i} for i in range(99)] + [{"pull_request": {}}]
        with patch.object(api, "request", side_effect=[first, [{"number": 100}]]) as request:
            self.assertEqual(len(api.open_issues()), 100)
            self.assertIn("page=2", request.call_args.args[1])


class ProbeTests(unittest.TestCase):
    def test_transient_failure_retried(self):
        with patch.object(monitor, "probe_once", side_effect=[(False, "fail"), (True, "ok")]) as probe:
            self.assertEqual(monitor.probe(TARGET)[1], "UP")
            self.assertEqual(probe.call_count, 2)

    def test_timeout_down(self):
        with patch.object(monitor, "command", side_effect=subprocess.TimeoutExpired("ping", 3)):
            self.assertEqual(monitor.probe(TARGET)[1], "DOWN")

    def test_family_is_forced(self):
        for family in (4, 6):
            with patch.object(monitor, "command", return_value=subprocess.CompletedProcess([], 0, "", "")) as command:
                monitor.probe_once(dict(TARGET, family=family))
                self.assertIn(f"-{family}", command.call_args.args[0])

    def test_ipv6_route_detection(self):
        for route, ready in (("", False), ("default via fe80::1 dev eth0", True)):
            with patch.object(monitor, "command", return_value=subprocess.CompletedProcess([], 0, route, "")):
                self.assertEqual(monitor.ipv6_ready(), ready)

    def test_missing_ipv6_route_is_unknown(self):
        target = dict(TARGET, host="::1", family=6)
        with patch("sys.argv", ["monitor.py", "--dry-run"]), patch.object(monitor, "load_targets", return_value=[target]), \
                patch.object(monitor.shutil, "which", return_value="tool"), patch.object(monitor, "ipv6_ready", return_value=False), \
                patch.object(monitor, "probe") as probe, patch.dict(monitor.os.environ, {}, clear=True):
            self.assertEqual(monitor.main(), 1)
            probe.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux iputils probe")
    def test_real_icmp_loopback(self):
        self.assertTrue(monitor.probe_once(TARGET)[0])
        self.assertTrue(monitor.probe_once(dict(TARGET, host="::1", family=6))[0])

    def test_real_tcp_ipv6(self):
        with socket.socket(socket.AF_INET6) as server:
            try:
                server.bind(("::1", 0))
            except OSError:
                self.skipTest("IPv6 loopback unavailable")
            server.listen()
            target = dict(TARGET, type="tcp", host="::1", family=6, port=server.getsockname()[1])
            self.assertTrue(monitor.probe_once(target)[0])

    def test_real_tcp_open_and_closed(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            target = dict(TARGET, type="tcp", port=port)
            self.assertTrue(monitor.probe_once(target)[0])
        self.assertFalse(monitor.probe_once(target)[0])

    def test_real_http_status_and_redirect(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(int(self.path[1:]))
                self.end_headers()
            def log_message(self, *args):
                pass
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for status, expected in ((200, True), (503, False), (302, False)):
                    target = dict(TARGET, type="http", url=f"http://127.0.0.1:{server.server_port}/{status}", expected_status=[200])
                    self.assertEqual(monitor.probe_once(target)[0], expected)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
