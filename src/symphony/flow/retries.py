"""Error classification and backoff policy for node attempts.

The executor never decides on its own whether to retry. It hands whatever
went wrong to `classify_failure`, gets back one of the five classes in
`statuses.ERROR_*`, and then asks the node's `RetryPolicy` whether that
class is retryable. Keeping the judgement in one pure function is what
makes retry behavior testable without running a backend.

Two boundaries worth stating, because getting them wrong is expensive:

- **Adapter retries are not node retries.** Backends already reconnect and
  re-read internally. A new node attempt begins only after the adapter
  returns a terminal failure, otherwise one rate-limit blip would burn the
  whole retry budget (PRD §9.5).
- **External side effects are not retried by default.** A node that may
  have opened a PR before dying gets an operator decision, not an automatic
  second attempt (PRD §9.6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import (
    PortExit,
    ResponseError,
    ResponseTimeout,
    SymphonyError,
    TurnCancelled,
    TurnInputRequired,
    TurnTimeout,
)
from . import statuses as st
from .model import RetryPolicy


# Exponential backoff is capped so a `max_attempts: 10` node cannot wait
# hours between tries while holding its workspace lock.
MAX_BACKOFF_SECONDS = 300.0


@dataclass(frozen=True)
class FailureClassification:
    """What went wrong, in the vocabulary the retry policy speaks."""

    error_class: str
    error_code: str
    message: str

    @property
    def is_retryable_in_principle(self) -> bool:
        """True for classes a policy is *allowed* to opt into retrying."""
        return self.error_class in {st.ERROR_TRANSIENT, st.ERROR_UNKNOWN, st.ERROR_VALIDATION}


# Substrings that mark a provider-side condition that clears on its own.
# Matched case-insensitively against the exception message.
TRANSIENT_MESSAGE_PATTERNS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "429",
    "too many requests",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "service unavailable",
    "503",
    "502",
    "504",
    "overloaded",
    "econnreset",
    "broken pipe",
)

# Substrings that mark a condition no number of retries will fix.
FATAL_MESSAGE_PATTERNS: tuple[str, ...] = (
    "authentication",
    "unauthorized",
    "401",
    "403",
    "forbidden",
    "permission denied",
    "invalid api key",
    "api key",
    "not logged in",
    "no such file or directory",
    "command not found",
    "quota exceeded",
    "insufficient",
    "budget",
)

_WHITESPACE_RE = re.compile(r"\s+")


def classify_failure(
    error: BaseException | None,
    *,
    node_type: str,
    exit_code: int | None = None,
    external_side_effects: bool = False,
) -> FailureClassification:
    """Decide which error class a failed node attempt belongs to.

    This is the single policy point for "should Symphony try again?" — the
    executor consults it and then defers entirely to the node's declared
    `RetryPolicy`.

    Args:
        error: the exception that ended the attempt, or `None` when a shell
            node simply exited nonzero without raising.
        node_type: one of `statuses.NODE_TYPE_*`. Shell failures are
            deterministic far more often than agent failures.
        exit_code: process exit status for shell nodes; `None` for agents.
        external_side_effects: whether this node may have already mutated
            something outside the workspace before it failed.

    Returns:
        A `FailureClassification` whose `error_class` is one of
        `statuses.ERROR_FATAL`, `ERROR_TRANSIENT`, `ERROR_VALIDATION`,
        `ERROR_CANCELLED`, or `ERROR_UNKNOWN`.

    Ordering matters more than the individual rules:

    1. Cancellation wins over everything. An operator who pressed cancel
       gets no argument from the retry policy.
    2. Fatal patterns are checked *before* transient ones. A message can
       plausibly contain both ("rate limit exceeded, quota exhausted"), and
       PRD §9.5 requires budget exhaustion to surface as fatal rather than
       hide as a provider hiccup. Money-shaped failures fail closed.
    3. Shell and agent failures are read differently, because a shell node
       that exits nonzero has *reported* a result — the tests failed — while
       an agent that raises has usually been cut off mid-thought.
    """
    message = normalized_message(error)
    code = error_code_for(error)
    text = str(error) if error is not None else f"exited {exit_code}"

    def classified(error_class: str) -> FailureClassification:
        # A node that may already have mutated something outside the
        # workspace cannot honestly be called transient: we do not know
        # whether the PR, comment, or deploy landed before the failure.
        # That uncertainty is a fact about the failure, not a policy, so it
        # belongs in the classification the operator reads.
        if external_side_effects and error_class == st.ERROR_TRANSIENT:
            return FailureClassification(st.ERROR_UNKNOWN, code, text)
        return FailureClassification(error_class, code, text)

    if isinstance(error, (TurnCancelled, KeyboardInterrupt)) or (
        error is not None and type(error).__name__ == "CancelledError"
    ):
        return FailureClassification(st.ERROR_CANCELLED, code, text or "cancelled")

    if matches_any(message, FATAL_MESSAGE_PATTERNS):
        return classified(st.ERROR_FATAL)

    if node_type == st.NODE_TYPE_SHELL:
        if isinstance(error, (TurnTimeout, ResponseTimeout)):
            # A command that hung is not the same as one that ran and
            # disagreed — a network-dependent test can clear on a retry.
            return classified(st.ERROR_TRANSIENT)
        if exit_code in {126, 127}:
            # Not executable / not found: the workflow names a command this
            # host does not have. No number of retries installs it.
            return classified(st.ERROR_FATAL)
        if exit_code is not None and exit_code < 0:
            # Killed by a signal — usually the OOM killer. We cannot tell
            # whether the command would have passed.
            return classified(st.ERROR_UNKNOWN)
        if exit_code is not None and exit_code != 0:
            # The command ran to completion and reported failure. Retrying
            # a failing test suite just spends time to learn the same thing.
            return classified(st.ERROR_VALIDATION)

    if isinstance(error, TurnInputRequired):
        # The backend is waiting for interactive input the engine cannot
        # supply; a second attempt asks the same unanswerable question.
        return classified(st.ERROR_FATAL)
    if isinstance(error, (TurnTimeout, ResponseTimeout, ResponseError, PortExit)):
        return classified(st.ERROR_TRANSIENT)

    if matches_any(message, TRANSIENT_MESSAGE_PATTERNS):
        return classified(st.ERROR_TRANSIENT)

    # `TurnFailed` with an unrecognized message lands here, as does any
    # unexpected exception. `unknown` is not retried unless a node opts in,
    # so the default is to stop and let a human look.
    return classified(st.ERROR_UNKNOWN)


def normalized_message(error: BaseException | None) -> str:
    """Lowercased, whitespace-collapsed message for substring matching."""
    if error is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(error)).strip().lower()


def error_code_for(error: BaseException | None) -> str:
    """Stable machine-readable code for an exception."""
    if error is None:
        return "nonzero_exit"
    if isinstance(error, SymphonyError):
        return error.code
    return type(error).__name__


def matches_any(message: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in message for pattern in patterns)


def should_retry(
    classification: FailureClassification,
    *,
    policy: RetryPolicy,
    attempt: int,
    external_side_effects: bool,
) -> bool:
    """Whether another attempt is permitted for this failure.

    `attempt` is 1-based and counts the attempt that just failed, so the
    check is against `max_attempts` directly rather than off by one.
    """
    if external_side_effects:
        # No idempotency strategy exists in v1, so a node that may have
        # already mutated an external service always stops for a human.
        return False
    if classification.error_class in {st.ERROR_FATAL, st.ERROR_CANCELLED}:
        return False
    if attempt >= policy.max_attempts:
        return False
    return policy.allows(classification.error_class)


def backoff_seconds(policy: RetryPolicy, attempt: int) -> float:
    """Exponential backoff from the policy's base, capped.

    `attempt` is the number of the attempt that just failed, so the first
    retry waits exactly `backoff_seconds`.
    """
    exponent = max(0, attempt - 1)
    delay = policy.backoff_seconds * (2**exponent)
    return min(delay, MAX_BACKOFF_SECONDS)
