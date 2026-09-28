#!/usr/bin/env python3
"""Verify a downloaded report file against its downloaded manifest, offline.

    python tools/verify/verify_artifact.py FILE MANIFEST.json [--manifest-sha256 HEX]

Standard library only, and deliberately independent of the application: a
recipient can check a file without SYLTHARAE. Checks:

1. SHA-256 and size of FILE equal the manifest's ``artifact.sha256`` and
   ``artifact.bytes``;
2. MANIFEST.json is in canonical form (sorted keys, no insignificant
   whitespace, UTF-8) - so its SHA-256 is well defined;
3. with ``--manifest-sha256`` (the value shown by the application, returned in
   the ``X-Manifest-SHA256`` header and stored as ``manifest_sha256``): the
   manifest file's SHA-256 equals it. This ties the manifest to the record in
   the application; checks 1-2 alone only show the pair is self-consistent.

Prints one line per check and ``VERIFIED`` or ``MISMATCH``; exit 0 / 1
(2 for unreadable input).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file")
    parser.add_argument("manifest")
    parser.add_argument("--manifest-sha256", dest="manifest_sha256")
    args = parser.parse_args(argv)
    try:
        with open(args.file, "rb") as handle:
            content = handle.read()
        with open(args.manifest, "rb") as handle:
            manifest_bytes = handle.read()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        declared = manifest["artifact"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"UNREADABLE: {exc}")
        return 2

    checks = []
    digest = hashlib.sha256(content).hexdigest()
    checks.append(("file sha256", digest, declared.get("sha256")))
    checks.append(("file bytes", len(content), declared.get("bytes")))
    checks.append(("manifest canonical", True, canonical(manifest) == manifest_bytes))
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if args.manifest_sha256:
        checks.append(("manifest sha256", manifest_digest, args.manifest_sha256.lower()))
    ok = True
    for name, measured, expected in checks:
        good = measured == expected
        ok &= good
        print(f"{'ok      ' if good else 'MISMATCH'} {name}: measured {measured}, "
              f"expected {expected}")
    report = manifest.get("report", {})
    run = manifest.get("run", {})
    print(f"report {report.get('key')} run {run.get('id')} snapshot "
          f"{manifest.get('snapshot', {}).get('id')} manifest sha256 {manifest_digest}")
    if not args.manifest_sha256:
        print("note: pass --manifest-sha256 to tie the manifest to the application's record")
    print("VERIFIED" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
