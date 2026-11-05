import unittest

from solution import (HOUR_SECONDS, Backup, BackupJobResult, alert_message,
                      naive_backup_ok, sha256_hex, verify_backup)

BACKUP_TIME = 1_700_000_000.0
CHECK_TIME = BACKUP_TIME + 3 * HOUR_SECONDS
SOURCE_ROWS = {"orders": 1_000, "customers": 200}


def dump_rows(count: int) -> bytes:
    """A table dump with one CSV line per row."""
    lines = []
    for row_id in range(count):
        lines.append(f"{row_id},value-{row_id}")
    return ("\n".join(lines) + "\n").encode() if lines else b""


def make_backup(orders: int = 1_000, customers: int = 200,
                created_at: float = BACKUP_TIME) -> Backup:
    """A backup whose manifest matches its files, like the dump step writes it."""
    files = {"orders.csv": dump_rows(orders), "customers.csv": dump_rows(customers)}
    manifest = {name: sha256_hex(data) for name, data in files.items()}
    return Backup(created_at, files, manifest)


class ReproduceProblem(unittest.TestCase):
    def test_exit_code_zero_hides_an_empty_backup(self):
        empty_backup = make_backup(orders=0, customers=0)
        self.assertTrue(naive_backup_ok(BackupJobResult(exit_code=0)))
        self.assertFalse(verify_backup(empty_backup, SOURCE_ROWS, CHECK_TIME).ok)


class FixVerifiesRestore(unittest.TestCase):
    def test_good_backup_passes_and_raises_no_alert(self):
        report = verify_backup(make_backup(), SOURCE_ROWS, CHECK_TIME)
        self.assertTrue(report.ok)
        self.assertIsNone(alert_message(report))

    def test_empty_dump_fails_even_though_checksums_match(self):
        report = verify_backup(make_backup(orders=0, customers=0), SOURCE_ROWS, CHECK_TIME)
        self.assertEqual(report.failures, [
            "customers: restored 0 rows, source has 200",
            "orders: restored 0 rows, source has 1000"])

    def test_partial_dump_beyond_tolerance_fails(self):
        report = verify_backup(make_backup(orders=900), SOURCE_ROWS, CHECK_TIME)
        self.assertIn("orders: restored 900 rows, source has 1000", report.failures)

    def test_small_drift_from_writes_after_the_dump_is_allowed(self):
        self.assertTrue(verify_backup(make_backup(orders=960), SOURCE_ROWS, CHECK_TIME).ok)

    def test_truncated_upload_fails_checksum(self):
        backup = make_backup()
        backup.files["orders.csv"] = backup.files["orders.csv"][:100]
        report = verify_backup(backup, SOURCE_ROWS, CHECK_TIME)
        self.assertIn("orders.csv: checksum mismatch", report.failures)

    def test_missing_manifest_fails(self):
        backup = make_backup()
        backup.manifest = {}
        self.assertIn("backup has no manifest",
                      verify_backup(backup, SOURCE_ROWS, CHECK_TIME).failures)

    def test_file_listed_in_manifest_but_missing_fails(self):
        backup = make_backup()
        del backup.files["customers.csv"]
        failures = verify_backup(backup, SOURCE_ROWS, CHECK_TIME).failures
        self.assertIn("customers.csv: listed in manifest but missing", failures)
        self.assertIn("customers: table missing from backup", failures)

    def test_new_source_table_not_in_backup_fails(self):
        source = dict(SOURCE_ROWS, refunds=50)
        failures = verify_backup(make_backup(), source, CHECK_TIME).failures
        self.assertEqual(failures, ["refunds: table missing from backup"])

    def test_stale_backup_fails_freshness(self):
        old_backup = make_backup(created_at=CHECK_TIME - 30 * HOUR_SECONDS)
        failures = verify_backup(old_backup, SOURCE_ROWS, CHECK_TIME).failures
        self.assertEqual(failures, ["latest backup is 30.0h old"])

    def test_no_backup_at_all_fails(self):
        report = verify_backup(None, SOURCE_ROWS, CHECK_TIME)
        self.assertIn("no backup found", alert_message(report))

    def test_empty_source_table_matches_empty_dump(self):
        source = {"orders": 1_000, "customers": 0}
        self.assertTrue(verify_backup(make_backup(customers=0), source, CHECK_TIME).ok)


if __name__ == "__main__":
    unittest.main()
