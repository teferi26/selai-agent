"""Plan limit policy (pure core)."""
from app.db.models import SubscriptionStatus
from app.services.plan_limits import BLOCKING_STATUSES, check_limit


def test_under_limit_not_exceeded():
    assert check_limit("properties", 10, 200).exceeded is False


def test_at_limit_is_exceeded():
    assert check_limit("agents", 5, 5).exceeded is True


def test_over_limit_is_exceeded():
    assert check_limit("voice_minutes", 301, 300).exceeded is True


def test_negative_limit_means_unlimited():
    assert check_limit("properties", 999_999, -1).exceeded is False


def test_zero_limit_blocks_first_use():
    assert check_limit("voice_minutes", 0, 0).exceeded is True


def test_blocking_statuses_are_the_terminal_ones():
    # PAUSED joined the set with (152): a trial that expired without a card.
    assert {
        SubscriptionStatus.CANCELED,
        SubscriptionStatus.UNPAID,
        SubscriptionStatus.PAUSED,
    } == BLOCKING_STATUSES
    assert SubscriptionStatus.TRIALING not in BLOCKING_STATUSES
    assert SubscriptionStatus.PAST_DUE not in BLOCKING_STATUSES
