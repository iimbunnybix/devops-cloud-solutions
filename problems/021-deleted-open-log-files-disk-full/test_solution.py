import unittest

from solution import (LinkedFile, OpenFd, Process, deleted_inode_holders, df_used_bytes,
                      hidden_bytes, is_on_mount, naive_du_used_bytes, recommend)

GIB = 1024 ** 3
DISK_CAPACITY = 200 * GIB


def linked_files() -> list[LinkedFile]:
    """What is still visible under /var: 37 GiB of named files."""
    return [
        LinkedFile("/var/log/nginx/access.log", 101, 1 * GIB),
        LinkedFile("/var/lib/app/data.db", 102, 36 * GIB),
    ]


def incident_processes() -> list[Process]:
    """nginx still writes to a rotated-and-deleted access log; others hold small ones."""
    nginx = Process(1201, "nginx", [
        OpenFd(3, "/var/log/nginx/access.log.1", 201, 160 * GIB, link_count=0),
        OpenFd(4, "/var/log/nginx/access.log", 101, 1 * GIB, link_count=1),
    ])
    java = Process(2300, "java", [
        OpenFd(7, "/var/log/app/gc.log.3", 202, 2 * GIB, link_count=0),
        OpenFd(8, "/var/log/app/gc.log.3", 202, 2 * GIB, link_count=0),   # dup()'d fd
    ])
    # A worker forked from its parent shares the same open file.
    parent = Process(3100, "celery", [OpenFd(5, "/var/log/worker.log.2", 203, 1 * GIB, 0)])
    child = Process(3101, "celery", [OpenFd(5, "/var/log/worker.log.2", 203, 1 * GIB, 0)])
    return [nginx, java, parent, child]


class ReproduceProblem(unittest.TestCase):
    def test_disk_is_full_but_du_sees_under_a_fifth_of_it(self):
        du_bytes = naive_du_used_bytes(linked_files())
        df_bytes = df_used_bytes(linked_files(), incident_processes(), "/var")
        self.assertEqual(df_bytes, DISK_CAPACITY)
        self.assertEqual(du_bytes, 37 * GIB)
        self.assertLess(du_bytes / df_bytes, 0.2)


class FixFindsHiddenSpace(unittest.TestCase):
    def test_hidden_bytes_explain_the_df_du_gap(self):
        gap = DISK_CAPACITY - naive_du_used_bytes(linked_files())
        self.assertEqual(hidden_bytes(incident_processes(), "/var"), gap)

    def test_duplicated_fd_is_counted_once(self):
        java = [incident_processes()[1]]
        self.assertEqual(hidden_bytes(java, "/var"), 2 * GIB)

    def test_still_linked_file_is_not_reported(self):
        holders = deleted_inode_holders(incident_processes(), "/var")
        self.assertNotIn(101, holders)

    def test_file_named_deleted_but_still_linked_is_not_reported(self):
        process = Process(1, "report", [OpenFd(3, "/var/tmp/report (deleted).txt", 9, GIB, 1)])
        self.assertEqual(hidden_bytes([process], "/var"), 0)

    def test_files_on_other_mounts_are_ignored(self):
        process = Process(1, "app", [OpenFd(3, "/var/logs-archive/old.log", 9, GIB, 0)])
        self.assertEqual(hidden_bytes([process], "/var/log"), 0)
        self.assertTrue(is_on_mount("/var/log/a.log", "/var/log"))
        self.assertTrue(is_on_mount("/tmp/x", "/"))

    def test_no_processes_means_nothing_hidden(self):
        self.assertEqual(hidden_bytes([], "/var"), 0)
        self.assertEqual(recommend([], "/var"), [])


class FixRecommendsActions(unittest.TestCase):
    def test_biggest_win_comes_first_and_uses_reopen_signal(self):
        first = recommend(incident_processes(), "/var")[0]
        self.assertEqual((first.pid, first.action), (1201, "send SIGUSR1"))
        self.assertEqual(first.reclaimable_bytes, 160 * GIB)
        self.assertEqual(first.deleted_paths, ("/var/log/nginx/access.log.1",))

    def test_process_without_reopen_signal_must_be_restarted(self):
        by_pid = {rec.pid: rec for rec in recommend(incident_processes(), "/var")}
        self.assertEqual(by_pid[2300].action, "restart")
        self.assertEqual(by_pid[2300].reclaimable_bytes, 2 * GIB)

    def test_shared_file_frees_nothing_until_every_holder_closes_it(self):
        by_pid = {rec.pid: rec for rec in recommend(incident_processes(), "/var")}
        self.assertEqual(by_pid[3100].reclaimable_bytes, 0)
        self.assertEqual(by_pid[3101].reclaimable_bytes, 0)
        self.assertEqual(by_pid[3100].deleted_paths, ("/var/log/worker.log.2",))

    def test_custom_signal_table_overrides_default(self):
        recs = recommend(incident_processes(), "/var", reopen_signals={"java": "SIGHUP"})
        by_pid = {rec.pid: rec for rec in recs}
        self.assertEqual(by_pid[2300].action, "send SIGHUP")
        self.assertEqual(by_pid[1201].action, "restart")


if __name__ == "__main__":
    unittest.main()
