#!/usr/bin/env python3
"""Lupio Device Flow and read-only REST probe. Never emits PR content or tokens."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPSConnection, RemoteDisconnected
import json
import re
import subprocess
import sys
import os
from pathlib import Path
from uuid import uuid4
from threading import Lock, local
import time
from urllib.parse import urlencode, urlsplit


HOST = "api.github.com"
TEAM = re.compile(r"[\w-]+/[\w-]+", re.ASCII)


class Probe:
    def __init__(self, token):
        self.token = token
        self.connections = local()
        self.metrics_lock = Lock()
        self.http_error = None
        self.cache = {}

    def reset_metrics(self):
        self.status = {}
        self.bytes = 0
        self.rate = {}
        self.started = time.monotonic()

    def get(self, path, group):
        with self.metrics_lock:
            if self.http_error:
                raise RuntimeError(self.http_error)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Lupio-API-probe",
        }
        cached = self.cache.get(path)
        if cached:
            headers["If-None-Match"] = cached[0]
        if not hasattr(self.connections, "http"):
            self.connections.http = HTTPSConnection(HOST, timeout=20)
        connection = self.connections.http
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
        except RemoteDisconnected:
            # A reused connection can close before delivering a response.
            connection.close()
            with self.metrics_lock:
                self.status.setdefault(group, Counter())["disconnected"] += 1
                if self.http_error:
                    raise RuntimeError(self.http_error) from None
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
        body = response.read()
        status = response.status
        resource = response.getheader("X-RateLimit-Resource")
        remaining = response.getheader("X-RateLimit-Remaining")
        with self.metrics_lock:
            self.status.setdefault(group, Counter())[status] += 1
            self.bytes += len(body)
            if resource and remaining is not None:
                self.rate[resource] = {
                    "limit": int(response.getheader("X-RateLimit-Limit")),
                    "remaining": int(remaining),
                    "reset": int(response.getheader("X-RateLimit-Reset")),
                }
        if status == 304 and cached:
            return cached[1:]
        if status != 200:
            sso = response.getheader("X-GitHub-SSO", "").split(";")[0]
            retry_after = response.getheader("Retry-After", "")
            with self.metrics_lock:
                if self.http_error is None:
                    self.http_error = (
                        f"{group}: HTTP {status}; SSO={sso!r}; Retry-After={retry_after!r}; "
                        f"{resource} remaining={remaining}"
                    )
                raise RuntimeError(self.http_error)
        data = json.loads(body)
        next_page = re.search(r'<([^>]+)>; rel="next"', response.getheader("Link", ""))
        next_path = None
        if next_page:
            url = urlsplit(next_page[1])
            if url.scheme != "https" or url.netloc != HOST:
                raise RuntimeError("Unexpected pagination host")
            next_path = url.path + "?" + url.query
        etag = response.getheader("ETag")
        if etag and group != "rate-limit":
            self.cache[path] = (etag, data, next_path)
        return data, next_path

    def pages(self, path, group, *, search=False, key=None):
        seen = set()
        while path:
            if path in seen:
                raise RuntimeError(f"{group}: pagination loop")
            seen.add(path)
            data, path = self.get(path, group)
            if search:
                if data["incomplete_results"] or data["total_count"] > 1000:
                    raise RuntimeError(f"{group}: incomplete or capped search")
            yield from data[key] if key else data

    def search(self, query, group):
        path = "/search/issues?" + urlencode({"q": query, "per_page": 100})
        return {(item["repository_url"].split("/repos/", 1)[1], item["number"])
                for item in self.pages(path, group, search=True, key="items")}

    def report(self, **fields):
        print(json.dumps({
            **fields, "seconds": round(time.monotonic() - self.started, 2),
            "status": self.status, "body_bytes": self.bytes, "rate": self.rate,
        }), flush=True)


def inspect_pull(probe, pr, current, login, selected_team):
    repo, number = pr
    pull = f"/repos/{repo}/pulls/{number}"
    issue = f"/repos/{repo}/issues/{number}"
    metadata, _ = probe.get(pull, "pull")
    is_open = metadata["state"] == "open"
    requests, _ = probe.get(pull + "/requested_reviewers", "requests")
    has_personal = any(user["login"].lower() == login.lower() for user in requests["users"])
    org, slug = selected_team.lower().split("/")
    has_team = repo.split("/")[0].lower() == org and any(
        request["slug"].lower() == slug for request in requests["teams"]
    )
    for group, path in (
        ("timeline", issue + "/timeline"),
        ("conversation", issue + "/comments"),
        ("inline", pull + "/comments"),
    ):
        for _ in probe.pages(path + "?per_page=100", group):
            pass
    reviews = list(probe.pages(pull + "/reviews?per_page=100", "reviews"))
    reviewed_without_request = (
        is_open and not has_personal and not has_team and pr not in current
        and any(review["submitted_at"] and (review["user"] or {}).get("login") == login
                for review in reviews)
    )
    return is_open, has_personal, has_team, reviewed_without_request


def device_post(path, fields):
    connection = HTTPSConnection("github.com", timeout=20)
    try:
        connection.request("POST", path, urlencode(fields), {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"GitHub Device Flow: HTTP {response.status}")
        return json.loads(response.read())
    finally:
        connection.close()


def device_token(client_id, *, include_refresh=False):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", client_id):
        raise RuntimeError("Invalid GitHub App Client ID")
    device = device_post("/login/device/code", {"client_id": client_id})
    if "error" in device:
        raise RuntimeError(f"Device code request failed: {device['error']}")
    print(f"Open https://github.com/login/device and enter {device['user_code']}",
          flush=True)
    expires = time.monotonic() + device["expires_in"]
    interval = device["interval"]
    while time.monotonic() + interval < expires:
        time.sleep(interval)
        token = device_post("/login/oauth/access_token", {
            "client_id": client_id,
            "device_code": device["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if "access_token" in token:
            if not token["access_token"].startswith("ghu_"):
                raise RuntimeError("Expected a GitHub App user access token")
            return token if include_refresh else token["access_token"]
        if token.get("error") == "slow_down":
            interval = max(interval + 5, token.get("interval", 0))
        elif token.get("error") != "authorization_pending":
            raise RuntimeError(f"Device authorization failed: {token.get('error', 'unknown')}")
    raise RuntimeError("Device authorization expired")


def credential_store(operation, entry, secret=None):
    if sys.platform == "darwin":
        command = ["swift", str(Path(__file__).with_name("keychain-probe.swift")),
                   operation, entry]
    elif sys.platform.startswith("linux"):
        tool = os.environ.get("LUPIO_SECRET_TOOL", "secret-tool")
        action = {"store": ["store", "--label=Lupio Device Flow probe"],
                  "read": ["lookup"], "delete": ["clear"]}[operation]
        command = [tool, *action, "service", "lupio-device-flow-probe", "account", entry]
    else:
        raise RuntimeError("No credential store for this OS")
    result = subprocess.run(command, input=secret, text=True, capture_output=True,
                            timeout=90, check=False)
    if result.returncode:
        raise RuntimeError(f"Credential store {operation} failed ({result.returncode})")
    return result.stdout


def check_stored_token(entry):
    credentials = json.loads(credential_store("read", entry))
    probe = Probe(credentials["access_token"])
    probe.reset_metrics()
    probe.get("/user", "restored-user")
    probe.report(phase="credential-read", github_user_verified=True,
                 refresh_token_present=bool(credentials.get("refresh_token")))


def verify_credential_store(client_id):
    entry = uuid4().hex
    credentials = device_token(client_id, include_refresh=True)
    secret = json.dumps({name: credentials[name] for name in
                         ("access_token", "refresh_token", "expires_in", "refresh_token_expires_in")
                         if name in credentials})
    credential_store("store", entry, secret)
    try:
        subprocess.run([sys.executable, __file__, "--credential-read", entry], check=True)
    finally:
        credential_store("delete", entry)
    print(json.dumps({"phase": "credential-cleanup", "removed": True}), flush=True)


def verify_app(probe, selected_team, check_members=False):
    probe.reset_metrics()
    login = probe.get("/user", "user")[0]["login"]
    org, slug = selected_team.lower().split("/")
    teams = {f"{team['organization']['login']}/{team['slug']}".lower()
             for team in probe.pages("/user/teams?per_page=100", "teams")}
    installations = probe.pages("/user/installations?per_page=100", "installations",
                                key="installations")
    installation = next((item for item in installations
                         if item["account"]["login"].lower() == org), None)
    if not installation:
        raise RuntimeError("No app installation visible for the selected organization")
    permissions = installation["permissions"]
    expected = {"metadata": "read", "pull_requests": "read"}
    if check_members:
        expected["members"] = "read"
    if permissions != expected:
        raise RuntimeError(f"Expected installation permissions {expected}, received {permissions}")
    if installation["repository_selection"] != "selected":
        raise RuntimeError("Expected an installation limited to selected repositories")
    repos = {repo["full_name"].lower() for repo in probe.pages(
        f"/user/installations/{installation['id']}/repositories?per_page=100",
        "installation-repos", key="repositories") if repo["private"]}
    personal = probe.search("is:pr is:open is:private user-review-requested:@me",
                            "personal-search")
    team = probe.search(f"is:pr is:open is:private team-review-requested:{selected_team}",
                        "team-search")
    personal = {(repo, number) for repo, number in personal if repo.lower() in repos}
    team = {(repo, number) for repo, number in team if repo.lower() in repos}

    def confirm(candidates, matches):
        for repo, number in sorted(candidates):
            pull = f"/repos/{repo}/pulls/{number}"
            reviewers, _ = probe.get(pull + "/requested_reviewers", "requests")
            if matches(reviewers):
                metadata, _ = probe.get(pull, "pull")
                if metadata["state"] == "open" and metadata["base"]["repo"]["private"]:
                    return True
        return False

    personal_confirmed = confirm(personal, lambda reviewers: any(
        user["login"].lower() == login.lower() for user in reviewers["users"]
    ))
    team_confirmed = confirm(team, lambda reviewers: any(
        request["slug"].lower() == slug for request in reviewers["teams"]
    ))
    membership = {}
    if check_members:
        org_teams = list(probe.pages(f"/orgs/{org}/teams?per_page=100", "org-teams"))
        record, _ = probe.get(f"/orgs/{org}/teams/{slug}/memberships/{login}", "membership")
        membership = {
            "org_team_list_contains_target": any(item["slug"].lower() == slug for item in org_teams),
            "membership_state": record["state"],
        }
    probe.report(phase="device-flow", permissions=permissions,
                 team_visible=selected_team.lower() in teams, private_repos=len(repos),
                 personal_matches=len(personal), team_matches=len(team),
                 personal_confirmed=personal_confirmed, team_confirmed=team_confirmed,
                 **membership)
    if not (personal_confirmed and team_confirmed):
        raise RuntimeError("Device Flow succeeded but both private PR request types were not confirmed")
    if check_members and (
        selected_team.lower() not in teams
        or not membership["org_team_list_contains_target"]
        or membership["membership_state"] != "active"
    ):
        raise RuntimeError("The selected team was not confirmed by all membership checks")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team", help="Selected team as ORG/SLUG")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=1,
                        help="Parallel PRs (default: 1)")
    parser.add_argument("--client-id", help="GitHub App Client ID for a Device Flow access check")
    parser.add_argument("--check-members", action="store_true",
                        help="Verify additional Members: read and team membership via Device Flow")
    parser.add_argument("--credential-probe", action="store_true",
                        help="Test OS credential storage with a Device Flow token")
    parser.add_argument("--credential-read", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.credential_read:
        check_stored_token(args.credential_read)
        return
    if args.credential_probe:
        if not args.client_id:
            parser.error("--credential-probe requires --client-id")
        verify_credential_store(args.client_id)
        return
    if not args.team or not TEAM.fullmatch(args.team) or args.rounds < 1:
        parser.error("expected ORG/SLUG and a positive round count")
    if args.check_members and not args.client_id:
        parser.error("--check-members requires --client-id")

    if args.client_id:
        verify_app(Probe(device_token(args.client_id)), args.team, args.check_members)
        return

    token = subprocess.check_output(
        ["gh", "auth", "token", "--hostname", "github.com"], text=True
    ).strip()
    probe = Probe(token)
    probe.reset_metrics()
    login = probe.get("/user", "user")[0]["login"]
    teams = {f"{team['organization']['login']}/{team['slug']}".lower()
             for team in probe.pages("/user/teams?per_page=100", "teams")}
    if args.team.lower() not in teams:
        raise RuntimeError("Selected team is absent from the authenticated team list")
    reviewed = probe.search(f"is:pr is:open reviewed-by:{login}", "reviewed-fixture")
    baseline = probe.get("/rate_limit", "rate-limit")[0]["resources"]["core"]
    probe.report(phase="setup", teams=len(teams), open_reviewed=len(reviewed),
                 core_baseline=baseline)

    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        measure(probe, args, workers, login, reviewed, baseline)


def measure(probe, args, workers, login, reviewed, baseline):
    tracked = None
    for index in range(args.rounds):
        probe.reset_metrics()
        personal = probe.search("is:pr is:open user-review-requested:@me", "personal-search")
        team = probe.search(f"is:pr is:open team-review-requested:{args.team}", "team-search")
        if tracked is None:
            # Preserve reviewed fixtures separately from current request discovery.
            tracked = list(dict.fromkeys(sorted(personal) + sorted(reviewed - personal - team)
                                        + sorted(team) + sorted(reviewed)))[:30]
            if not tracked:
                raise RuntimeError("No pull requests found for the probe")
        current = personal | team

        def inspect(pr):
            return inspect_pull(probe, pr, current, login, args.team)

        observations = list(workers.map(inspect, tracked))
        open_count, confirmed_personal, confirmed_team, reviewed_without_request = (
            sum(flags) for flags in zip(*observations)
        )
        core = probe.get("/rate_limit", "rate-limit")[0]["resources"]["core"]
        core_used = core["used"] - baseline["used"] if core["reset"] == baseline["reset"] else None
        baseline = core
        probe.report(phase="poll", round=index + 1, workers=args.workers,
                     tracked=len(tracked), open=open_count,
                     search_personal=len(personal), search_team=len(team),
                     confirmed_personal=confirmed_personal, confirmed_team=confirmed_team,
                     reviewed_without_request=reviewed_without_request, core_used_delta=core_used)
        if index + 1 < args.rounds:
            time.sleep(max(0, 60 - (time.monotonic() - probe.started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
