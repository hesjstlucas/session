import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

PING_ROLE_RE = re.compile(r"^<@&(\d+)>$")
SESSION_UPDATE_INTERVAL_SECONDS = 30


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def split_csv_ids(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            result.add(int(item))
    return result


def parse_optional_id(value: str) -> Optional[int]:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def summarize_exception(error: Exception) -> str:
    return str(error) or error.__class__.__name__


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_datetime_for_embed(value: Optional[str]) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return "Unknown"
    return f"{discord.utils.format_dt(parsed, style='F')} ({discord.utils.format_dt(parsed, style='R')})"


def resolve_ping(
    guild: discord.Guild, value: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    text = (value or "").strip()
    if not text:
        return None, None

    lowered = text.lower()
    if lowered in {"@everyone", "everyone"}:
        return "@everyone", None
    if lowered in {"@here", "here"}:
        return "@here", None

    role_id: Optional[int] = None
    if text.isdigit():
        role_id = int(text)
    else:
        match = PING_ROLE_RE.fullmatch(text)
        if match:
            role_id = int(match.group(1))

    if role_id is None:
        return None, "Ping must be `@everyone`, `@here`, a role mention, or a role ID."

    role = guild.get_role(role_id)
    if role is None:
        return None, "That ping role was not found in this server."
    return role.mention, None


def allowed_mentions_for_ping(ping_text: Optional[str]) -> discord.AllowedMentions:
    if ping_text in {"@everyone", "@here"}:
        return discord.AllowedMentions(everyone=True, roles=False, users=False)
    if ping_text and ping_text.startswith("<@&") and ping_text.endswith(">"):
        return discord.AllowedMentions(everyone=False, roles=True, users=False)
    return discord.AllowedMentions.none()


def format_member_count(guild: discord.Guild) -> str:
    member_count = guild.member_count
    if member_count is None:
        member_count = len(guild.members)
    return f"{member_count:,}"


def build_session_embed(
    guild: discord.Guild,
    session: dict,
    *,
    active: bool,
    ended_by: Optional[discord.abc.User] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="ERLC Session Started" if active else "ERLC Session Ended",
        description=(
            "A new ERLC session is now active."
            if active
            else "The ERLC session has been ended."
        ),
        color=discord.Color.green() if active else discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Started By", value=f"<@{session['started_by_id']}>", inline=True)
    embed.add_field(
        name="Vote Count",
        value=(
            str(session["vote_count"])
            if session.get("vote_count") is not None
            else "Not provided"
        ),
        inline=True,
    )
    embed.add_field(name="Member Count", value=format_member_count(guild), inline=True)
    embed.add_field(
        name="Started At",
        value=format_datetime_for_embed(session.get("started_at")),
        inline=False,
    )

    ping_text = session.get("ping_text")
    if ping_text:
        embed.add_field(name="Ping", value=ping_text, inline=True)

    if active:
        embed.add_field(name="Status", value="Active", inline=True)
        embed.set_footer(text=f"Member count refreshes every {SESSION_UPDATE_INTERVAL_SECONDS} seconds.")
    else:
        embed.add_field(name="Status", value="Ended", inline=True)
        if ended_by is not None:
            embed.add_field(name="Ended By", value=ended_by.mention, inline=True)
        embed.set_footer(text="Session closed.")

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    return embed


@dataclass(frozen=True)
class BotConfig:
    token: str
    register_guild_id: Optional[int]
    owner_user_ids: set[int]
    session_manager_role_ids: set[int]
    data_file_path: Path

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            token=require_env("DISCORD_TOKEN"),
            register_guild_id=parse_optional_id(os.getenv("REGISTER_GUILD_ID", "")),
            owner_user_ids=split_csv_ids(os.getenv("OWNER_USER_IDS", "")),
            session_manager_role_ids=split_csv_ids(os.getenv("SESSION_MANAGER_ROLE_IDS", "")),
            data_file_path=Path(
                os.getenv("DATA_FILE_PATH", "data/session-store.json").strip()
                or "data/session-store.json"
            ),
        )


class SessionStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.data = {"sessions": {}}

    def load(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.save()
            return

        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
        sessions = payload.get("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
        self.data = {"sessions": sessions}

    def save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
        temp_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temp_path.replace(self.file_path)

    def get_session(self, guild_id: int) -> Optional[dict]:
        return self.data["sessions"].get(str(guild_id))

    def list_sessions(self) -> list[dict]:
        entries = [
            {"guild_id": int(guild_id), **entry}
            for guild_id, entry in self.data["sessions"].items()
        ]
        entries.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return entries

    def set_session(self, guild_id: int, entry: dict) -> None:
        self.data["sessions"][str(guild_id)] = entry
        self.save()

    def remove_session(self, guild_id: int) -> Optional[dict]:
        removed = self.data["sessions"].pop(str(guild_id), None)
        if removed is not None:
            self.save()
        return removed


class ErlcSessionBot(commands.Bot):
    def __init__(self, config: BotConfig, store: SessionStore) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.store = store
        self._commands_registered = False
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        if not self._commands_registered:
            self.register_commands()
            self._commands_registered = True

        if self.config.register_guild_id is not None:
            guild = discord.Object(id=self.config.register_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {self.config.register_guild_id}.")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global command(s).")

        if not self.session_updater.is_running():
            self.session_updater.start()

    async def close(self) -> None:
        if self.session_updater.is_running():
            self.session_updater.cancel()
        await super().close()

    async def on_ready(self) -> None:
        if self.user is None:
            return
        print(f"Logged in as {self.user} in {len(self.guilds)} guild(s).")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        print(f"Command error: {error}")
        await self.send_ephemeral(
            interaction,
            "The command failed. Check the bot logs for details.",
        )

    @tasks.loop(seconds=SESSION_UPDATE_INTERVAL_SECONDS)
    async def session_updater(self) -> None:
        for session in self.store.list_sessions():
            try:
                await self.refresh_session_message(session["guild_id"])
            except Exception as error:
                print(
                    f"Could not refresh session message for guild {session['guild_id']}: "
                    f"{summarize_exception(error)}"
                )

    @session_updater.before_loop
    async def before_session_updater(self) -> None:
        await self.wait_until_ready()

    def register_commands(self) -> None:
        @self.tree.command(name="ssu", description="Start an ERLC session announcement.")
        @app_commands.guild_only()
        @app_commands.describe(
            vote_count="Optional vote count for the session",
            ping="Optional ping: @everyone, @here, role mention, or role ID",
        )
        async def ssu(
            interaction: discord.Interaction,
            vote_count: Optional[int] = None,
            ping: Optional[str] = None,
        ) -> None:
            if not await self.ensure_access(interaction):
                return

            assert interaction.guild is not None
            channel = interaction.channel
            if channel is None or not hasattr(channel, "send") or not hasattr(channel, "id"):
                await self.send_ephemeral(interaction, "This channel cannot be used for session announcements.")
                return

            if vote_count is not None and vote_count < 0:
                await self.send_ephemeral(interaction, "`vote_count` must be 0 or higher.")
                return

            existing_session = self.store.get_session(interaction.guild.id)
            if existing_session is not None:
                _, error, removable = await self.get_session_message(existing_session)
                if removable:
                    self.store.remove_session(interaction.guild.id)
                else:
                    active_channel_id = existing_session.get("channel_id")
                    channel_mention = f"<#{active_channel_id}>" if active_channel_id else "another channel"
                    message = f"A session is already active in {channel_mention}. Use `/ssd` first."
                    if error:
                        message += f"\nStored session lookup note: {error}"
                    await self.send_ephemeral(interaction, message)
                    return

            ping_text, ping_error = resolve_ping(interaction.guild, ping)
            if ping_error is not None:
                await self.send_ephemeral(interaction, ping_error)
                return

            session = {
                "channel_id": int(channel.id),
                "message_id": None,
                "started_by_id": str(interaction.user.id),
                "started_by_tag": str(interaction.user),
                "started_at": utc_now_iso(),
                "vote_count": vote_count,
                "ping_text": ping_text,
            }

            embed = build_session_embed(interaction.guild, session, active=True)
            try:
                message = await channel.send(
                    content=ping_text or None,
                    embed=embed,
                    allowed_mentions=allowed_mentions_for_ping(ping_text),
                )
            except Exception as error:
                await self.send_ephemeral(
                    interaction,
                    f"Could not send the session announcement: {summarize_exception(error)}",
                )
                return

            session["message_id"] = message.id
            self.store.set_session(interaction.guild.id, session)
            await self.send_ephemeral(
                interaction,
                f"Session started in {channel.mention}. The member count will refresh every 30 seconds.",
            )

        @self.tree.command(name="ssd", description="End the active ERLC session announcement.")
        @app_commands.guild_only()
        async def ssd(interaction: discord.Interaction) -> None:
            if not await self.ensure_access(interaction):
                return

            assert interaction.guild is not None
            session = self.store.get_session(interaction.guild.id)
            if session is None:
                await self.send_ephemeral(interaction, "There is no active session in this server.")
                return

            message, error, removable = await self.get_session_message(session)
            self.store.remove_session(interaction.guild.id)

            if message is None:
                if removable:
                    await self.send_ephemeral(
                        interaction,
                        "The active session was cleared, but its announcement message could not be found.",
                    )
                else:
                    await self.send_ephemeral(
                        interaction,
                        f"The active session was cleared, but the announcement could not be updated: {error}",
                    )
                return

            embed = build_session_embed(
                interaction.guild,
                session,
                active=False,
                ended_by=interaction.user,
            )
            try:
                await message.edit(embed=embed)
            except Exception as edit_error:
                await self.send_ephemeral(
                    interaction,
                    f"Session ended, but I could not update the announcement message: {summarize_exception(edit_error)}",
                )
                return

            await self.send_ephemeral(interaction, "Session ended.")

    async def ensure_access(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await self.send_ephemeral(interaction, "This command can only be used in a server.")
            return False

        member = interaction.user
        if member.id in self.config.owner_user_ids:
            return True

        if any(role.id in self.config.session_manager_role_ids for role in member.roles):
            return True

        if member.guild_permissions.manage_messages:
            return True

        await self.send_ephemeral(
            interaction,
            "You need the `Manage Messages` permission, a role in `SESSION_MANAGER_ROLE_IDS`, or a user ID in `OWNER_USER_IDS` to use this command.",
        )
        return False

    async def send_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def get_session_message(
        self, session: dict
    ) -> tuple[Optional[discord.Message], Optional[str], bool]:
        channel_id = session.get("channel_id")
        message_id = session.get("message_id")
        if channel_id is None or message_id is None:
            return None, "Stored session message data is incomplete.", True

        channel = self.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.fetch_channel(int(channel_id))
            except discord.NotFound:
                return None, "The session channel no longer exists.", True
            except Exception as error:
                return None, summarize_exception(error), False

        if not hasattr(channel, "fetch_message"):
            return None, "The session channel is not messageable.", True

        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return None, "The session announcement message no longer exists.", True
        except Exception as error:
            return None, summarize_exception(error), False

        return message, None, False

    async def refresh_session_message(self, guild_id: int) -> None:
        session = self.store.get_session(guild_id)
        if session is None:
            return

        guild = self.get_guild(guild_id)
        if guild is None:
            return

        message, error, removable = await self.get_session_message(session)
        if message is None:
            if removable:
                self.store.remove_session(guild_id)
            if error:
                print(f"Session refresh skipped for guild {guild_id}: {error}")
            return

        embed = build_session_embed(guild, session, active=True)
        try:
            await message.edit(embed=embed)
        except discord.NotFound:
            self.store.remove_session(guild_id)
        except Exception as error:
            print(
                f"Could not edit session announcement for guild {guild_id}: "
                f"{summarize_exception(error)}"
            )


def main() -> None:
    config = BotConfig.from_env()
    store = SessionStore(config.data_file_path)
    store.load()

    bot = ErlcSessionBot(config, store)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
