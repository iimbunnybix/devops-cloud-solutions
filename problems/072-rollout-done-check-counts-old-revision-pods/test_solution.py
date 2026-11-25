import unittest

from solution import (COMPLETE, CRASHING, FAILED, HEALTHY, IN_PROGRESS, NEVER_READY,
                      PROGRESS_DEADLINE_SECONDS, TIMED_OUT, Pod, naive_rollout_status,
                      rollout_status, start_rollout, wait_for_rollout)

REPLICAS = 4
OLD, NEW = "v41", "v42"


def rollout(behaviour: str):
    return start_rollout(REPLICAS, OLD, NEW, behaviour)


def naive_check(pods: list[Pod]) -> str:
    return naive_rollout_status(pods, REPLICAS)


def fixed_check(pods: list[Pod]) -> str:
    return rollout_status(pods, REPLICAS, NEW)


class ReproduceProblem(unittest.TestCase):
    def test_crash_looping_release_is_declared_complete_at_once(self):
        self.assertEqual(wait_for_rollout(rollout(CRASHING), naive_check), (COMPLETE, 0))

    def test_complete_rollout_still_serves_only_the_old_version(self):
        deployment = rollout(CRASHING)
        wait_for_rollout(deployment, naive_check)
        ready_revisions = {pod.revision for pod in deployment.pods if pod.ready}
        self.assertEqual(ready_revisions, {OLD})

    def test_healthy_release_is_declared_complete_before_any_new_pod_is_ready(self):
        deployment = rollout(HEALTHY)
        self.assertEqual(wait_for_rollout(deployment, naive_check), (COMPLETE, 0))
        self.assertEqual([pod for pod in deployment.new_pods() if pod.ready], [])


class FixRevisionAwareCheck(unittest.TestCase):
    def test_healthy_release_completes_when_all_pods_are_new(self):
        deployment = rollout(HEALTHY)
        self.assertEqual(wait_for_rollout(deployment, fixed_check), (COMPLETE, 120))
        self.assertEqual({pod.revision for pod in deployment.pods}, {NEW})
        self.assertEqual(len(deployment.pods), REPLICAS)

    def test_crash_looping_release_fails_fast(self):
        self.assertEqual(wait_for_rollout(rollout(CRASHING), fixed_check), (FAILED, 80))

    def test_release_that_never_becomes_ready_times_out(self):
        status, decided_at = wait_for_rollout(rollout(NEVER_READY), fixed_check)
        self.assertEqual(status, TIMED_OUT)
        self.assertGreater(decided_at, PROGRESS_DEADLINE_SECONDS)

    def test_leftover_old_pod_keeps_rollout_in_progress(self):
        pods = [Pod(NEW, 0, ready=True) for _ in range(REPLICAS)] + [Pod(OLD, 0, ready=True)]
        self.assertEqual(fixed_check(pods), IN_PROGRESS)

    def test_unready_new_pod_keeps_rollout_in_progress(self):
        pods = [Pod(NEW, 0, ready=True) for _ in range(REPLICAS - 1)] + [Pod(NEW, 0)]
        self.assertEqual(fixed_check(pods), IN_PROGRESS)

    def test_a_few_restarts_during_startup_are_tolerated(self):
        pods = [Pod(NEW, 0, ready=True) for _ in range(REPLICAS - 1)]
        pods.append(Pod(NEW, 0, ready=True, restarts=2))
        self.assertEqual(fixed_check(pods), COMPLETE)

    def test_no_pods_at_all_is_not_complete(self):
        self.assertEqual(fixed_check([]), IN_PROGRESS)


if __name__ == "__main__":
    unittest.main()
