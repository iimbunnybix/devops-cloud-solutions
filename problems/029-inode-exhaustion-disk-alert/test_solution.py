import unittest

from solution import (CRITICAL, GIB, WARNING, FilesystemUsage, directories_by_file_count,
                      disk_alert, ext4_volume, first_alert_hour, first_failed_write_hour,
                      hourly_samples, naive_disk_alert)

SESSION_FILE_BYTES = 1200          # one serialized PHP-style session
SESSIONS_PER_HOUR = 60_000         # new visitors per hour, never cleaned up
WEEK_HOURS = 168


def session_host_week() -> list:
    """A 100 GiB ext4 /var with 15 GiB of app data, filling with session files for a week."""
    start = ext4_volume("/var", 100 * GIB, 15 * GIB, base_inodes_used=200_000)
    return hourly_samples(start, WEEK_HOURS, SESSIONS_PER_HOUR, SESSION_FILE_BYTES)


def usage(bytes_percent: float, inodes_percent: float, inodes_total: int = 1000) -> FilesystemUsage:
    return FilesystemUsage("/data", 1000, int(bytes_percent * 10), inodes_total,
                           int(inodes_percent * inodes_total / 100))


def critical_only(fs: FilesystemUsage):
    """The disk alert, but only when it is CRITICAL."""
    alert = disk_alert(fs)
    if alert is not None and alert.severity == CRITICAL:
        return alert
    return None


class ReproduceProblem(unittest.TestCase):
    def test_writes_start_failing_while_disk_is_under_forty_percent(self):
        samples = session_host_week()
        failed_hour = first_failed_write_hour(samples, SESSION_FILE_BYTES)
        self.assertEqual(failed_hour, 106)
        self.assertLess(samples[failed_hour - 1].bytes_percent, 40)
        self.assertEqual(samples[failed_hour - 1].inodes_percent, 100.0)

    def test_bytes_only_alert_never_fires_during_the_whole_week(self):
        self.assertIsNone(first_alert_hour(session_host_week(), naive_disk_alert))


class FixAlertsOnInodes(unittest.TestCase):
    def test_inode_warning_fires_sixteen_hours_before_writes_fail(self):
        samples = session_host_week()
        alert_hour = first_alert_hour(samples, disk_alert)
        self.assertEqual(alert_hour, 90)
        alert = disk_alert(samples[alert_hour - 1])
        self.assertEqual((alert.resource, alert.severity), ("inodes", WARNING))

    def test_inode_critical_fires_before_writes_fail(self):
        samples = session_host_week()
        self.assertEqual(first_alert_hour(samples, critical_only), 101)

    def test_bytes_are_still_checked(self):
        alert = disk_alert(usage(bytes_percent=96, inodes_percent=10))
        self.assertEqual((alert.resource, alert.severity), ("bytes", CRITICAL))

    def test_worse_severity_wins_over_higher_percent_of_other_resource(self):
        alert = disk_alert(usage(bytes_percent=94, inodes_percent=95))
        self.assertEqual((alert.resource, alert.severity), ("inodes", CRITICAL))

    def test_same_severity_reports_the_fuller_resource(self):
        alert = disk_alert(usage(bytes_percent=86, inodes_percent=90))
        self.assertEqual(alert.resource, "inodes")

    def test_healthy_filesystem_has_no_alert(self):
        self.assertIsNone(disk_alert(usage(bytes_percent=50, inodes_percent=50)))

    def test_filesystem_without_fixed_inodes_is_checked_on_bytes_only(self):
        dynamic = FilesystemUsage("/btrfs", 1000, 500, inodes_total=0, inodes_used=0)
        self.assertIsNone(dynamic.inodes_percent)
        self.assertIsNone(disk_alert(dynamic))
        self.assertTrue(dynamic.can_create_file(100))


class FixHelpsTriage(unittest.TestCase):
    def test_file_count_ranking_points_at_the_session_directory(self):
        paths = [f"/var/lib/php/sessions/sess_{i}" for i in range(500)]
        paths += [f"/var/log/app/app.log.{i}" for i in range(5)]
        paths += ["/var/lib/mysql/ibdata1"]
        ranked = directories_by_file_count(paths, depth=3, top=2)
        self.assertEqual(ranked, [("/var/lib/php", 500), ("/var/log/app", 5)])


if __name__ == "__main__":
    unittest.main()
