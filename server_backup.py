import json
import base64
from datetime import datetime, timezone
from typing import Any

import discord
from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()

# Conectare la Supabase (lazily initialized)
_supabase_client = None

def get_supabase_client():
    global _supabase_client
    if _supabase_client is None:
        SUPABASE_URL = os.getenv("SUPABASE_URL")
        SUPABASE_KEY = os.getenv("SUPABASE_KEY")
        
        print(f"DEBUG SUPABASE_URL: {'Setat' if SUPABASE_URL else 'Lipseste'}")
        print(f"DEBUG SUPABASE_KEY: {'Setat' if SUPABASE_KEY else 'Lipseste'}")
        
        if not SUPABASE_URL:
            raise SystemExit("EROARE: SUPABASE_URL lipsește! Adaugă-l în Render Environment Variables!")
        if not SUPABASE_KEY:
            raise SystemExit("EROARE: SUPABASE_KEY lipsește! Adaugă-l în Render Environment Variables!")
        
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


async def save_guild(guild: discord.Guild, save_name: str) -> dict:
    safe_name = "".join(c for c in save_name if c.isalnum() or c in ("-", "_")).strip()
    if not safe_name:
        raise ValueError("Numele fișierului nu este valid!")

    roles = [
        {
            "name": role.name,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position": role.position,
            "managed": role.managed,
            "id": role.id,
        }
        for role in sorted(guild.roles, key=lambda r: r.position)
        if not role.is_default() and not role.managed
    ]

    categories = [
        _serialize_channel(category)
        for category in sorted(guild.categories, key=lambda c: c.position)
    ]

    channels = [
        _serialize_channel(channel)
        for channel in sorted(guild.channels, key=lambda c: c.position)
        if not isinstance(channel, discord.CategoryChannel)
    ]

    icon_base64 = None
    if guild.icon:
        try:
            icon_bytes = await guild.icon.read()
            icon_base64 = base64.b64encode(icon_bytes).decode("utf-8")
        except Exception:
            icon_base64 = None

    payload = {
        "save_name": safe_name,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "source_guild": {
            "name": guild.name,
            "id": guild.id,
            "icon": icon_base64,
        },
        "roles": roles,
        "categories": categories,
        "channels": channels,
    }

    # Salvează în Supabase
    try:
        supabase = get_supabase_client()
        # Dacă există deja backup-ul cu același nume, îl actualizăm; altfel, îl creăm
        existing = supabase.table("backups").select("id").eq("save_name", safe_name).execute()
        if existing.data:
            supabase.table("backups").update({"data": payload}).eq("save_name", safe_name).execute()
        else:
            supabase.table("backups").insert({"save_name": safe_name, "data": payload}).execute()
        return {"save_name": safe_name, "source_guild": payload["source_guild"]}
    except Exception as e:
        raise ValueError(f"Eroare la salvare în Supabase: {e}")


def load_save_data(save_name: str) -> dict:
    safe_name = "".join(c for c in save_name if c.isalnum() or c in ("-", "_")).strip()
    supabase = get_supabase_client()
    response = supabase.table("backups").select("data").eq("save_name", safe_name).execute()
    if not response.data:
        raise FileNotFoundError(f"Backup-ul `{safe_name}` nu există!")
    return response.data[0]["data"]


def list_saves() -> list[str]:
    supabase = get_supabase_client()
    response = supabase.table("backups").select("save_name, created_at").order("created_at", desc=True).execute()
    return [f"- `{item['save_name']}` ({item['created_at'][:16]})" for item in response.data]


# Restul funcțiilor (pentru serializare, wipe, apply) rămân la fel
def _overwrite_to_dict(overwrite: discord.PermissionOverwrite) -> dict[str, bool | None]:
    data: dict[str, bool | None] = {}
    for name, value in overwrite:
        data[name] = value
    return data


def _serialize_overwrites(
    overwrites: dict[Any, discord.PermissionOverwrite],
    guild: discord.Guild,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for target, overwrite in overwrites.items():
        entry: dict[str, Any] = {
            "allow": _overwrite_to_dict(overwrite),
        }
        if isinstance(target, discord.Role):
            entry["type"] = "role"
            entry["name"] = target.name
            entry["id"] = target.id
        elif isinstance(target, discord.Member):
            entry["type"] = "member"
            entry["name"] = str(target)
            entry["id"] = target.id
        else:
            entry["type"] = "unknown"
            entry["id"] = getattr(target, "id", None)
        result.append(entry)
    return result


def _serialize_channel(channel: discord.abc.GuildChannel) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": channel.name,
        "type": str(channel.type),
        "position": channel.position,
        "category": channel.category.name if channel.category else None,
        "overwrites": _serialize_overwrites(channel.overwrites, channel.guild),
    }

    if isinstance(channel, discord.TextChannel):
        data["topic"] = channel.topic
        data["nsfw"] = channel.nsfw
        data["slowmode_delay"] = channel.slowmode_delay
    elif isinstance(channel, discord.VoiceChannel):
        data["bitrate"] = channel.bitrate
        data["user_limit"] = channel.user_limit
    elif isinstance(channel, discord.ForumChannel):
        data["topic"] = channel.topic
        data["nsfw"] = channel.nsfw
        data["slowmode_delay"] = channel.slowmode_delay

    return data


async def wipe_guild(guild: discord.Guild) -> dict[str, int]:
    deleted_channels = 0
    deleted_roles = 0

    for channel in list(guild.channels):
        if isinstance(channel, discord.CategoryChannel):
            continue
        await channel.delete(reason="Restore server backup - wipe")
        deleted_channels += 1

    for category in list(guild.categories):
        await category.delete(reason="Restore server backup - wipe")
        deleted_channels += 1

    bot_member = guild.me
    bot_top_role = bot_member.top_role if bot_member else None

    deletable_roles = [
        role
        for role in guild.roles
        if not role.is_default()
        and not role.managed
        and (bot_top_role is None or role.position < bot_top_role.position)
    ]

    for role in sorted(deletable_roles, key=lambda item: item.position):
        await role.delete(reason="Restore server backup - wipe")
        deleted_roles += 1

    return {
        "channels": deleted_channels,
        "roles": deleted_roles,
    }


async def apply_save_to_guild(guild: discord.Guild, data: dict[str, Any]) -> dict[str, int]:
    role_map: dict[str, discord.Role] = {role.name: role for role in guild.roles}
    created_roles = 0
    created_categories = 0
    created_channels = 0

    everyone = guild.default_role
    bot_member = guild.me
    bot_top_role = bot_member.top_role if bot_member else None

    # Setăm numele și iconița serverului
    source_guild = data.get("source_guild", {})
    new_name = source_guild.get("name")
    icon_base64 = source_guild.get("icon")
    
    icon_bytes = None
    if icon_base64:
        try:
            icon_bytes = base64.b64decode(icon_base64)
        except Exception:
            icon_bytes = None
    
    if new_name and new_name != guild.name:
        try:
            await guild.edit(name=new_name, icon=icon_bytes)
        except Exception:
            pass
    elif icon_bytes:
        try:
            await guild.edit(icon=icon_bytes)
        except Exception:
            pass

    for role_data in data.get("roles", []):
        if role_data.get("managed"):
            continue

        name = role_data["name"]

        if bot_top_role and role_data.get("permissions"):
            new_perms = discord.Permissions(role_data["permissions"])
            if new_perms.administrator and bot_top_role.position <= max(
                (r.position for r in guild.roles if r != everyone),
                default=0,
            ):
                continue

        role = await guild.create_role(
            name=name,
            permissions=discord.Permissions(role_data.get("permissions", 0)),
            colour=discord.Colour(role_data.get("color", 0)),
            hoist=role_data.get("hoist", False),
            mentionable=role_data.get("mentionable", False),
            reason="Restore server backup",
        )
        role_map[name] = role
        created_roles += 1

    category_map: dict[str, discord.CategoryChannel] = {}

    for category_data in data.get("categories", []):
        name = category_data["name"]
        overwrites = _build_overwrites(guild, category_data.get("overwrites", []), role_map)

        category = await guild.create_category(
            name=name,
            overwrites=overwrites,
            reason="Restore server backup",
        )
        category_map[name] = category
        created_categories += 1

    for channel_data in data.get("channels", []):
        name = channel_data["name"]

        category_name = channel_data.get("category")
        category = category_map.get(category_name) if category_name else None
        overwrites = _build_overwrites(guild, channel_data.get("overwrites", []), role_map)
        channel_type = channel_data.get("type", "text")

        if channel_type == str(discord.ChannelType.text):
            await guild.create_text_channel(
                name=name,
                category=category,
                topic=channel_data.get("topic"),
                nsfw=channel_data.get("nsfw", False),
                slowmode_delay=channel_data.get("slowmode_delay", 0),
                overwrites=overwrites,
                reason="Restore server backup",
            )
            created_channels += 1
        elif channel_type == str(discord.ChannelType.voice):
            await guild.create_voice_channel(
                name=name,
                category=category,
                bitrate=channel_data.get("bitrate", 64000),
                user_limit=channel_data.get("user_limit", 0),
                overwrites=overwrites,
                reason="Restore server backup",
            )
            created_channels += 1
        elif channel_type == str(discord.ChannelType.forum):
            await guild.create_forum(
                name=name,
                category=category,
                topic=channel_data.get("topic"),
                nsfw=channel_data.get("nsfw", False),
                slowmode_delay=channel_data.get("slowmode_delay", 0),
                overwrites=overwrites,
                reason="Restore server backup",
            )
            created_channels += 1

    return {
        "roles": created_roles,
        "categories": created_categories,
        "channels": created_channels,
    }


def _build_overwrites(
    guild: discord.Guild,
    entries: list[dict[str, Any]],
    role_map: dict[str, discord.Role],
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {}

    for entry in entries:
        target_type = entry.get("type")
        allow = entry.get("allow", {})
        overwrite = discord.PermissionOverwrite(**allow)

        if target_type == "role":
            role_name = entry.get("name")
            role = role_map.get(role_name)
            if role is None and entry.get("id"):
                role = guild.get_role(entry["id"])
            if role is not None:
                overwrites[role] = overwrite
        elif target_type == "member":
            member_id = entry.get("id")
            if member_id:
                member = guild.get_member(member_id)
                if member is not None:
                    overwrites[member] = overwrite

    return overwrites
