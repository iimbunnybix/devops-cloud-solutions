import random
import unittest

from solution import (MAX_DATA_DELAY_S, Batch, RequestCountStore, Verdict,
                      last_complete_minute_end, naive_traffic_check,
                      watermark_traffic_check)

HOSTS = [f"web-{index}" for index in range(10)]
REQUESTS_PER_SECOND_PER_HOST = 10
EXPECTED_PER_MINUTE = len(HOSTS) * REQUESTS_PER_SECOND_PER_HOST * 60   # 6,000
FLUSH_INTERVAL_S = 15
MIN_TRANSIT_S, MAX_TRANSIT_S = 1.0, 20.0
ONE_HOUR_S = 3600
FIRST_CHECK_S = 300          # give the fleet a few minutes of history first


def build_store(duration_s: int = ONE_HOUR_S, seed: int = 11,
                traffic_factor_after: tuple[int, float] | None = None,
                host_stops_shipping_at: tuple[str, int] | None = None) -> RequestCountStore:
    """Simulate every host flushing per-second request counts every 15 s.

    Each host starts its flush cycle at a different phase, and each batch
    spends 1-20 s in transit (seeded), so batches may arrive out of order.
    ``traffic_factor_after`` = (time, factor) scales real traffic from then on.
    ``host_stops_shipping_at`` = (host, time) makes one shipper hang.
    """
    rng = random.Random(seed)
    store = RequestCountStore(HOSTS)
    for host_index, host in enumerate(HOSTS):
        phase = host_index * FLUSH_INTERVAL_S / len(HOSTS)
        covered_from = 0
        flush_at = phase + FLUSH_INTERVAL_S
        while flush_at <= duration_s + MAX_TRANSIT_S:
            if host_stops_shipping_at and host_stops_shipping_at[0] == host \
                    and flush_at >= host_stops_shipping_at[1]:
                break
            covers_until = int(flush_at)
            counts = {}
            for second in range(covered_from, covers_until):
                counts[second] = requests_in_second(second, traffic_factor_after)
            transit = rng.uniform(MIN_TRANSIT_S, MAX_TRANSIT_S)
            store.add(Batch(host, covers_until, flush_at + transit, counts))
            covered_from = covers_until
            flush_at += FLUSH_INTERVAL_S
    return store


def requests_in_second(second: int, traffic_factor_after: tuple[int, float] | None) -> int:
    """Steady traffic, optionally scaled down after a given time (a real drop)."""
    if traffic_factor_after and second >= traffic_factor_after[0]:
        return int(REQUESTS_PER_SECOND_PER_HOST * traffic_factor_after[1])
    return REQUESTS_PER_SECOND_PER_HOST


def run_checks(check, store: RequestCountStore, start_s: int = FIRST_CHECK_S,
               end_s: int = ONE_HOUR_S) -> dict[int, Verdict]:
    """Evaluate the alert once a minute, on the minute, like the rule engine."""
    verdicts = {}
    for now in range(start_s, end_s + 1, 60):
        verdicts[now] = check(store, float(now), EXPECTED_PER_MINUTE)
    return verdicts


def count_verdict(verdicts: dict[int, Verdict], wanted: Verdict) -> int:
    return sum(1 for verdict in verdicts.values() if verdict is wanted)


class ReproduceProblem(unittest.TestCase):
    def test_naive_check_reports_drops_on_steady_traffic(self):
        verdicts = run_checks(naive_traffic_check, build_store())
        self.assertEqual(len(verdicts), 56)
        self.assertEqual(count_verdict(verdicts, Verdict.TRAFFIC_DROP), 24)

    def test_minute_that_just_ended_is_missing_data(self):
        store = build_store()
        now = 1200.0
        partial = store.count_between(now - 60, now, as_of=now)
        complete = store.count_between(now - 60, now, as_of=now + 60)
        self.assertEqual(complete, EXPECTED_PER_MINUTE)
        self.assertLess(partial, complete)


class FixWatermark(unittest.TestCase):
    def test_watermark_is_the_slowest_host(self):
        store = RequestCountStore(["a", "b"])
        store.add(Batch("a", 100, 101, {}))
        store.add(Batch("b", 90, 95, {}))
        self.assertEqual(store.watermark(as_of=200), 90)

    def test_batch_that_overtook_an_earlier_one_does_not_move_watermark(self):
        store = RequestCountStore(["a"])
        store.add(Batch("a", 15, 30, {}))     # slow in transit
        store.add(Batch("a", 30, 20, {}))     # overtook it, arrived first
        self.assertEqual(store.watermark(as_of=25), float("-inf"))
        self.assertEqual(store.watermark(as_of=30), 30)

    def test_host_with_no_data_holds_watermark_back(self):
        store = RequestCountStore(["a", "b"])
        store.add(Batch("a", 100, 101, {}))
        self.assertEqual(store.watermark(as_of=200), float("-inf"))

    def test_complete_minute_end_rounds_down(self):
        self.assertEqual(last_complete_minute_end(1199.0), 1140)
        self.assertEqual(last_complete_minute_end(1200.0), 1200)


class FixAlert(unittest.TestCase):
    def test_steady_traffic_never_alerts(self):
        verdicts = run_checks(watermark_traffic_check, build_store())
        self.assertEqual(count_verdict(verdicts, Verdict.OK), len(verdicts))

    def test_real_drop_is_detected_within_three_minutes(self):
        drop_at = 1800
        store = build_store(traffic_factor_after=(drop_at, 0.5))
        verdicts = run_checks(watermark_traffic_check, store)
        first_alert = min(now for now, verdict in verdicts.items()
                          if verdict is Verdict.TRAFFIC_DROP)
        self.assertGreater(first_alert, drop_at)
        self.assertLessEqual(first_alert - drop_at, 180)

    def test_stuck_shipper_reports_data_delayed_not_traffic_drop(self):
        store = build_store(host_stops_shipping_at=("web-3", 1200))
        verdicts = run_checks(watermark_traffic_check, store)
        self.assertEqual(count_verdict(verdicts, Verdict.TRAFFIC_DROP), 0)
        self.assertEqual(verdicts[1200 + 60 * 5], Verdict.DATA_DELAYED)

    def test_normal_ingestion_lag_stays_under_the_delay_limit(self):
        store = build_store()
        verdict = watermark_traffic_check(store, 1200.0, EXPECTED_PER_MINUTE)
        self.assertIs(verdict, Verdict.OK)
        self.assertLess(1200.0 - last_complete_minute_end(store.watermark(1200.0)),
                        MAX_DATA_DELAY_S)


if __name__ == "__main__":
    unittest.main()
