"""Shared helpers for Discord snowflake columns."""


def parse_snowflake(value: str | int) -> int:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"Expected a numeric Discord id, got {value!r}")
    return int(text)
