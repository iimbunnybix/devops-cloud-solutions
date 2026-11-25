import unittest

from solution import FakeDisk, FakeFile, log_line, naive_poll, poll, write_lines

LOG = "/var/log/app/access.log"
LINES_BEFORE_ROTATION = 500
LINES_AFTER_ROTATION = 800


def disk_with_lines(count: int) -> FakeDisk:
    disk = FakeDisk()
    disk.create(LOG)
    write_lines(disk, LOG, 0, count)
    return disk


def ship_across_rotation(poll_function, checkpoints: dict, lines_after: int) -> list[bytes]:
    """Ship the old file, rotate (the inode is reused), write new lines, ship again."""
    disk = disk_with_lines(LINES_BEFORE_ROTATION)
    poll_function(disk, LOG, checkpoints)
    disk.rotate_delete_and_recreate(LOG)
    write_lines(disk, LOG, LINES_BEFORE_ROTATION, lines_after)
    return poll_function(disk, LOG, checkpoints)


def expected_lines(first: int, count: int) -> list[bytes]:
    return [log_line(sequence).rstrip(b"\n") for sequence in range(first, first + count)]


class ReproduceProblem(unittest.TestCase):
    def test_rotation_reuses_the_inode_number(self):
        disk = disk_with_lines(1)
        old_inode = disk.files[LOG].inode
        disk.rotate_delete_and_recreate(LOG)
        self.assertEqual(disk.files[LOG].inode, old_inode)

    def test_naive_shipper_loses_most_lines_of_the_new_file(self):
        shipped = ship_across_rotation(naive_poll, {}, LINES_AFTER_ROTATION)
        self.assertEqual(len(shipped), 303)

    def test_naive_shipper_starts_with_half_a_line(self):
        shipped = ship_across_rotation(naive_poll, {}, LINES_AFTER_ROTATION)
        self.assertEqual(shipped[0], b"/api/orders/997 200")

    def test_naive_shipper_ships_nothing_while_new_file_is_below_old_offset(self):
        self.assertEqual(ship_across_rotation(naive_poll, {}, 300), [])


class FixFingerprintedCheckpoint(unittest.TestCase):
    def test_every_line_of_the_new_file_is_shipped(self):
        shipped = ship_across_rotation(poll, {}, LINES_AFTER_ROTATION)
        self.assertEqual(shipped, expected_lines(LINES_BEFORE_ROTATION, LINES_AFTER_ROTATION))

    def test_small_new_file_is_shipped_immediately(self):
        shipped = ship_across_rotation(poll, {}, 300)
        self.assertEqual(shipped, expected_lines(LINES_BEFORE_ROTATION, 300))

    def test_appends_to_the_same_file_are_not_shipped_twice(self):
        disk = disk_with_lines(10)
        checkpoints: dict = {}
        poll(disk, LOG, checkpoints)
        write_lines(disk, LOG, 10, 5)
        self.assertEqual(poll(disk, LOG, checkpoints), expected_lines(10, 5))

    def test_file_shorter_than_fingerprint_keeps_resuming_as_it_grows(self):
        disk = disk_with_lines(1)
        checkpoints: dict = {}
        poll(disk, LOG, checkpoints)
        for sequence in range(1, 4):
            write_lines(disk, LOG, sequence, 1)
            self.assertEqual(poll(disk, LOG, checkpoints), expected_lines(sequence, 1))

    def test_truncated_file_is_read_from_the_start(self):
        disk = disk_with_lines(20)
        checkpoints: dict = {}
        poll(disk, LOG, checkpoints)
        disk.files[LOG].content = b""
        write_lines(disk, LOG, 20, 3)
        self.assertEqual(poll(disk, LOG, checkpoints), expected_lines(20, 3))

    def test_rotation_to_a_new_inode_is_read_from_the_start(self):
        disk = disk_with_lines(20)
        checkpoints: dict = {}
        poll(disk, LOG, checkpoints)
        disk.files[LOG] = FakeFile(inode=disk.files[LOG].inode + 1)   # rename-style rotation
        write_lines(disk, LOG, 20, 2)
        self.assertEqual(poll(disk, LOG, checkpoints), expected_lines(20, 2))

    def test_partial_line_waits_for_its_newline(self):
        disk = disk_with_lines(2)
        checkpoints: dict = {}
        poll(disk, LOG, checkpoints)
        disk.append(LOG, b"2026-11-25T03:01:00Z GET /api/ord")
        self.assertEqual(poll(disk, LOG, checkpoints), [])
        disk.append(LOG, b"ers/2 200\n")
        self.assertEqual(poll(disk, LOG, checkpoints),
                         [b"2026-11-25T03:01:00Z GET /api/orders/2 200"])

    def test_poll_right_after_rotation_then_more_lines_ships_them_all(self):
        disk = disk_with_lines(LINES_BEFORE_ROTATION)
        checkpoints: dict = {}
        poll(disk, LOG, checkpoints)
        disk.rotate_delete_and_recreate(LOG)
        self.assertEqual(poll(disk, LOG, checkpoints), [])
        write_lines(disk, LOG, 0, 4)
        self.assertEqual(poll(disk, LOG, checkpoints), expected_lines(0, 4))


if __name__ == "__main__":
    unittest.main()
