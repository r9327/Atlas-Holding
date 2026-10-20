import unittest

from holding.github_full_evidence import GitHubEvidenceError
from holding.service_health import (
    MAX_RETRY_ATTEMPTS,
    ProbeOutcome,
    ServiceStatus,
    initial_service_health,
    record_probe,
    retry_allowed,
)


class ServiceHealthTests(unittest.TestCase):
    def test_each_target_service_is_tracked_independently(self):
        github = record_probe(
            initial_service_health("github"),
            outcome=ProbeOutcome.NETWORK_ERROR,
            observed_at=10,
        )
        context7 = initial_service_health("context7")
        self.assertEqual(github.status, ServiceStatus.DEGRADED)
        self.assertEqual(context7.status, ServiceStatus.UNKNOWN)
        with self.assertRaisesRegex(GitHubEvidenceError, "Unsupported"):
            initial_service_health("wifi")

    def test_two_successes_are_required_to_be_online(self):
        state = initial_service_health("github")
        state = record_probe(state, outcome=ProbeOutcome.SUCCESS, observed_at=10)
        self.assertEqual(state.status, ServiceStatus.PROBING)
        state = record_probe(state, outcome=ProbeOutcome.SUCCESS, observed_at=11)
        self.assertEqual(state.status, ServiceStatus.ONLINE)

    def test_failure_resets_success_hysteresis(self):
        state = record_probe(
            initial_service_health("github"),
            outcome=ProbeOutcome.SUCCESS,
            observed_at=10,
        )
        state = record_probe(
            state, outcome=ProbeOutcome.SERVER_ERROR, observed_at=11)
        self.assertEqual(state.consecutive_successes, 0)
        state = record_probe(state, outcome=ProbeOutcome.SUCCESS, observed_at=12)
        self.assertEqual(state.status, ServiceStatus.PROBING)

    def test_transient_retry_is_delayed_and_bounded(self):
        state = initial_service_health("firecrawl")
        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            state = record_probe(
                state,
                outcome=ProbeOutcome.NETWORK_ERROR,
                observed_at=attempt * 100,
            )
        self.assertEqual(state.status, ServiceStatus.BLOCKED)
        self.assertEqual(state.reason, "RETRY_EXHAUSTED")
        self.assertFalse(retry_allowed(state, now_epoch=10_000))

    def test_repeated_transient_failure_marks_only_that_service_offline(self):
        state = initial_service_health("github")
        state = record_probe(
            state, outcome=ProbeOutcome.NETWORK_ERROR, observed_at=100)
        state = record_probe(
            state, outcome=ProbeOutcome.NETWORK_ERROR, observed_at=102)
        self.assertEqual(state.status, ServiceStatus.OFFLINE)

    def test_retry_cannot_run_before_backoff(self):
        state = record_probe(
            initial_service_health("github"),
            outcome=ProbeOutcome.SERVER_ERROR,
            observed_at=100,
        )
        self.assertFalse(retry_allowed(state, now_epoch=100))
        self.assertTrue(retry_allowed(state, now_epoch=101))

    def test_auth_failure_never_retries_automatically(self):
        state = record_probe(
            initial_service_health("github"),
            outcome=ProbeOutcome.AUTH_ERROR,
            observed_at=100,
        )
        self.assertEqual(state.status, ServiceStatus.BLOCKED)
        self.assertEqual(state.reason, "AUTHORIZATION_REQUIRED")
        self.assertFalse(retry_allowed(state, now_epoch=10_000))

    def test_quota_respects_server_retry_after(self):
        state = record_probe(
            initial_service_health("context7"),
            outcome=ProbeOutcome.QUOTA_ERROR,
            observed_at=100,
            retry_after=500,
        )
        self.assertFalse(retry_allowed(state, now_epoch=499))
        self.assertTrue(retry_allowed(state, now_epoch=500))

    def test_quota_retries_are_also_bounded(self):
        state = initial_service_health("context7")
        for attempt in range(MAX_RETRY_ATTEMPTS):
            observed_at = 100 + attempt * 100
            state = record_probe(
                state,
                outcome=ProbeOutcome.QUOTA_ERROR,
                observed_at=observed_at,
                retry_after=observed_at + 50,
            )
        self.assertEqual(state.reason, "QUOTA_RETRY_EXHAUSTED")
        self.assertFalse(retry_allowed(state, now_epoch=10_000))

    def test_stale_or_malformed_observations_fail_closed(self):
        state = record_probe(
            initial_service_health("github"),
            outcome=ProbeOutcome.SUCCESS,
            observed_at=100,
        )
        with self.assertRaisesRegex(GitHubEvidenceError, "stale"):
            record_probe(state, outcome=ProbeOutcome.SUCCESS, observed_at=100)
        with self.assertRaisesRegex(GitHubEvidenceError, "Retry-After"):
            record_probe(
                state,
                outcome=ProbeOutcome.SUCCESS,
                observed_at=101,
                retry_after=200,
            )


if __name__ == "__main__":
    unittest.main()
