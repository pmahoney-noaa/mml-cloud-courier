import random

from mml_cloud_transfer.core.retry import QUARANTINE_ATTEMPTS, RetrySchedule


def test_defaults_match_the_spec():
    schedule = RetrySchedule()
    assert schedule.max_attempts == 5
    assert QUARANTINE_ATTEMPTS == 15


def test_yields_one_fewer_delays_than_attempts():
    delays = list(RetrySchedule().delays(random.Random(0)))
    assert len(delays) == 4


def test_delays_grow_exponentially_and_are_capped():
    schedule = RetrySchedule(max_attempts=6, base_delay=1.0, factor=2.0, max_delay=5.0)
    # With full jitter each delay is uniform in [0, min(cap, base * factor**n)].
    for seed in range(20):
        delays = list(schedule.delays(random.Random(seed)))
        assert len(delays) == 5
        for n, delay in enumerate(delays):
            assert 0.0 <= delay <= min(5.0, 1.0 * 2.0**n)


def test_jitter_actually_varies():
    a = list(RetrySchedule().delays(random.Random(1)))
    b = list(RetrySchedule().delays(random.Random(2)))
    assert a != b
