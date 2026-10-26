"""Read-only, low-bandwidth release version probe for Holding.

Never downloads application binaries, installs software or certifies a release.
Status CACHE_NOT_REVALIDATED deliberately does NOT mean "up to date".
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .update_manifest_client import ManifestFetchError
from .update_probe import probe_release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check release version with persistent HTTP ETag")
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--cache-file", type=Path, required=True)
    parser.add_argument("--max-cache-age", type=int, default=30,
                        help="Seconds allowed to skip a redundant HTTP check, max 3600")
    parser.add_argument("--allow-test-loopback", action="store_true",
                        help="Local integration-test server only, never remote plain HTTP")
    args = parser.parse_args(argv)
    try:
        state = probe_release(
            args.manifest_url,
            cache_file=args.cache_file,
            max_cache_age_seconds=args.max_cache_age,
            allow_test_loopback=args.allow_test_loopback,
        )
    except (ManifestFetchError, OSError, ValueError) as exc:
        print(json.dumps({"status": "UNAVAILABLE", "reason": str(exc)},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": state.status,
        "release": state.release,
        "channel": state.channel,
        "etag": state.etag,
        "network_bytes": state.network_bytes,
        "revalidated": state.status != "CACHE_NOT_REVALIDATED",
        "installation_performed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
