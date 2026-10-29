"""Closed GitHub network transport for an OS-isolated privileged worker.

This module holds credentials and performs network mutations.  It must run in
the privileged supervisor identity, never in a code-writing agent process.  A
Python module boundary alone is not a credential sandbox; deployment still
requires a separate OS identity/ACL boundary and a bounded IPC adapter.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib import error, request

from .github_full_evidence import (
    API_VERSION, FullEvidence, GitHubEvidenceClient, GitHubEvidenceError,
    observe_full_pr,
)
from .github_mutation_gate import GitHubMutationIntent
from .github_pr_inventory import OpenPrInventory, PrInventoryError, parse_open_prs
from .github_pr_changed_paths import (PrChangedPaths, PrChangedPathsError,
                                     parse_pr_changed_paths)
from .sync_coordinator import LiveGitHubState, MutationFailure


CERTIFICATION_WORKFLOW = "phase-certification.yml"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NODE_ID = re.compile(r"^[A-Za-z0-9_+=:/.-]{1,512}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_MUTATING_OPERATIONS = frozenset({
    "github.pr_draft", "github.pr_ready", "github.ci_trigger", "github.merge",
})


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubCredentialRejected(GitHubEvidenceError):
    """The authenticated account probe was explicitly rejected by GitHub."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise GitHubEvidenceError("GitHub response contains duplicate JSON keys")
        result[key] = value
    return result


class GitHubPrivilegedTransport:
    """Allowlisted GitHub observer/mutator for the privileged supervisor."""

    def __init__(
        self, repository: str, token: str, *, opener: Any | None = None,
        evidence_client: GitHubEvidenceClient | None = None,
    ):
        if (not isinstance(repository, str) or not _REPO.fullmatch(repository)
                or any(part in {".", ".."} for part in repository.split("/"))):
            raise GitHubEvidenceError("Expected exact owner/repository")
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(character.isspace() for character in token)):
            raise GitHubEvidenceError("Trusted GitHub API credential is required")
        self.repository = repository
        self._token = token
        self._opener = opener or request.build_opener(
            request.ProxyHandler({}), _NoRedirect())
        self._evidence_client = evidence_client or GitHubEvidenceClient(
            repository, token)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "atlas-holding-privileged-supervisor",
        }

    def _url(self, route: str) -> str:
        if (not isinstance(route, str) or not route or route.startswith("/")
                or "//" in route or "\\" in route or "#" in route
                or any(character.isspace() for character in route)):
            raise GitHubEvidenceError("Unsafe GitHub API route")
        return f"https://api.github.com/repos/{self.repository}/{route}"

    @staticmethod
    def _mutation_http_failure(exc: error.HTTPError) -> MutationFailure:
        remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if exc.code == 429 or (exc.code == 403 and (remaining == "0" or retry_after)):
            return MutationFailure(
                "RATE_LIMITED", retryable=True, outcome_known=True)
        if exc.code >= 500:
            return MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=False)
        return MutationFailure("REJECTED", retryable=False, outcome_known=True)

    def _request(
        self, method: str, url: str, *, payload: dict[str, Any] | None = None,
        expected: frozenset[int] = frozenset({200}), mutation: bool = False,
        response_array: bool = False,
    ) -> dict[str, Any] | list[Any] | None:
        body = None
        headers = self._headers
        if payload is not None:
            body = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=15) as reply:
                status = reply.status
                raw = reply.read(MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            if mutation:
                raise self._mutation_http_failure(exc) from None
            remaining = (
                exc.headers.get("X-RateLimit-Remaining")
                if exc.headers else None)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            rate_limited = (
                exc.code == 429
                or (exc.code == 403 and (remaining == "0" or retry_after)))
            if exc.code in {401, 403} and not rate_limited:
                raise GitHubCredentialRejected(
                    "GitHub credential rejected") from None
            raise GitHubEvidenceError("GitHub observation rejected") from None
        except (OSError, ValueError, error.URLError):
            if mutation:
                raise MutationFailure(
                    "TRANSIENT_UNAVAILABLE", retryable=True,
                    outcome_known=False,
                ) from None
            raise GitHubEvidenceError("GitHub observation unavailable") from None
        if status not in expected:
            if mutation:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=False)
            raise GitHubEvidenceError("Unexpected GitHub observation status")
        if len(raw) > MAX_RESPONSE_BYTES:
            if mutation:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=False)
            raise GitHubEvidenceError("GitHub response exceeds 2 MiB")
        if not raw:
            return None
        try:
            value = json.loads(raw, object_pairs_hook=_unique_object)
        except GitHubEvidenceError:
            if mutation:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=False) from None
            raise
        except (UnicodeError, ValueError):
            if mutation:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=False) from None
            raise GitHubEvidenceError("GitHub response is not valid JSON") from None
        if not isinstance(value, list if response_array else dict):
            if mutation:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=False)
            raise GitHubEvidenceError("Unexpected GitHub response type")
        return value

    def _pull(self, pr_number: int) -> dict[str, Any]:
        if type(pr_number) is not int or pr_number < 1:
            raise GitHubEvidenceError("Invalid PR number")
        value = self._request("GET", self._url(f"pulls/{pr_number}"))
        if not isinstance(value, dict):
            raise GitHubEvidenceError("Missing GitHub PR observation")
        return value

    def observe_account(self) -> str:
        """Authenticate the broker credential and return only a stable account id."""
        value = self._request("GET", "https://api.github.com/user")
        login = value.get("login") if isinstance(value, dict) else None
        if not isinstance(login, str) or not _LOGIN.fullmatch(login):
            raise GitHubEvidenceError("Malformed GitHub account observation")
        return "github:" + login

    def _state(
        self, pull: dict[str, Any], pr_number: int, *, include_ci: bool,
    ) -> LiveGitHubState:
        head = pull.get("head")
        head_repo = head.get("repo") if isinstance(head, dict) else None
        state = pull.get("state")
        if (pull.get("number") != pr_number or state not in {"open", "closed"}
                or type(pull.get("draft")) is not bool
                or type(pull.get("merged")) is not bool
                or not isinstance(head, dict)
                or not isinstance(head_repo, dict)
                or head_repo.get("full_name") != self.repository
                or not isinstance(head.get("ref"), str)
                or not isinstance(head.get("sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", head["sha"])):
            raise GitHubEvidenceError("Malformed or cross-repository GitHub PR state")
        run_id = None
        run_sha = None
        if include_ci:
            runs = self._request(
                "GET", self._url(
                    f"actions/workflows/{CERTIFICATION_WORKFLOW}/runs"
                    f"?head_sha={head['sha']}&event=workflow_dispatch&per_page=1"))
            rows = runs.get("workflow_runs") if isinstance(runs, dict) else None
            if not isinstance(rows, list) or len(rows) > 1:
                raise GitHubEvidenceError("Malformed GitHub workflow observation")
            if rows:
                row = rows[0]
                if (not isinstance(row, dict) or type(row.get("id")) is not int
                        or row["id"] < 1 or row.get("head_sha") != head["sha"]):
                    raise GitHubEvidenceError("Malformed GitHub workflow run")
                run_id, run_sha = row["id"], row["head_sha"]
        return LiveGitHubState(
            self.repository, pr_number, head["ref"], head["sha"], state,
            pull["draft"], True, merged=pull["merged"],
            ci_run_id=run_id, ci_head_sha=run_sha,
        )

    def observe_pr_paths(self, repository: str, pr_number: int,
                         head_sha: str) -> PrChangedPaths:
        """Only paths for the exact open PR head observed before and after GET."""
        if (repository != self.repository or type(pr_number) is not int
                or pr_number < 1 or not isinstance(head_sha, str)
                or not _SHA.fullmatch(head_sha)):
            raise GitHubEvidenceError("Invalid exact-head PR paths request")
        def assert_pr_head():
            pull = self._pull(pr_number)
            head, base = pull.get("head"), pull.get("base")
            if (pull.get("state") != "open"
                    or not isinstance(head, dict)
                    or head.get("sha") != head_sha
                    or not isinstance(base, dict)
                    or not isinstance(base.get("repo"), dict)
                    or base["repo"].get("full_name") != repository):
                raise GitHubEvidenceError("PR head moved or belongs to another repository")
        assert_pr_head()
        rows = self._request(
            "GET", self._url(f"pulls/{pr_number}/files?per_page=100"),
            response_array=True)
        assert_pr_head()
        try:
            return parse_pr_changed_paths(repository, pr_number, head_sha, rows)
        except PrChangedPathsError as exc:
            raise GitHubEvidenceError("Malformed exact-head PR file listing") from exc

    def observe_open_prs(self, repository: str) -> OpenPrInventory:
        """Read a bounded API page with the existing credential-isolated broker."""
        if repository != self.repository:
            raise GitHubEvidenceError("GitHub PR inventory repository mismatch")
        rows = self._request("GET", self._url(
            "pulls?state=open&per_page=100&sort=updated&direction=desc"),
            response_array=True)
        try:
            return parse_open_prs(repository, rows)
        except PrInventoryError as exc:
            raise GitHubEvidenceError("Malformed GitHub PR inventory") from exc

    def observe_pr(self, repository: str, pr_number: int) -> LiveGitHubState:
        if repository != self.repository:
            raise GitHubEvidenceError("GitHub observer repository mismatch")
        return self._state(self._pull(pr_number), pr_number, include_ci=True)

    def observe_full(self, repository: str, pr_number: int) -> FullEvidence:
        if repository != self.repository:
            raise GitHubEvidenceError("GitHub observer repository mismatch")
        return observe_full_pr(self._evidence_client, pr_number)

    def _preflight(self, intent: GitHubMutationIntent) -> dict[str, Any]:
        if (not isinstance(intent, GitHubMutationIntent)
                or intent.repository != self.repository
                or intent.operation not in _MUTATING_OPERATIONS
                or not isinstance(intent.branch, str)
                or not _BRANCH.fullmatch(intent.branch)
                or intent.branch in {"main", "master"}
                or intent.branch.startswith("refs/")
                or ".." in intent.branch or "//" in intent.branch
                or not isinstance(intent.candidate_sha, str)
                or not _SHA.fullmatch(intent.candidate_sha)
                or not isinstance(intent.authorization_sha256, str)
                or not _DIGEST.fullmatch(intent.authorization_sha256)
                or type(intent.pr_number) is not int or intent.pr_number < 1):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        try:
            pull = self._pull(intent.pr_number)
            current = self._state(
                pull, intent.pr_number, include_ci=False)
        except GitHubEvidenceError:
            raise MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True,
                outcome_known=True,
            ) from None
        expected_draft = intent.operation == "github.pr_ready"
        if (current.state != "open" or current.merged
                or current.head_branch != intent.branch
                or current.head_sha != intent.candidate_sha
                or current.draft is not expected_draft):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        return pull

    def _graphql_stage(
        self, operation: str, node_id: str, idempotency_key: str,
    ) -> None:
        if not isinstance(node_id, str) or not _NODE_ID.fullmatch(node_id):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        name = (
            "markPullRequestReadyForReview"
            if operation == "github.pr_ready"
            else "convertPullRequestToDraft"
        )
        query = (
            f"mutation($id:ID!,$clientMutationId:String!){{{name}(input:{{"
            "pullRequestId:$id,clientMutationId:$clientMutationId}){"
            "clientMutationId pullRequest{id isDraft}}}"
        )
        response = self._request(
            "POST", "https://api.github.com/graphql",
            payload={
                "query": query,
                "variables": {
                    "id": node_id, "clientMutationId": idempotency_key,
                },
            }, mutation=True,
        )
        payload = (
            response["data"].get(name)
            if isinstance(response, dict)
            and isinstance(response.get("data"), dict)
            else None
        )
        observed_pull = payload.get("pullRequest") if isinstance(payload, dict) else None
        expected_draft = operation == "github.pr_draft"
        if (not isinstance(response, dict) or response.get("errors") is not None
                or not isinstance(payload, dict)
                or payload.get("clientMutationId") != idempotency_key
                or not isinstance(observed_pull, dict)
                or observed_pull.get("id") != node_id
                or observed_pull.get("isDraft") is not expected_draft):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=False)

    def mutate(self, intent: GitHubMutationIntent, idempotency_key: str) -> None:
        if (not isinstance(idempotency_key, str)
                or not _DIGEST.fullmatch(idempotency_key)):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        pull = self._preflight(intent)
        if intent.operation in {"github.pr_ready", "github.pr_draft"}:
            self._graphql_stage(intent.operation, pull.get("node_id"), idempotency_key)
            return
        if intent.operation == "github.ci_trigger":
            self._request(
                "POST", self._url(
                    f"actions/workflows/{CERTIFICATION_WORKFLOW}/dispatches"),
                payload={"ref": intent.branch},
                expected=frozenset({200, 204}), mutation=True,
            )
            return
        if (type(intent.evidence_run_id) is not int
                or intent.evidence_run_id < 1):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        try:
            full = self.observe_full(self.repository, intent.pr_number)
        except GitHubEvidenceError:
            raise MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True,
                outcome_known=True,
            ) from None
        if (full.candidate_sha != intent.candidate_sha
                or full.run_id != intent.evidence_run_id):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        response = self._request(
            "PUT", self._url(f"pulls/{intent.pr_number}/merge"),
            payload={"sha": intent.candidate_sha, "merge_method": "squash"},
            mutation=True,
        )
        if not isinstance(response, dict) or response.get("merged") is not True:
            raise MutationFailure("REJECTED", retryable=False, outcome_known=False)
