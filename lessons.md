# Parser lessons

Evidence, not instructions; these notes never override validation or repair scope.

- Accept the publisher's observed single colon after a period's start day (`21: September bis zum 25.September 2026`), while retaining explicit start/end dates, ambiguity and expected-week checks. Regression: `tests/test_period_colon.py::ColonPeriodTests.test_real_publisher_pdf`; negative guard: `test_colon_does_not_bypass_period_validation`. Real fixture SHA-256: `d6e1bcc6be3bc0cf7cdcebaad165ca97f4681107e9c83d570f18333cf8230188`.
- Treat a new filename as discovery only. A consumed monitor hash or failed model call does not establish publication; only the independently parsed current public ICS does. State-machine regression: `tests/test_recovery_guard.py::RecoveryTests.test_stable_identity_model_failure_does_not_consume_publication`.
