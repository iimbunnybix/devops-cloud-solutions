import json
import os
import tempfile
import unittest
from pathlib import Path

from solution import (TEMP_SUFFIX, ConfigError, ConfigReloader, InterruptedWriter,
                      atomic_write_config, encode_config, naive_reload,
                      naive_write_config, parse_config, sample_config)

OLD_CONFIG = sample_config(version=1, route_count=40)
NEW_CONFIG = sample_config(version=2, route_count=50)


class ConfigDirTestCase(unittest.TestCase):
    """Gives each test a fresh directory holding version 1 of routes.json."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp_dir.name)
        self.path = self.directory / "routes.json"
        self.path.write_bytes(encode_config(OLD_CONFIG))

    def tearDown(self):
        self._temp_dir.cleanup()

    def half_of_new_config(self) -> int:
        return len(encode_config(NEW_CONFIG)) // 2


class ReproduceProblem(ConfigDirTestCase):
    def test_reader_sees_empty_file_right_after_truncate(self):
        naive_write_config(self.path, NEW_CONFIG, InterruptedWriter(stop_after_bytes=0))
        with self.assertRaises(json.JSONDecodeError):
            naive_reload(self.path)

    def test_reader_sees_truncated_json_mid_write(self):
        naive_write_config(self.path, NEW_CONFIG, InterruptedWriter(self.half_of_new_config()))
        with self.assertRaises(json.JSONDecodeError):
            naive_reload(self.path)

    def test_old_config_is_already_gone_during_the_write(self):
        naive_write_config(self.path, NEW_CONFIG, InterruptedWriter(stop_after_bytes=0))
        self.assertEqual(self.path.read_bytes(), b"")


class FixAtomicWrite(ConfigDirTestCase):
    def test_interrupted_write_leaves_old_file_intact(self):
        with self.assertRaises(OSError):
            atomic_write_config(self.path, NEW_CONFIG, InterruptedWriter(self.half_of_new_config()))
        self.assertEqual(parse_config(self.path.read_bytes()), OLD_CONFIG)

    def test_interrupted_write_leaves_no_temp_file_behind(self):
        with self.assertRaises(OSError):
            atomic_write_config(self.path, NEW_CONFIG, InterruptedWriter(stop_after_bytes=10))
        leftovers = [name for name in os.listdir(self.directory) if name.endswith(TEMP_SUFFIX)]
        self.assertEqual(leftovers, [])

    def test_complete_write_replaces_the_file(self):
        atomic_write_config(self.path, NEW_CONFIG)
        self.assertEqual(parse_config(self.path.read_bytes()), NEW_CONFIG)

    def test_write_creates_file_when_none_exists(self):
        new_path = self.directory / "fresh.json"
        atomic_write_config(new_path, NEW_CONFIG)
        self.assertEqual(parse_config(new_path.read_bytes()), NEW_CONFIG)


class FixValidatingReloader(ConfigDirTestCase):
    def test_reloader_keeps_last_good_config_on_truncated_file(self):
        reloader = ConfigReloader(self.path)
        reloader.reload()
        naive_write_config(self.path, NEW_CONFIG, InterruptedWriter(self.half_of_new_config()))
        self.assertEqual(reloader.reload(), OLD_CONFIG)
        self.assertEqual(reloader.rejected_reloads, 1)

    def test_reloader_picks_up_the_new_config_once_complete(self):
        reloader = ConfigReloader(self.path)
        reloader.reload()
        atomic_write_config(self.path, NEW_CONFIG)
        self.assertEqual(reloader.reload()["version"], 2)

    def test_valid_json_missing_routes_is_rejected(self):
        reloader = ConfigReloader(self.path)
        reloader.reload()
        self.path.write_text(json.dumps({"version": 3}))
        self.assertEqual(reloader.reload(), OLD_CONFIG)
        self.assertIn("routes", reloader.last_error)

    def test_empty_routes_table_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_config(json.dumps({"version": 3, "routes": {}}).encode())

    def test_deleted_file_keeps_last_good_config(self):
        reloader = ConfigReloader(self.path)
        reloader.reload()
        self.path.unlink()
        self.assertEqual(reloader.reload(), OLD_CONFIG)

    def test_startup_with_bad_file_fails_loudly(self):
        self.path.write_bytes(b"{")
        with self.assertRaises(ConfigError):
            ConfigReloader(self.path).reload()


if __name__ == "__main__":
    unittest.main()
