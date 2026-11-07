import unittest

from solution import (MESSAGES_PER_WORKER_PER_MINUTE, QueueMetrics, ScalerLimits,
                      backlog_scaler, campaign_traffic, naive_cpu_scaler, simulate)

LIMITS = ScalerLimits(min_workers=4, max_workers=40)
START_WORKERS = 4


def run(scaler, traffic=None):
    """Simulate the campaign day with the given scaler."""
    if traffic is None:
        traffic = campaign_traffic()
    return simulate(traffic, scaler, LIMITS, START_WORKERS)


def fully_busy_metrics(workers: int, backlog: int) -> QueueMetrics:
    """Metrics for workers that are 100% busy but I/O bound (15% CPU)."""
    return QueueMetrics(workers, backlog, workers * MESSAGES_PER_WORKER_PER_MINUTE, 0.15)


class ReproduceProblem(unittest.TestCase):
    def test_cpu_scaler_never_adds_a_worker_during_the_campaign(self):
        result = run(naive_cpu_scaler)
        self.assertEqual(max(result.workers), START_WORKERS)

    def test_cpu_scaler_lets_the_backlog_reach_216000_messages(self):
        self.assertEqual(run(naive_cpu_scaler).max_backlog(), 216_000)

    def test_cpu_scaler_delays_emails_by_three_hours(self):
        self.assertEqual(run(naive_cpu_scaler).max_wait_minutes(), 180.0)

    def test_fully_busy_io_bound_workers_look_idle_to_the_cpu_formula(self):
        metrics = fully_busy_metrics(workers=10, backlog=50_000)
        self.assertEqual(naive_cpu_scaler(metrics, ScalerLimits(min_workers=1)), 3)


class FixBacklogScaling(unittest.TestCase):
    def test_backlog_scaler_scales_up_in_the_first_minute_of_the_peak(self):
        result = run(backlog_scaler)
        self.assertEqual(result.workers[60], 4)
        self.assertEqual(result.workers[61], 11)

    def test_backlog_never_exceeds_one_minute_of_peak_overflow(self):
        self.assertEqual(run(backlog_scaler).max_backlog(), 1_800)

    def test_worst_email_delay_is_under_two_minutes(self):
        self.assertLess(run(backlog_scaler).max_wait_minutes(), 2.0)

    def test_fleet_shrinks_back_after_the_campaign(self):
        result = run(backlog_scaler)
        self.assertEqual(result.workers[-1], 4)
        self.assertEqual(result.backlog[-1], 0)

    def test_large_backlog_adds_drain_workers_on_top_of_keep_up_workers(self):
        # 3000/min needs 10 workers; 30000 waiting needs 10 more to drain in 10 min
        metrics = QueueMetrics(4, 30_000, 3_000, 0.15)
        self.assertEqual(backlog_scaler(metrics, LIMITS), 20)

    def test_desired_count_is_capped_at_max_workers(self):
        metrics = QueueMetrics(4, 10_000_000, 3_000, 0.15)
        self.assertEqual(backlog_scaler(metrics, LIMITS), 40)

    def test_empty_queue_falls_back_to_min_workers(self):
        metrics = QueueMetrics(12, 0, 0, 0.0)
        self.assertEqual(backlog_scaler(metrics, LIMITS), 4)

    def test_negative_backlog_is_rejected(self):
        with self.assertRaises(ValueError):
            backlog_scaler(QueueMetrics(4, -1, 0, 0.0), LIMITS)


if __name__ == "__main__":
    unittest.main()
