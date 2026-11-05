import threading
import unittest

from solution import (DrainController, ShutdownPolicy, check_timings,
                      simulate_rolling_restart)

GOOD = ShutdownPolicy(pre_stop_delay=5, drain_timeout=20, termination_grace=30)


class FakeTime:
    def __init__(self):
        self.t = 0.0
        self.on_sleep = []          # callbacks run as time passes

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s
        for cb in list(self.on_sleep):
            cb(self.t)


class ReproduceDroppedRequests(unittest.TestCase):
    def test_exiting_on_sigterm_fails_requests(self):
        r = simulate_rolling_restart(ShutdownPolicy.exit_immediately())
        self.assertEqual(r.refused, 200)     # 3 s of propagation, 1/3 of 200 rps
        self.assertEqual(r.reset, 38)        # in-flight requests cut off
        self.assertEqual(r.exit_after, 0)

    def test_draining_alone_still_refuses_during_propagation(self):
        r = simulate_rolling_restart(ShutdownPolicy(pre_stop_delay=0, drain_timeout=20))
        self.assertEqual((r.refused, r.reset), (200, 0))

    def test_pre_stop_alone_cuts_off_slow_requests(self):
        r = simulate_rolling_restart(ShutdownPolicy(pre_stop_delay=5, drain_timeout=0))
        self.assertEqual((r.refused, r.reset), (0, 8))

    def test_pre_stop_shorter_than_propagation_still_refuses(self):
        r = simulate_rolling_restart(ShutdownPolicy(pre_stop_delay=2, drain_timeout=20))
        self.assertEqual((r.refused, r.reset), (66, 0))


class GracefulShutdownFix(unittest.TestCase):
    def test_pre_stop_plus_drain_drops_nothing(self):
        r = simulate_rolling_restart(GOOD)
        self.assertEqual(r.failed, 0)
        self.assertLess(r.exit_after, GOOD.termination_grace)

    def test_holds_across_seeds(self):
        for seed in range(10):
            self.assertEqual(simulate_rolling_restart(GOOD, seed=seed).failed, 0)

    def test_grace_period_caps_shutdown(self):
        r = simulate_rolling_restart(ShutdownPolicy(5, 30, 30), latency=lambda rng: 40)
        self.assertEqual(r.exit_after, 30)    # SIGKILL wins
        self.assertGreater(r.reset, 0)

    def test_check_timings(self):
        self.assertEqual(check_timings(GOOD, propagation_delay=3, max_request_seconds=8), [])
        problems = check_timings(ShutdownPolicy(2, 5, 6), propagation_delay=3,
                                 max_request_seconds=8)
        self.assertEqual(len(problems), 3)


class DrainControllerTest(unittest.TestCase):
    def test_sequence_waits_for_in_flight_then_exits(self):
        ft = FakeTime()
        dc = DrainController(GOOD, ft.sleep, ft.now)
        self.assertTrue(dc.begin_request())
        self.assertTrue(dc.begin_request())
        finish = {2.0: 1, 7.5: 1}            # requests finish at these times

        def tick(t):
            for at in list(finish):
                if t >= at:
                    for _ in range(finish.pop(at)):
                        dc.end_request()
        ft.on_sleep.append(tick)

        left = dc.shutdown()
        self.assertEqual(left, 0)
        self.assertFalse(dc.ready)
        self.assertFalse(dc.begin_request())       # refused after pre-stop
        self.assertEqual(dc.log[0], "not-ready")
        self.assertTrue(7.5 <= ft.t <= 7.6 + 1e-9)  # exits within one poll of draining

    def test_gives_up_at_drain_timeout(self):
        ft = FakeTime()
        dc = DrainController(ShutdownPolicy(1, 3, 30), ft.sleep, ft.now)
        dc.begin_request()                         # never finishes
        self.assertEqual(dc.shutdown(), 1)
        self.assertAlmostEqual(ft.t, 4.0, places=6)

    def test_ready_stays_true_until_shutdown_and_accepts_during_pre_stop(self):
        ft = FakeTime()
        dc = DrainController(GOOD, ft.sleep, ft.now)
        accepted = []
        ft.on_sleep.append(lambda t: accepted.append(dc.accepting))
        self.assertTrue(dc.ready)
        dc.shutdown()
        self.assertTrue(accepted[0])               # still accepting at end of pre-stop sleep

    def test_refused_request_in_track_does_not_change_the_count(self):
        ft = FakeTime()
        dc = DrainController(GOOD, ft.sleep, ft.now)
        self.assertTrue(dc.begin_request())        # one real request still running
        dc.accepting = False
        with dc.track() as accepted:
            self.assertFalse(accepted)
        self.assertEqual(dc.in_flight, 1)

    def test_unmatched_end_request_raises(self):
        ft = FakeTime()
        dc = DrainController(GOOD, ft.sleep, ft.now)
        dc.begin_request()
        dc.end_request()
        with self.assertRaises(RuntimeError):
            dc.end_request()
        self.assertEqual(dc.in_flight, 0)

    def test_shutdown_waits_for_real_request_after_a_refused_one(self):
        ft = FakeTime()
        dc = DrainController(ShutdownPolicy(0, 20, 30), ft.sleep, ft.now)
        dc.begin_request()                          # real 8 s request

        def finish_real_request(t):
            if t >= 8.0 and dc.in_flight == 1:
                dc.end_request()
        ft.on_sleep.append(finish_real_request)
        dc.accepting = False
        with dc.track():
            pass                                    # refused request
        self.assertEqual(dc.shutdown(), 0)
        self.assertGreaterEqual(ft.t, 8.0)

    def test_track_counts_accepted_request_until_block_ends(self):
        ft = FakeTime()
        dc = DrainController(GOOD, ft.sleep, ft.now)
        with dc.track() as accepted:
            self.assertTrue(accepted)
            self.assertEqual(dc.in_flight, 1)
        self.assertEqual(dc.in_flight, 0)

    def test_thread_safe_counting(self):
        ft = FakeTime()
        dc = DrainController(GOOD, ft.sleep, ft.now)

        def work():
            for _ in range(1000):
                dc.begin_request()
                dc.end_request()
        ts = [threading.Thread(target=work) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(dc.in_flight, 0)


if __name__ == "__main__":
    unittest.main()
