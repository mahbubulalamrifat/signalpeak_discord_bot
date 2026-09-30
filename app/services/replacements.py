"""Preg-style search and replace applied before a message is forwarded.

A rule is a pair: search_key is what to find, replace_value is what to put in its place.
An empty replace_value removes the match.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ReplacementRule:
    search_key: str
    replace_value: str
    rule_id: int | None = None


@dataclass(frozen=True)
class ReplacementChange:
    rule_id: int | None
    search_key: str
    replace_value: str
    matches: int


@dataclass(frozen=True)
class ReplacementResult:
    text: str
    changes: list[ReplacementChange]
    errors: list[str]


def apply_replacements(content: str, rules: list[ReplacementRule]) -> ReplacementResult:
    text = content or ""
    changes: list[ReplacementChange] = []

    for rule in rules:
        key = rule.search_key or ""
        if key == "":
            continue
        replacement = "" if rule.replace_value is None else rule.replace_value
        count = text.count(key)
        if count:
            text = text.replace(key, replacement)
            changes.append(
                ReplacementChange(
                    rule_id=rule.rule_id,
                    search_key=key,
                    replace_value=replacement,
                    matches=count,
                )
            )

    return ReplacementResult(text=text, changes=changes, errors=[])


_CHANNEL_MENTION = re.compile(r"<#(\d+)>")


def rewrite_channel_mentions(content: str, source_to_destination: dict[int, int]) -> str:
    """Swap a source channel mention for the paired channel on the destination server."""

    def replace(match: re.Match) -> str:
        destination_id = source_to_destination.get(int(match.group(1)))
        if destination_id is None:
            return match.group(0)
        return f"<#{destination_id}>"

    return _CHANNEL_MENTION.sub(replace, content or "")
