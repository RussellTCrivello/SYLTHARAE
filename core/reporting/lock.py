"""Pin report and dataset fingerprints.

    python -m core.reporting.lock --check   # exit 1 on any registry problem
    python -m core.reporting.lock --write   # pin *new* id@version entries

``--write`` only ever adds entries. It refuses to overwrite a pinned
fingerprint or to drop a pinned version: that would erase the evidence that a
released definition changed meaning. Bump the version instead.
"""

from __future__ import annotations

import argparse
import json
import sys

from .registry import LOCK_PATH, REGISTRY, read_lock


def write_new_entries(path: str = LOCK_PATH) -> int:
    lock = read_lock(path)
    current = REGISTRY.fingerprints()
    changed = [p for p in REGISTRY.validate_lock(lock)
               if "changed meaning" in p or "no longer registered" in p]
    if changed:
        for problem in changed:
            print(f"refused: {problem}", file=sys.stderr)
        return 1
    added = 0
    for section in ("datasets", "reports"):
        pinned = lock.setdefault(section, {})
        for key, fp in current[section].items():
            if key not in pinned:
                pinned[key] = fp
                added += 1
                print(f"pinned {section[:-1]} {key} {fp[:12]}")
        lock[section] = dict(sorted(pinned.items()))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(lock, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"{added} new entr{'y' if added == 1 else 'ies'} pinned")
    return 0


def check() -> int:
    problems = (REGISTRY.validate() + REGISTRY.validate_translations()
                + REGISTRY.validate_lock())
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(REGISTRY.reports)} report version(s), {len(REGISTRY.datasets)} "
          f"dataset version(s): {len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    return write_new_entries() if args.write else check()


if __name__ == "__main__":
    raise SystemExit(main())
