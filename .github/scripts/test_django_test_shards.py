import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from django_test_shards import (
    allocate_modules,
    module_for_test_id,
    verify_test_coverage,
    write_labels_file,
)


class TestModuleForTestID(unittest.TestCase):
    def test_extracts_module_label(self):
        self.assertEqual(
            module_for_test_id("programs.tests.test_students.StudentTests.test_list"),
            "programs.tests.test_students",
        )

    def test_rejects_malformed_test_id(self):
        with self.assertRaises(ValueError):
            module_for_test_id("not-a-test-id")


class AllocateModulesTests(unittest.TestCase):
    def test_allocation_is_complete_balanced_and_deterministic(self):
        module_counts = {
            "a.tests": 8,
            "b.tests": 7,
            "c.tests": 6,
            "d.tests": 5,
            "e.tests": 4,
            "f.tests": 3,
        }

        shards = allocate_modules(module_counts, 3)

        self.assertEqual(shards, allocate_modules(module_counts, 3))
        self.assertEqual(
            sorted(module for shard in shards for module in shard),
            sorted(module_counts),
        )
        self.assertLessEqual(max(map(len, shards)), 2)
        shard_test_counts = [
            sum(module_counts[module] for module in shard) for shard in shards
        ]
        self.assertLessEqual(max(shard_test_counts) - min(shard_test_counts), 5)

    def test_rejects_invalid_shard_counts(self):
        with self.assertRaises(ValueError):
            allocate_modules({"a.tests": 1}, 0)
        with self.assertRaises(ValueError):
            allocate_modules({"a.tests": 1}, 2)


class VerifyTestCoverageTests(unittest.TestCase):
    def test_accepts_exact_coverage(self):
        verify_test_coverage(
            ["a.Test.test_one", "b.Test.test_two"],
            ["b.Test.test_two", "a.Test.test_one"],
        )

    def test_rejects_missing_tests(self):
        with self.assertRaisesRegex(RuntimeError, "missing 1 tests"):
            verify_test_coverage(
                ["a.Test.test_one", "b.Test.test_two"], ["a.Test.test_one"]
            )

    def test_rejects_duplicate_tests(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected/duplicate 1 tests"):
            verify_test_coverage(
                ["a.Test.test_one"], ["a.Test.test_one", "a.Test.test_one"]
            )


class WriteLabelsFileTests(unittest.TestCase):
    def test_writes_one_label_per_line(self):
        with TemporaryDirectory() as directory:
            labels_file = Path(directory) / "labels.txt"

            write_labels_file(["a.tests", "b.tests"], labels_file)

            self.assertEqual(
                labels_file.read_text(encoding="utf-8"), "a.tests\nb.tests\n"
            )

    def test_rejects_empty_labels(self):
        with TemporaryDirectory() as directory:
            labels_file = Path(directory) / "labels.txt"

            with self.assertRaisesRegex(ValueError, "empty test-label selection"):
                write_labels_file([], labels_file)

            self.assertFalse(labels_file.exists())


if __name__ == "__main__":
    unittest.main()
