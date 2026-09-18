#!/usr/bin/env python3
"""External Linux probes and GitHub incident reconciliation; standard library only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


def load_targets(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict) or set(config) != {"targets"} or not isinstance(config["targets"], list):
        raise ValueError('Configuration must contain only a "targets" array')
    targets, ids = [], set()
    for source in config["targets"]:
        if not isinstance(source, dict):
            raise ValueError("Each target must be an object")
        t = dict(source)
        if not isinstance(t.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", t["id"]) or t["id"] in ids:
            raise ValueError("Target IDs must be unique lowercase slugs (1–80 characters)")
        ids.add(t["id"])
        kind = t.get("type")
        if kind not in ("icmp", "tcp", "http") or type(t.get("family")) is not int or t["family"] not in (4, 6):
            raise ValueError(f'{t["id"]}: require type icmp/tcp/http and family 4/6')
        allowed = {"id", "type", "family", "enabled", "timeout", "attempts"}
        allowed |= {"url", "expected_status"} if kind == "http" else {"host"}
        if kind == "tcp":
            allowed.add("port")
        if set(t) - allowed:
            raise ValueError(f'{t["id"]}: unknown fields {set(t) - allowed}')
        for key, default, low, high in (("timeout", 10, 1, 30), ("attempts", 2, 1, 3)):
            t.setdefault(key, default)
            if type(t[key]) is not int or not low <= t[key] <= high:
                raise ValueError(f'{t["id"]}: invalid {key}')
        t.setdefault("enabled", True)
        if type(t["enabled"]) is not bool:
            raise ValueError("enabled must be boolean")
        if kind == "http":
            url = urllib.parse.urlsplit(t.get("url", ""))
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.fragment:
                raise ValueError("HTTP targets need an http(s) URL without credentials or fragments")
            _ = url.port
            host = url.hostname
            t.setdefault("expected_status", [200])
            if not isinstance(t["expected_status"], list) or not t["expected_status"] or any(type(s) is not int or not 100 <= s <= 599 for s in t["expected_status"]):
                raise ValueError("expected_status must be a nonempty list of HTTP status codes")
        else:
            host = t.get("host")
            if not isinstance(host, str) or not host or not re.fullmatch(r"[a-zA-Z0-9_.:%-]+", host) or host.startswith("-"):
                raise ValueError("Invalid host")
            if kind == "tcp" and (type(t.get("port")) is not int or not 1 <= t["port"] <= 65535):
                raise ValueError("TCP port must be 1–65535")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if address.version != t["family"]:
                raise ValueError("Literal IP address does not match family")
        if t["enabled"]:
            targets.append(t)
    if len(targets) > 50:
        raise ValueError("Maximum 50 enabled probes per run")
    return targets


def command(args, timeout):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def ipv6_ready():
    result = command(["ip", "-6", "route", "show", "default"], 5)
    return result.returncode == 0 and bool(result.stdout.strip())


def probe_once(t):
    timeout = t["timeout"]
    family = str(t["family"])
    if t["type"] == "icmp":
        args = ["ping", "-" + family, "-n", "-c", "2", "-i", "0.2", "-W", str(timeout), "--", t["host"]]
    elif t["type"] == "tcp":
        # The parent timeout also bounds DNS resolution and all candidate addresses.
        args = [sys.executable, __file__, "--tcp", t["host"], str(t["port"]), family, str(timeout)]
    else:
        args = ["curl", "--disable", "-" + family, "--silent", "--show-error", "--noproxy", "*",
                "--connect-timeout", str(timeout), "--max-time", str(timeout),
                "--proto", "=http,https", "--output", "/dev/null", "--write-out", "%{http_code}", "--url", t["url"]]
    try:
        result = command(args, timeout)
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout}s"
    if result.returncode:
        # Do not echo server-controlled output or URL secrets into GitHub issues/logs.
        return False, f'{t["type"]} exited with code {result.returncode}'
    if t["type"] == "http":
        status = result.stdout.strip()
        ok = status.isdigit() and int(status) in t["expected_status"]
        return ok, f"HTTP status {status if status.isdigit() else 'invalid'}"
    return True, "Probe succeeded"


def probe(t):
    for _ in range(t["attempts"]):
        ok, detail = probe_once(t)
        if ok:
            return t, "UP", detail
    return t, "DOWN", detail


class GitHub:
    def __init__(self):
        self.base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.token = os.environ["GITHUB_TOKEN"]

    def request(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, method=method, headers={
            "Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def open_issues(self):
        issues = []
        page = 1
        while True:
            batch = self.request("GET", f"/repos/{self.repo}/issues?state=open&per_page=100&page={page}")
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                return issues
            page += 1


def reconcile(api, results, run_url):
    issues = api.open_issues()
    for t, state, detail in results:
        if state == "UNKNOWN":
            continue
        marker = f'<!-- server-monitor:v1:{t["id"]} -->'
        matching = [i for i in issues if (i.get("body") or "").splitlines()[:1] == [marker]
                    and i.get("user", {}).get("type") == "Bot"]
        prefix = f"/repos/{api.repo}/issues"
        if state == "DOWN" and not matching:
            api.request("POST", prefix, {
                "title": f'[Monitor] {t["id"]} DOWN (IPv{t["family"]} / {t["type"]})',
                "body": f'{marker}\n\nProbe **{t["id"]}** failed after {t["attempts"]} attempts.\n\n{detail}\n\n[Workflow run]({run_url})\n\nThis issue closes automatically after a successful probe.'})
        elif state == "UP":
            for issue in matching:
                api.request("POST", f'{prefix}/{issue["number"]}/comments', {
                    "body": f'Recovered at {datetime.datetime.now(datetime.timezone.utc).isoformat()}.\n\n{detail}\n\n[Workflow run]({run_url})'})
                api.request("PATCH", f'{prefix}/{issue["number"]}', {"state": "closed", "state_reason": "completed"})


def tcp_child(host, port, family, timeout):
    af = socket.AF_INET if family == "4" else socket.AF_INET6
    for item in socket.getaddrinfo(host, int(port), af, socket.SOCK_STREAM):
        try:
            with socket.socket(item[0], item[1], item[2]) as connection:
                connection.settimeout(int(timeout))
                connection.connect(item[4])
                return 0
        except OSError:
            continue
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="monitoring/targets.json")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Run probes without GitHub writes")
    args = parser.parse_args()
    targets = load_targets(args.config)
    if args.validate:
        print(f"Valid configuration: {len(targets)} enabled probes")
        return 0
    if not targets:
        summary = "No monitoring targets configured. Edit monitoring/targets.json.\n"
        print(summary)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(summary)
        return 0
    required = {"ip"} if any(t["family"] == 6 for t in targets) else set()
    required |= {"ping" for t in targets if t["type"] == "icmp"}
    required |= {"curl" for t in targets if t["type"] == "http"}
    missing = sorted(name for name in required if not shutil.which(name))
    if missing:
        raise ValueError("Missing runner tools: " + ", ".join(missing))
    v6 = not any(t["family"] == 6 for t in targets) or ipv6_ready()
    runnable = [t for t in targets if t["family"] != 6 or v6]
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(probe, runnable))
    results += [(t, "UNKNOWN", "Runner has no IPv6 default route; incident state unchanged")
                for t in targets if t["family"] == 6 and not v6]
    summary = "| Probe | Family | Status | Detail |\n| --- | --- | --- | --- |\n"
    summary += "".join(f'| {t["id"]} | IPv{t["family"]} | {state} | {detail} |\n' for t, state, detail in results)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(summary)
    if not args.dry_run:
        api = GitHub()
        run_url = f'{os.environ.get("GITHUB_SERVER_URL", "https://github.com")}/{api.repo}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
        reconcile(api, results, run_url)
    return int(any(state != "UP" for _, state, _ in results))


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--tcp":
            sys.exit(tcp_child(*sys.argv[2:]))
        sys.exit(main())
    except urllib.error.HTTPError as error:
        print(f"GitHub API failed: HTTP {error.code}", file=sys.stderr)
        sys.exit(2)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(f"Monitor could not complete ({type(error).__name__}); check configuration, runner and API access.", file=sys.stderr)
        sys.exit(2)
