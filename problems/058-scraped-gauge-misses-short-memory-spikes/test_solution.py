import unittest

from solution import (InstantGauge, PeakSinceLastScrapeGauge, Spike, alert_fires,
                      run_and_scrape)

BASELINE_PERCENT = 45.0
TEN_MINUTES = 600


def batch_job_spikes() -> list[Spike]:
    """Four-second spikes to 98% at the start of every other minute (not at scrape time)."""
    return [Spike(start_second=minute * 60 + 20, duration_seconds=4, percent=98.0)
            for minute in range(0, 10, 2)]


class ReproduceProblem(unittest.TestCase):
    def test_every_scrape_reads_the_baseline(self):
        scraped = run_and_scrape(InstantGauge(), TEN_MINUTES, BASELINE_PERCENT, batch_job_spikes())
        self.assertEqual(scraped, [BASELINE_PERCENT] * 10)

    def test_alert_never_fires_although_memory_hit_98_percent(self):
        scraped = run_and_scrape(InstantGauge(), TEN_MINUTES, BASELINE_PERCENT, batch_job_spikes())
        self.assertFalse(alert_fires(scraped))

    def test_scraping_every_15_seconds_still_misses_them(self):
        scraped = run_and_scrape(InstantGauge(), TEN_MINUTES, BASELINE_PERCENT,
                                 batch_job_spikes(), scrape_interval=15)
        self.assertFalse(alert_fires(scraped))


class FixReportsPeakPerInterval(unittest.TestCase):
    def test_each_interval_with_a_spike_reports_98(self):
        scraped = run_and_scrape(PeakSinceLastScrapeGauge(), TEN_MINUTES,
                                 BASELINE_PERCENT, batch_job_spikes())
        self.assertEqual(scraped, [98.0, 45.0] * 5)

    def test_alert_fires(self):
        scraped = run_and_scrape(PeakSinceLastScrapeGauge(), TEN_MINUTES,
                                 BASELINE_PERCENT, batch_job_spikes())
        self.assertTrue(alert_fires(scraped))

    def test_one_second_spike_is_still_caught(self):
        spikes = [Spike(start_second=137, duration_seconds=1, percent=99.0)]
        scraped = run_and_scrape(PeakSinceLastScrapeGauge(), TEN_MINUTES, BASELINE_PERCENT, spikes)
        self.assertEqual(scraped[2], 99.0)
        self.assertEqual(scraped.count(99.0), 1)

    def test_spike_across_a_scrape_boundary_is_reported_in_both_intervals(self):
        spikes = [Spike(start_second=58, duration_seconds=4, percent=95.0)]
        scraped = run_and_scrape(PeakSinceLastScrapeGauge(), 180, BASELINE_PERCENT, spikes)
        self.assertEqual(scraped, [95.0, 95.0, 45.0])

    def test_value_that_stays_high_is_reported_on_every_scrape(self):
        gauge = PeakSinceLastScrapeGauge()
        gauge.set(92.0)
        self.assertEqual(gauge.scrape(), 92.0)
        self.assertEqual(gauge.scrape(), 92.0)

    def test_quiet_worker_does_not_alert(self):
        scraped = run_and_scrape(PeakSinceLastScrapeGauge(), TEN_MINUTES, BASELINE_PERCENT, [])
        self.assertFalse(alert_fires(scraped))

    def test_zero_scrape_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            run_and_scrape(PeakSinceLastScrapeGauge(), 10, BASELINE_PERCENT, [], scrape_interval=0)


if __name__ == "__main__":
    unittest.main()
