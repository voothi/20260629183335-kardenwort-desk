"""Strict JSONL record boundary for the configured word extractor."""
import json


def _valid_scalar(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return float('-inf') < value < float('inf')
    return isinstance(value, (str, int))


def extraction_rows(stdout, headers):
    records = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    return [extraction_row(record, headers) for record in records]


def extraction_row(record, headers):
    if not isinstance(record, dict) or set(record) != set(headers):
        raise ValueError('Extractor changed the requested column manifest')
    if any(not _valid_scalar(value) for value in record.values()):
        raise ValueError('Extractor returned a non-scalar field')
    return [str(record[name]) for name in headers]
