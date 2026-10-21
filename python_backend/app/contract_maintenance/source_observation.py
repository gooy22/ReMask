"""Local observation clocks: monotonic proof deadlines, epoch metadata only."""
import math
import time


def proof_until(row, now, ttl):
    observed = row.get('_source_observed_at', now)
    if type(observed) not in (int, float) or not math.isfinite(observed) or observed < 0 or observed > now:
        return now  # Invalid/future observation cannot authorize a fresh proof.
    return observed + ttl


def epoch_observation(observed_at=None):
    now = time.time()
    if observed_at is None:
        return int(now)
    if (type(observed_at) not in (int, float) or not math.isfinite(observed_at)
            or observed_at < 0 or observed_at > now):
        raise ValueError('Invalid local source observation epoch')
    return int(observed_at)


def oldest_epoch(rows):
    values = [row['_source_observed_wall_at'] for row in rows if '_source_observed_wall_at' in row]
    return min((epoch_observation(value) for value in values), default=epoch_observation())
