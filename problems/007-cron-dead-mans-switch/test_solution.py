import unittest

from solution import (FAILED, MISSED, NEVER_RAN, OK, STUCK, FailureOnlyAlerter, FakeClock,
                      HeartbeatMonitor, JobSpec)

DAY, HOUR, MIN = 86400, 3600, 60
BACKUP = JobSpec("db-backup", period=DAY, grace=HOUR, max_runtime=2 * HOUR)


def run_ok(mon, clock, run_id, duration=20 * MIN):
    mon.start("db-backup", run_id)
    clock.advance(duration)
    mon.success("db-backup", run_id)


def tick_until(mon, clock, until, step=MIN):
    alerts = []
    while clock.now() < until:
        clock.advance(step)
        alerts += mon.evaluate()
    return alerts


class ReproduceSilentCron(unittest.TestCase):
    def test_failure_only_alerting_is_silent_when_job_stops(self):
        alerter = FailureOnlyAlerter()
        for _ in range(3):
            alerter.on_exit("db-backup", 0)
        # cron daemon disappears: the script is never invoked again for 21 days
        self.assertEqual(alerter.alerts, [])


class DeadMansSwitch(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(0)
        self.mon = HeartbeatMonitor(self.clock)
        self.mon.register(BACKUP)

    def test_missed_run_detected_within_period_plus_grace(self):
        for i in range(3):                       # three healthy nights at 02:00
            self.clock.t = i * DAY + 2 * HOUR
            run_ok(self.mon, self.clock, f"r{i}")
            self.assertEqual(self.mon.evaluate(), [])
        last_success = self.clock.now()
        alerts = tick_until(self.mon, self.clock, last_success + 21 * DAY)
        self.assertEqual([(a.kind, a.status) for a in alerts], [("FIRING", MISSED)])  # one page, not 30k
        detected = alerts[0].at - last_success
        self.assertGreater(detected, DAY + HOUR)
        self.assertLessEqual(detected, DAY + HOUR + MIN)

    def test_no_alert_for_a_late_but_within_grace_run(self):
        run_ok(self.mon, self.clock, "r0")
        self.clock.advance(DAY + 50 * MIN)       # 50 min late, grace is 60
        self.assertEqual(self.mon.evaluate(), [])
        run_ok(self.mon, self.clock, "r1")
        self.assertEqual(self.mon.status_of("db-backup"), OK)

    def test_recovery_resolves(self):
        run_ok(self.mon, self.clock, "r0")
        self.clock.advance(2 * DAY)
        self.assertEqual(self.mon.evaluate()[0].status, MISSED)
        run_ok(self.mon, self.clock, "r1")
        alerts = self.mon.evaluate()
        self.assertEqual([(a.kind, a.status) for a in alerts], [("RESOLVED", OK)])

    def test_never_ran_after_registration(self):
        self.clock.advance(DAY + HOUR - 1)
        self.assertEqual(self.mon.evaluate(), [])
        self.clock.advance(2)
        a = self.mon.evaluate()
        self.assertEqual(a[0].status, NEVER_RAN)
        self.assertIn("since it was registered", a[0].message)

    def test_hung_run_is_stuck(self):
        run_ok(self.mon, self.clock, "r0")
        self.mon.start("db-backup", "r1")        # starts, then hangs on a lock
        self.clock.advance(2 * HOUR + 1)
        self.assertEqual(self.mon.evaluate()[0].status, STUCK)

    def test_explicit_failure_then_success(self):
        self.mon.start("db-backup", "r0")
        self.mon.fail("db-backup", "r0", "pg_dump: connection refused")
        a = self.mon.evaluate()
        self.assertEqual(a[0].status, FAILED)
        self.assertIn("connection refused", a[0].message)
        run_ok(self.mon, self.clock, "r1")
        self.assertEqual(self.mon.evaluate()[0].kind, "RESOLVED")

    def test_status_change_between_bad_states_pages_again(self):
        self.mon.start("db-backup", "r0")
        self.mon.fail("db-backup", "r0", "disk full")
        self.assertEqual(self.mon.evaluate()[0].status, FAILED)
        self.mon.start("db-backup", "r1")
        self.clock.advance(3 * HOUR)
        self.assertEqual(self.mon.evaluate()[0].status, STUCK)

    def test_overlapping_runs_tracked_by_id(self):
        self.mon.start("db-backup", "a")
        self.mon.start("db-backup", "b")
        self.clock.advance(MIN)
        self.mon.success("db-backup", "b")
        self.clock.advance(2 * HOUR)
        self.assertEqual(self.mon.status_of("db-backup"), STUCK)   # "a" never finished

    def test_crashed_run_without_ping_is_cleared_by_a_later_success(self):
        self.mon.start("db-backup", "r0")        # OOM-killed: never sends success or fail
        self.clock.advance(3 * HOUR)
        self.assertEqual(self.mon.evaluate()[0].status, STUCK)
        self.clock.t = DAY
        run_ok(self.mon, self.clock, "r1")
        self.assertEqual(self.mon.status_of("db-backup"), OK)
        self.assertEqual([(a.kind, a.status) for a in self.mon.evaluate()], [("RESOLVED", OK)])

    def test_job_that_stops_after_a_crashed_run_still_pages_missed(self):
        self.mon.start("db-backup", "r0")        # crashed run, no ping
        self.clock.t = DAY
        run_ok(self.mon, self.clock, "r1")
        self.mon.evaluate()
        self.clock.advance(2 * DAY)              # then the job stops running entirely
        self.assertEqual([(a.kind, a.status) for a in self.mon.evaluate()], [("FIRING", MISSED)])

    def test_unknown_job_and_bad_spec(self):
        with self.assertRaises(KeyError):
            self.mon.start("typo-backup", "x")
        with self.assertRaises(ValueError):
            self.mon.register(JobSpec("bad", period=0, grace=0, max_runtime=1))

    def test_independent_jobs(self):
        self.mon.register(JobSpec("hourly-report", period=HOUR, grace=5 * MIN, max_runtime=10 * MIN))
        run_ok(self.mon, self.clock, "r0")
        self.clock.advance(2 * HOUR)
        alerts = {a.job: a.status for a in self.mon.evaluate()}
        self.assertEqual(alerts, {"hourly-report": NEVER_RAN})


if __name__ == "__main__":
    unittest.main()
