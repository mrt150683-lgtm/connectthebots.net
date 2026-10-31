"""Development-only validation of real public board JSON against its schema."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs/BOARD_SCHEMA.json"


def board_validator():
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError:
        raise RuntimeError("Board contract checks require the development tool jsonschema==4.26.0") from None

    checker = FormatChecker()

    @checker.checks("date-time", raises=(ValueError, TypeError))
    def utc_timestamp(value):
        # The public contract is stricter than arbitrary RFC 3339. Register the
        # checker explicitly; jsonschema otherwise treats format as annotation.
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value) is None:
            return False
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
        return True

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=checker)


def validate_samples(samples):
    if not isinstance(samples, list) or not samples:
        raise ValueError("Supply at least one captured public JSON response")
    validator = board_validator()
    for sample in samples:
        validator.validate(sample)
    return len(samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    count = validate_samples(json.loads(args.samples.read_text(encoding="utf-8")))
    evidence = {"passed": True, "schema": "docs/BOARD_SCHEMA.json", "draft": "2020-12",
                "date_time_format_checked": True, "samples_validated": count}
    args.out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Public board schema checks passed: {count} actual responses, including timestamp format")


if __name__ == "__main__":
    main()
