import unittest

from solution import (
    Finding,
    lint_script,
    lint_steps,
    naive_pipeline_status,
    pipeline_status,
    split_pipelines,
    step_passes,
)

MIGRATION_FAILED = 1
TEE_OK = 0

ORIGINAL_DEPLOY_STEP = """\
#!/usr/bin/env bash
set -e
./run_migrations.sh | tee migrate.log
./deploy.sh --release "$RELEASE"
"""

FIXED_DEPLOY_STEP = """\
#!/usr/bin/env bash
set -euo pipefail
./run_migrations.sh | tee migrate.log
./deploy.sh --release "$RELEASE"
"""


class ReproduceProblem(unittest.TestCase):
    def test_failed_migration_piped_to_tee_reports_success(self):
        self.assertEqual(naive_pipeline_status([MIGRATION_FAILED, TEE_OK]), 0)

    def test_deploy_step_goes_green_without_pipefail(self):
        self.assertTrue(step_passes([MIGRATION_FAILED, TEE_OK], pipefail=False))

    def test_failing_last_command_is_still_reported(self):
        self.assertEqual(naive_pipeline_status([0, 1]), 1)


class FixPipelineStatus(unittest.TestCase):
    def test_pipefail_reports_the_migration_failure(self):
        self.assertEqual(pipeline_status([MIGRATION_FAILED, TEE_OK], pipefail=True), MIGRATION_FAILED)

    def test_pipefail_fails_the_deploy_step(self):
        self.assertFalse(step_passes([MIGRATION_FAILED, TEE_OK], pipefail=True))

    def test_pipefail_reports_the_rightmost_non_zero_status(self):
        self.assertEqual(pipeline_status([3, 5, 0], pipefail=True), 5)

    def test_pipefail_reports_zero_when_every_command_succeeds(self):
        self.assertEqual(pipeline_status([0, 0, 0], pipefail=True), 0)

    def test_single_command_status_is_the_same_either_way(self):
        self.assertEqual(pipeline_status([2], pipefail=True), pipeline_status([2], pipefail=False))

    def test_empty_pipeline_is_rejected(self):
        with self.assertRaises(ValueError):
            pipeline_status([], pipefail=True)


class FixSplitsShellLines(unittest.TestCase):
    def test_pipe_separates_stages(self):
        self.assertEqual(split_pipelines("make build | gzip > app.gz"), [["make build", "gzip > app.gz"]])

    def test_or_operator_is_not_a_pipe(self):
        self.assertEqual(split_pipelines("./check.sh || exit 1"), [["./check.sh"], ["exit 1"]])

    def test_pipe_inside_quotes_is_not_a_pipe(self):
        self.assertEqual(split_pipelines("""echo 'a|b' "c|d" """), [["""echo 'a|b' "c|d\""""]])

    def test_escaped_pipe_is_not_a_pipe(self):
        self.assertEqual(split_pipelines(r"grep a\|b log"), [[r"grep a\|b log"]])

    def test_pipe_in_comment_is_ignored(self):
        self.assertEqual(split_pipelines("make  # then | gzip"), [["make"]])

    def test_pipe_with_stderr_is_a_pipe(self):
        self.assertEqual(split_pipelines("make |& tee build.log"), [["make", "tee build.log"]])


class FixLintsCiScripts(unittest.TestCase):
    def test_original_deploy_step_is_flagged(self):
        self.assertEqual(lint_script(ORIGINAL_DEPLOY_STEP), [Finding(3, "./run_migrations.sh | tee migrate.log")])

    def test_set_e_alone_does_not_satisfy_the_linter(self):
        self.assertEqual(len(lint_script("set -e\nbuild | gzip > artifact.gz\n")), 1)

    def test_fixed_deploy_step_is_clean(self):
        self.assertEqual(lint_script(FIXED_DEPLOY_STEP), [])

    def test_long_option_form_enables_pipefail(self):
        self.assertEqual(lint_script("set -o pipefail\nbuild | gzip\n"), [])

    def test_pipefail_set_earlier_on_the_same_line_counts(self):
        self.assertEqual(lint_script("set -o pipefail; build | gzip\n"), [])

    def test_pipe_before_pipefail_is_enabled_is_flagged(self):
        script = "build | gzip\nset -o pipefail\ntest | tee log\n"
        self.assertEqual([finding.line_number for finding in lint_script(script)], [1])

    def test_turning_pipefail_off_is_flagged_again(self):
        script = "set -euo pipefail\nset +o pipefail\nbuild | gzip\n"
        self.assertEqual([finding.line_number for finding in lint_script(script)], [3])

    def test_scripts_with_only_or_operators_are_clean(self):
        self.assertEqual(lint_script("./migrate.sh || exit 1\necho 'a|b'\n"), [])

    def test_each_step_is_linted_on_its_own(self):
        steps = {"setup": "set -o pipefail\n", "deploy": "./run_migrations.sh | tee migrate.log\n"}
        self.assertEqual(list(lint_steps(steps)), ["deploy"])


if __name__ == "__main__":
    unittest.main()
