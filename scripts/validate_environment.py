#!/usr/bin/env python3
"""Validate rollout environment variables without displaying their values."""

import argparse
import sys

from rollout_common import validate_environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("sandbox", "production"), required=True)
    args = parser.parse_args(argv)

    results = validate_environment(args.mode)
    for name, status in results:
        print(f"{name}: {status}")
    return 1 if any(status in {"missing", "malformed"} for _, status in results) else 0


if __name__ == "__main__":
    sys.exit(main())
