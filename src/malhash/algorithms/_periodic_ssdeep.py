"""Fast SSDEEP for inputs verified to repeat a short byte pattern."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_HASH_SEED = 39
_REDUCED_FNV_PRIME = 19
_DIGEST_LENGTH = 64
_MIN_BLOCK_SIZE = 3
_ROLLING_WINDOW = 7
_UINT32_MASK = (1 << 32) - 1


def find_period(sample: bytes, max_period: int = 256) -> bytes:
    """Return the shortest candidate period of a sampled prefix, or empty bytes."""
    for length in range(1, min(max_period, len(sample) // 2) + 1):
        if sample[length:] == sample[:-length]:
            return sample[:length]
    return b""


def _rolling_cycle(pattern: bytes) -> tuple[tuple[int, ...], tuple[int, ...]]:
    # Six leading bytes contain part of the initial zero-padded rolling window.
    # The following complete windows repeat after exactly one pattern length.
    startup = _ROLLING_WINDOW - 1
    window = [0] * _ROLLING_WINDOW
    first = weighted = shifted = 0
    values = []
    for index in range(startup + len(pattern)):
        slot = index % _ROLLING_WINDOW
        byte = pattern[index % len(pattern)]
        weighted += _ROLLING_WINDOW * byte - first
        first += byte - window[slot]
        window[slot] = byte
        shifted = ((shifted << 5) ^ byte) & _UINT32_MASK
        values.append((first + weighted + shifted) & _UINT32_MASK)
    return tuple(values[:startup]), tuple(values[startup:])


@dataclass(frozen=True, slots=True)
class _CutPositions:
    startup: tuple[int, ...]
    cycle: tuple[int, ...]
    startup_length: int
    cycle_length: int
    count: int

    @classmethod
    def for_block(
        cls, leading: tuple[int, ...], periodic: tuple[int, ...], length: int, block: int
    ) -> _CutPositions:
        startup = tuple(i for i, value in enumerate(leading, 1) if (value + 1) % block == 0)
        cycle = tuple(i for i, value in enumerate(periodic, 1) if (value + 1) % block == 0)
        rounds, tail = divmod(length - len(leading), len(periodic))
        count = len(startup) + rounds * len(cycle) + bisect_right(cycle, tail)
        return cls(startup, cycle, len(leading), len(periodic), count)

    def at(self, index: int) -> int:
        if index < len(self.startup):
            return self.startup[index]
        rounds, slot = divmod(index - len(self.startup), len(self.cycle))
        return self.startup_length + rounds * self.cycle_length + self.cycle[slot]


def _segment_hash(pattern: bytes, start: int, end: int) -> str:
    period = len(pattern)
    state = _HASH_SEED
    leading = min(end - start, (-start) % period)
    for index in range(start, start + leading):
        state = ((state * _REDUCED_FNV_PRIME) ^ pattern[index % period]) & 63
    rounds, trailing = divmod(end - start - leading, period)
    seen: dict[int, int] = {}
    while rounds:
        # The six-bit FNV state repeats quickly even for multi-gigabyte segments.
        if state in seen:
            rounds %= seen[state] - rounds
            seen.clear()
            if rounds == 0:
                break
        seen[state] = rounds
        for byte in pattern:
            state = ((state * _REDUCED_FNV_PRIME) ^ byte) & 63
        rounds -= 1
    for byte in pattern[:trailing]:
        state = ((state * _REDUCED_FNV_PRIME) ^ byte) & 63
    return _ALPHABET[state]


def _component(
    pattern: bytes, cuts: _CutPositions, length: int, last_roll: int, capacity: int
) -> str:
    digest = []
    start = 0
    kept = min(cuts.count, capacity - 1)
    for index in range(kept):
        stop = cuts.at(index)
        digest.append(_segment_hash(pattern, start, stop))
        start = stop
    if last_roll:
        digest.append(_segment_hash(pattern, start, length))
    elif cuts.count > kept:
        digest.append(_segment_hash(pattern, start, cuts.at(cuts.count - 1)))
    return "".join(digest)


def hash_periodic(pattern: bytes, length: int) -> str:
    """Compute the digest after the caller verifies every byte against the pattern."""
    if length == 0:
        return "3::"
    leading, periodic = _rolling_cycle(pattern)
    leading = leading[:length]
    last_roll = (
        leading[-1]
        if length <= len(leading)
        else periodic[(length - len(leading) - 1) % len(pattern)]
    )
    block = _MIN_BLOCK_SIZE
    while block * _DIGEST_LENGTH < length:
        block *= 2
    primary = _CutPositions.for_block(leading, periodic, length, block)
    while block > _MIN_BLOCK_SIZE and primary.count < _DIGEST_LENGTH // 2:
        block //= 2
        primary = _CutPositions.for_block(leading, periodic, length, block)
    secondary = _CutPositions.for_block(leading, periodic, length, block * 2)
    short = _component(pattern, primary, length, last_roll, _DIGEST_LENGTH)
    long = _component(pattern, secondary, length, last_roll, _DIGEST_LENGTH // 2)
    return f"{block}:{short}:{long}"
