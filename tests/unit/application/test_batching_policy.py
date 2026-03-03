"""Tests for BatchingPolicy."""

from application.value_objects.batching_policy import BatchingPolicy


def test_split_exact_multiple():
    policy = BatchingPolicy(max_batch_size=1000)
    assert policy.split(3000) == [1000, 1000, 1000]


def test_split_with_remainder():
    policy = BatchingPolicy(max_batch_size=1000)
    assert policy.split(2500) == [1000, 1000, 500]


def test_split_smaller_than_batch():
    policy = BatchingPolicy(max_batch_size=1000)
    assert policy.split(4) == [4]


def test_split_zero():
    policy = BatchingPolicy(max_batch_size=1000)
    assert policy.split(0) == []


def test_split_negative():
    policy = BatchingPolicy(max_batch_size=1000)
    assert policy.split(-1) == []


def test_default_batch_size():
    policy = BatchingPolicy()
    assert policy.max_batch_size == 1000
