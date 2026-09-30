"""Discord bot stub so services and the API can share the same client without import cycles."""

from __future__ import annotations

import discord


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.moderation = True
    return intents


bot = discord.Client(intents=build_intents())
