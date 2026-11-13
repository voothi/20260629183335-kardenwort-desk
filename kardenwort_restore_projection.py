"""Pure lookup indexes for the SQLite restore read projection."""


def case_insensitive_fields(fields):
    """Keep the first matching spelling, including a first value of None."""
    indexed = {}
    for key, value in fields.items():
        indexed.setdefault(key.lower(), value)
    return indexed
