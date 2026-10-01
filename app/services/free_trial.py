"""Destination-server join options. Only the one-day free trial is active."""

import asyncio
import logging

import discord

from app.config import get_settings
from app.services.bot_client import bot
from app.services.signalpeak_api import get_json, post_json

logger = logging.getLogger("signalpeak.free_trial")
settings = get_settings()

FREE_TRIAL = "join_plan:free_trial"
MONTHLY = "join_plan:monthly"
LIFETIME = "join_plan:lifetime"
EMAIL_MODAL = "join_plan:free_trial_email"

_expiry_task: asyncio.Task | None = None


async def enforce_join(member: discord.Member) -> None:
    """Kick a returning member whose free trial is already used. Offer plans otherwise."""

    destination_id = settings.destination_server_snowflake
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
        return

    await _offer_plans(member)


async def handle_interaction(interaction: discord.Interaction) -> None:
    custom_id = (interaction.data or {}).get("custom_id")
    if custom_id == FREE_TRIAL:
        await interaction.response.send_modal(_email_modal())
        return
    if custom_id in {MONTHLY, LIFETIME}:
        await interaction.response.send_message("This option is not open yet.", ephemeral=True)
        return
    if custom_id == EMAIL_MODAL:
        await _start_trial(interaction)


def start_expiry_loop() -> None:
    global _expiry_task
    if _expiry_task is not None and not _expiry_task.done():
        return
    _expiry_task = asyncio.create_task(_expire_loop())


async def _offer_plans(member: discord.Member) -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Free trial (1 day)", style=discord.ButtonStyle.primary, custom_id=FREE_TRIAL))
    view.add_item(discord.ui.Button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id=MONTHLY))
    view.add_item(discord.ui.Button(label="Lifetime", style=discord.ButtonStyle.secondary, custom_id=LIFETIME))
    try:
        await member.send("Choose how you want to stay in the server.", view=view)
    except discord.Forbidden:
        logger.info("Could not send join options to %s", member.id)


def _email_modal() -> discord.ui.Modal:
    modal = discord.ui.Modal(title="Free trial", custom_id=EMAIL_MODAL)
    modal.add_item(discord.ui.TextInput(label="Email", custom_id="email", required=True, max_length=255))
    return modal


async def _start_trial(interaction: discord.Interaction) -> None:
    email = _modal_value(interaction, "email")
    if not email or "@" not in email:
        await interaction.response.send_message("Enter a valid email to start the free trial.", ephemeral=True)
        return
    destination_id = settings.destination_server_snowflake
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
                "email": email,
            },
        )
    except Exception:
        await interaction.response.send_message("This Discord account cannot start another free trial.", ephemeral=True)
        return
    await interaction.response.send_message("Your free trial has started. It lasts 1 day.", ephemeral=True)


def _modal_value(interaction: discord.Interaction, custom_id: str) -> str:
    for row in (interaction.data or {}).get("components", []):
        for component in row.get("components", []):
            if component.get("custom_id") == custom_id:
                return str(component.get("value", "")).strip()
    return ""


async def _expire_loop() -> None:
    while True:
        await asyncio.sleep(60)
        await expire_due_trials()


async def expire_due_trials() -> None:
    destination_id = settings.destination_server_snowflake
    if destination_id is None or not bot.is_ready():
        return
    try:
        payload = await get_json("/discord/free-trials?due=1")
    except Exception:
        return
    guild = bot.get_guild(destination_id)
    if guild is None:
        return
    for trial in payload.get("free_trials", []):
        user_id = int(trial["discord_user_id"])
        await _remove(guild, user_id, "Free trial ended")
        try:
            await post_json(
                f"/discord/free-trials/{trial['id']}/kick",
                {"already_removed": True, "status": "expired", "reason": "Free trial ended"},
            )
        except Exception:
            logger.exception("Could not mark free trial %s expired", trial.get("id"))


async def _remove(guild: discord.Guild, user_id: int, reason: str) -> None:
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        await member.kick(reason=reason)
    except (discord.NotFound, discord.Forbidden):
        return
