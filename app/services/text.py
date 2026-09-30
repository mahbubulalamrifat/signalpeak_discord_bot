"""Split text so each Discord message stays within the 2000 character limit."""

DISCORD_MESSAGE_LIMIT = 2000


def split_discord_content(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    remaining = (text or "").strip()
    if not remaining:
        return []
    if len(remaining) <= limit:
        return [remaining]

    parts: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut <= 0:
            cut = limit
        piece = remaining[:cut].rstrip()
        if piece:
            parts.append(piece)
        remaining = remaining[cut:].lstrip("\n")
    return parts
