"""Destination-server join options. Only the one-day free trial is active."""

import logging

import discord

from app.config import get_settings
from app.services.server_cache import server_pair
from app.services.signalpeak_api import get_json, post_json

logger = logging.getLogger("signalpeak.free_trial")

FREE_TRIAL = "join_plan:free_trial"
MONTHLY = "join_plan:monthly"
LIFETIME = "join_plan:lifetime"


async def enforce_join(member: discord.Member) -> None:
    """Kick a returning member whose free trial is already used. Offer plans otherwise."""

    pair = server_pair()
    destination_id = pair.destination_server_id if pair else None
    if destination_id is None or member.guild.id != destination_id:
        return

    try:
        decision = await get_json(f"/discord/free-trials/eligibility?discord_user_id={member.id}")
    except Exception:
        logger.exception("Could not check free-trial eligibility for %s", member.id)
        return

    reason = decision.get("reason")
    if reason == "free_trial_expired":
        await _remove(member.guild, member.id, "Free trial already used")
        return
    if reason == "free_trial_active":
        await _grant_member_role(member)
        return

    await _offer_plans(member)


async def handle_interaction(interaction: discord.Interaction) -> None:
    custom_id = (interaction.data or {}).get("custom_id")
    if custom_id == FREE_TRIAL:
        await _start_trial(interaction)
        return
    if custom_id in {MONTHLY, LIFETIME}:
        await interaction.response.send_message("This option is not open yet.", ephemeral=True)


async def _offer_plans(member: discord.Member) -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Free trial (1 day)", style=discord.ButtonStyle.primary, custom_id=FREE_TRIAL))
    view.add_item(discord.ui.Button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id=MONTHLY))
    view.add_item(discord.ui.Button(label="Lifetime", style=discord.ButtonStyle.secondary, custom_id=LIFETIME))
    try:
        await member.send("Choose how you want to stay in the server.", view=view)
    except discord.Forbidden:
        logger.info("Could not send join options to %s", member.id)


async def _start_trial(interaction: discord.Interaction) -> None:
    pair = server_pair()
    destination_id = pair.destination_server_id if pair else None
    if destination_id is None:
        await interaction.response.send_message("The destination server is not configured.", ephemeral=True)
        return
    user = interaction.user
    try:
        await post_json(
            "/discord/free-trials",
            {
                "discord_user_id": user.id,
                "server_id": destination_id,
                "username": str(user),
            },
        )
    except Exception:
        await interaction.response.send_message("This Discord account cannot start another free trial.", ephemeral=True)
        return

    await interaction.response.send_message("Your free trial has started. It lasts 1 day.", ephemeral=True)

    if isinstance(user, discord.Member):
        await _grant_member_role(user)
        return
    guild = interaction.guild
    if guild is None and pair is not None:
        guild = interaction.client.get_guild(pair.destination_server_id)
    if guild is None:
        return
    try:
        member = guild.get_member(user.id) or await guild.fetch_member(user.id)
        await _grant_member_role(member)
    except (discord.NotFound, discord.Forbidden):
        return


async def _grant_member_role(member: discord.Member) -> None:
    role_id = get_settings().approval_role_snowflake
    if role_id is None:
        return
    role = member.guild.get_role(role_id)
    if role is None or role in member.roles:
        return
    try:
        await member.add_roles(role, reason="Free trial started")
    except discord.Forbidden:
        logger.warning("Bot cannot assign Member role %s in %s", role_id, member.guild.id)


async def _remove(guild: discord.Guild, user_id: int, reason: str) -> None:
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        await member.kick(reason=reason)
    except (discord.NotFound, discord.Forbidden):
        return
