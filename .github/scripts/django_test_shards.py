"""Discover, balance, and write Django tests as complete module-level shards."""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def iter_test_ids(suite):
    """Yield IDs from a Django/unittest suite, including nested suites."""
    for item in suite:
        if callable(getattr(item, "id", None)):
            yield item.id()
        else:
            yield from iter_test_ids(item)


def module_for_test_id(test_id):
    """Return the importable module label from a unittest test ID."""
    parts = test_id.rsplit(".", 2)
    if len(parts) != 3:
        raise ValueError(f"Test ID is not a module/class/method path: {test_id}")
    return parts[0]


def allocate_modules(module_counts, shard_count):
    """Greedily assign the largest test modules to the lightest shard."""
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    if len(module_counts) < shard_count:
        raise ValueError("shard_count cannot exceed the number of test modules")
    if any(count < 1 for count in module_counts.values()):
        raise ValueError("each test module must contain at least one test")

    shards = [[] for _ in range(shard_count)]
    shard_sizes = [0] * shard_count
    ordered_modules = sorted(
        module_counts.items(), key=lambda item: (-item[1], item[0])
    )
    for module, count in ordered_modules:
        shard_index = min(
            range(shard_count), key=lambda index: (shard_sizes[index], index)
        )
        shards[shard_index].append(module)
        shard_sizes[shard_index] += count
    return [sorted(modules) for modules in shards]


def verify_test_coverage(expected_ids, actual_ids):
    """Raise when shard label discovery omits or duplicates any discovered test."""
    expected = Counter(expected_ids)
    actual = Counter(actual_ids)
    if expected == actual:
        return

    missing = list((expected - actual).elements())
    unexpected = list((actual - expected).elements())
    details = []
    if missing:
        details.append(f"missing {len(missing)} tests, including {missing[:5]}")
    if unexpected:
        details.append(
            f"unexpected/duplicate {len(unexpected)} tests, including {unexpected[:5]}"
        )
    raise RuntimeError(
        "Shard selections do not match full discovery: " + "; ".join(details)
    )


def write_labels_file(labels, labels_file):
    """Write a non-empty set of verified test labels, one per line."""
    if not labels:
        raise ValueError("Cannot write an empty test-label selection")
    Path(labels_file).write_text(
        "".join(f"{label}\n" for label in labels), encoding="utf-8"
    )


def discover_suite_ids(runner, labels=None):
    return list(iter_test_ids(runner.build_suite(labels or [])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate discovery and print this shard without running its tests",
    )
    parser.add_argument(
        "--labels-file",
        type=Path,
        help="write this shard's verified module labels to a file",
    )
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard_index < args.shards:
        parser.error("require --shards >= 1 and 0 <= --shard-index < --shards")
    if not args.dry_run and args.labels_file is None:
        parser.error("--labels-file is required unless --dry-run is used")

    sys.path.insert(0, str(BASE_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "GoSAdminPortal.settings")
    sys.argv.append("test")
    import django

    django.setup()

    from django.conf import settings
    from django.test.utils import get_runner

    runner_class = get_runner(settings)
    runner = runner_class(verbosity=0, interactive=False, parallel=0)
    all_test_ids = discover_suite_ids(runner)
    if not all_test_ids:
        raise RuntimeError("Django test discovery returned no tests")

    module_counts = Counter(module_for_test_id(test_id) for test_id in all_test_ids)
    module_shards = allocate_modules(module_counts, args.shards)

    selected_test_ids = []
    for modules in module_shards:
        selected_test_ids.extend(discover_suite_ids(runner, modules))
    verify_test_coverage(all_test_ids, selected_test_ids)

    labels = module_shards[args.shard_index]
    shard_test_count = sum(module_counts[module] for module in labels)
    print(
        f"Shard {args.shard_index + 1}/{args.shards}: {shard_test_count} tests "
        f"across {len(labels)} modules (all {len(all_test_ids)} discovered tests "
        "verified exactly once).",
        flush=True,
    )
    if args.dry_run:
        print("Test labels:")
        print("\n".join(labels))
        return

    write_labels_file(labels, args.labels_file)
    print(f"Wrote {len(labels)} verified test labels to {args.labels_file}.")


if __name__ == "__main__":
    main()
