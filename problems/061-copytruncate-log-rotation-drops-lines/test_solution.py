import unittest

from solution import (
    AppLogger,
    FakeFileSystem,
    copytruncate_copy_step,
    copytruncate_truncate_step,
    lines_across_generations,
    rename_rotate,
)

LOG_PATH = "/var/log/payments/app.log"


def logger_with_lines(count, prefix="before"):
    """A running service that has already written ``count`` lines."""
    file_system = FakeFileSystem()
    logger = AppLogger(file_system, LOG_PATH)
    for index in range(count):
        logger.write(f"{prefix}-{index}")
    return file_system, logger


class ReproduceProblem(unittest.TestCase):
    def test_lines_written_between_copy_and_truncate_are_lost(self):
        file_system, logger = logger_with_lines(3)
        copytruncate_copy_step(file_system, LOG_PATH)
        logger.write("payment-captured-order-981")
        copytruncate_truncate_step(file_system, LOG_PATH)
        logger.write("after-0")
        stored = lines_across_generations(file_system, LOG_PATH, keep=1)
        self.assertNotIn("payment-captured-order-981", stored)
        self.assertEqual(stored, ["before-0", "before-1", "before-2", "after-0"])


class FixRenameRotation(unittest.TestCase):
    def test_lines_written_during_rotation_land_in_the_rotated_file(self):
        file_system, logger = logger_with_lines(3)
        rename_rotate(file_system, LOG_PATH)
        logger.write("payment-captured-order-981")
        logger.reopen()
        logger.write("after-0")
        self.assertEqual(file_system.read(f"{LOG_PATH}.1")[-1], "payment-captured-order-981")
        self.assertEqual(file_system.read(LOG_PATH), ["after-0"])

    def test_every_line_is_stored_exactly_once(self):
        file_system, logger = logger_with_lines(3)
        rename_rotate(file_system, LOG_PATH)
        logger.write("during-0")
        logger.reopen()
        logger.write("after-0")
        self.assertEqual(
            lines_across_generations(file_system, LOG_PATH, keep=3),
            ["before-0", "before-1", "before-2", "during-0", "after-0"],
        )

    def test_without_reopen_service_keeps_writing_to_rotated_file(self):
        file_system, logger = logger_with_lines(1)
        rename_rotate(file_system, LOG_PATH)
        logger.write("still-old-handle")
        self.assertFalse(file_system.exists(LOG_PATH))
        self.assertEqual(file_system.read(f"{LOG_PATH}.1"), ["before-0", "still-old-handle"])


class FixGenerations(unittest.TestCase):
    def rotate_nightly(self, file_system, logger, nights, keep):
        for night in range(nights):
            logger.write(f"night-{night}")
            rename_rotate(file_system, LOG_PATH, keep=keep)
            logger.reopen()

    def test_older_files_shift_down_one_generation(self):
        file_system, logger = logger_with_lines(0)
        self.rotate_nightly(file_system, logger, nights=2, keep=3)
        self.assertEqual(file_system.read(f"{LOG_PATH}.1"), ["night-1"])
        self.assertEqual(file_system.read(f"{LOG_PATH}.2"), ["night-0"])

    def test_oldest_generation_beyond_keep_is_deleted(self):
        file_system, logger = logger_with_lines(0)
        self.rotate_nightly(file_system, logger, nights=4, keep=2)
        self.assertEqual(file_system.read(f"{LOG_PATH}.1"), ["night-3"])
        self.assertEqual(file_system.read(f"{LOG_PATH}.2"), ["night-2"])
        self.assertFalse(file_system.exists(f"{LOG_PATH}.3"))

    def test_keep_below_one_is_rejected(self):
        file_system, _ = logger_with_lines(1)
        with self.assertRaises(ValueError):
            rename_rotate(file_system, LOG_PATH, keep=0)


if __name__ == "__main__":
    unittest.main()
