import os
import json
import traceback
import re
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv, set_key, find_dotenv

from keep_alive import keep_alive
from server_backup import apply_save_to_guild, load_save_data, save_guild, wipe_guild, list_saves


load_dotenv()

# Preluăm tokenul contului tău din .env
USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")

if not USER_TOKEN:
    raise SystemExit("❌ Eroare: Lipseste DISCORD_USER_TOKEN in fisierul .env!")

# Configurăm self-botul
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    self_bot=True,
    intents=intents
)


def user_can_load_server(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        return False
    return ctx.author == bot.user or ctx.author.guild_permissions.administrator


@bot.event
async def on_ready() -> None:
    print(f"\n✅ Conectat cu succes ca: {bot.user} (ID: {bot.user.id})")
    print(f"📍 Sunt pe {len(bot.guilds)} servere!")
    print("\n📜 Comenzi disponibile:")
    print("  !ping                 - Verifică dacă botul răspunde")
    print("  !save [nume]          - Salvează serverul curent în Supabase")
    print("  !load [nume]          - Încarcă un backup din Supabase (trebuie să fii admin)")
    print("  !clone [invitație]    - Clonează un server dintr-o invitație")
    print("  !saves                - Vezi lista backup-urilor din Supabase")
    print("  !nuke                 - Șterge tot de pe un server (trebuie să fii admin)")
    print("  !token [NOU_TOKEN]    - Schimbă tokenul contului (doar tu)")
    print("  !restart              - Repornește botul (doar tu)")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        try:
            await ctx.send(f"⚠️ Lipsesc argumente! Folosește: `{ctx.prefix}{ctx.command}`")
        except discord.NotFound:
            pass
        return

    print(f"❌ Eroare la comanda {ctx.command}: {error}")
    traceback.print_exception(type(error), error, error.__traceback__)
    try:
        await ctx.send(f"❌ Eroare: {error}")
    except discord.NotFound:
        pass


@bot.command(name="ping")
async def ping_command(ctx: commands.Context) -> None:
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! Latency: {latency_ms}ms")


@bot.command(name="save")
async def save_command(ctx: commands.Context, save_name: str) -> None:
    if ctx.guild is None:
        await ctx.send("⚠️ Această comandă funcționează doar pe un server!")
        return

    if not save_name.strip():
        await ctx.send("⚠️ Folosește: `!save nume_backup`")
        return

    status = await ctx.send("📦 Salvez structura serverului în Supabase...")
    try:
        result = await save_guild(ctx.guild, save_name.strip())
        await status.edit(
            content=(
                f"✅ Serverul **{ctx.guild.name}** a fost salvat cu succes în Supabase!\n"
                f"📁 Nume backup: `{result['save_name']}`\n"
                f"👥 Roluri salvate: {len([r for r in ctx.guild.roles if not r.is_default() and not r.managed])}\n"
                f"📂 Categorii: {len(ctx.guild.categories)}\n"
                f"💬 Canale: {len([c for c in ctx.guild.channels if not isinstance(c, discord.CategoryChannel)])}"
            )
        )
    except ValueError as exc:
        await status.edit(content=str(exc))
        return
    except Exception as exc:
        print(f"❌ Eroare completă: {traceback.format_exc()}")
        await status.edit(content=f"❌ Eroare la salvare: {exc}")
        return


async def _notify_user(ctx: commands.Context, content: str) -> None:
    if ctx.guild:
        for channel in ctx.guild.text_channels:
            if channel.permissions_for(ctx.guild.me).send_messages:
                await channel.send(content)
                return


@bot.command(name="load")
async def load_command(ctx: commands.Context, filename: str) -> None:
    if ctx.guild is None:
        await ctx.send("⚠️ Această comandă funcționează doar pe un server!")
        return

    if not user_can_load_server(ctx):
        await ctx.send("❌ Ai nevoie de permisiunea **Administrator** pentru `!load`!")
        return

    if not filename.strip():
        await ctx.send("⚠️ Folosește: `!load nume_backup`")
        return

    status = await ctx.send("⚠️ ATENȚIE! Tot conținutul serverului va fi ȘTERS! Continuăm?")

    try:
        data = load_save_data(filename.strip())
        await status.edit(content="🗑️ Ștergem canalele și rolurile vechi...")
        wipe_result = await wipe_guild(ctx.guild)
        try:
            await status.edit(content="🔨 Recreez structura din backup...")
        except discord.NotFound:
            pass
        result = await apply_save_to_guild(ctx.guild, data)
    except FileNotFoundError as exc:
        await status.edit(content=str(exc))
        return
    except ValueError as exc:
        await status.edit(content=str(exc))
        return
    except discord.Forbidden:
        message = (
            "❌ Nu ai permisiuni suficiente!\n"
            "Asigură-te că ai **Administrator** (sau Manage Roles + Manage Channels)\n"
            "și că ai un rol deasupra celorlalte!"
        )
        try:
            await status.edit(content=message)
        except discord.NotFound:
            await _notify_user(ctx, message)
        return
    except Exception as exc:
        message = f"❌ Eroare la încărcare: {exc}"
        try:
            await status.edit(content=message)
        except discord.NotFound:
            await _notify_user(ctx, message)
        return

    source_name = data.get("source_guild", {}).get("name", "necunoscut")
    final_message = (
        f"✅ Backup restaurat cu succes pe **{ctx.guild.name}**!\n"
        f"📦 Sursa: `{source_name}`\n"
        f"🗑️ Șters: {wipe_result['channels']} canale, {wipe_result['roles']} roluri\n"
        f"✨ Creat: {result['roles']} roluri, {result['categories']} categorii, {result['channels']} canale"
    )

    try:
        await status.edit(content=final_message)
    except discord.NotFound:
        pass

    await _notify_user(ctx, final_message)


@bot.command(name="saves")
async def list_saves_command(ctx: commands.Context) -> None:
    try:
        saves = list_saves()
        if not saves:
            await ctx.send("⚠️ Nu există niciun backup încă! Folosește `!save nume`!")
            return
        await ctx.send(f"💾 Backup-uri disponibile în Supabase:\n" + "\n".join(saves))
    except Exception as e:
        await ctx.send(f"❌ Eroare la listare: {e}")


@bot.command(name="nuke")
async def nuke_command(ctx: commands.Context) -> None:
    if ctx.guild is None:
        await ctx.send("⚠️ Această comandă funcționează doar pe un server!")
        return

    if not user_can_load_server(ctx):
        await ctx.send("❌ Ai nevoie de permisiunea **Administrator** pentru `!nuke`!")
        return

    status = await ctx.send("⚠️ ATENȚIE! Tot conținutul serverului va fi ȘTERS! Continuăm? (scrie DA pentru a confirma)")

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content.strip().upper() == "DA"

    try:
        await bot.wait_for("message", check=check, timeout=30.0)
    except TimeoutError:
        await status.edit(content="⏱️ Nu a primit confirmare! Am renunțat!")
        return

    await status.edit(content="🗑️ Ștergem tot de pe server...")
    wipe_result = await wipe_guild(ctx.guild)

    # Încercăm să ștergem și numele și iconița
    try:
        await ctx.guild.edit(name="Server Nuked", icon=None)
    except Exception:
        pass

    try:
        await status.edit(
            content=(
                f"✅ Serverul a fost NUKED cu succes!\n"
                f"🗑️ Șters: {wipe_result['channels']} canale, {wipe_result['roles']} roluri"
            )
        )
    except discord.NotFound:
        for channel in ctx.guild.text_channels:
            if channel.permissions_for(ctx.guild.me).send_messages:
                await channel.send(
                    f"✅ Serverul a fost NUKED cu succes!\n"
                    f"🗑️ Șters: {wipe_result['channels']} canale, {wipe_result['roles']} roluri"
                )
                break


@bot.command(name="token")
async def set_token_command(ctx: commands.Context, *, new_token: str) -> None:
    if ctx.author != bot.user:
        await ctx.send("❌ Doar proprietarul contului poate schimba tokenul!")
        return

    if not new_token.strip():
        await ctx.send("⚠️ Folosește: `!token NOUL_TAU_TOKEN`")
        return

    status = await ctx.send("🔐 Actualizez tokenul...")
    try:
        dotenv_path = find_dotenv()
        if not dotenv_path:
            dotenv_path = ".env"
            open(dotenv_path, "a", encoding="utf-8").close()
        set_key(dotenv_path, "DISCORD_USER_TOKEN", new_token.strip())
        await status.edit(content="✅ Tokenul actualizat! Folosește `!restart` pentru a aplica schimbările!")
    except Exception as e:
        print(f"❌ Eroare la actualizarea tokenului: {e}")
        traceback.print_exc()
        await status.edit(content=f"❌ Eroare: {e}")


@bot.command(name="restart")
async def restart_command(ctx: commands.Context) -> None:
    if ctx.author != bot.user:
        await ctx.send("❌ Doar proprietarul contului poate reporni botul!")
        return
    status = await ctx.send("🔄 Repornește botul...")
    try:
        await status.edit(content="✅ Se repornește acum!")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"❌ Eroare la repornire: {e}")
        traceback.print_exc()
        try:
            await status.edit(content=f"❌ Eroare: {e}")
        except discord.NotFound:
            pass


@bot.command(name="clone")
async def clone_command(ctx: commands.Context, invite_link: str) -> None:
    invite_code_match = re.search(r"(?:discord\.gg/|discord\.com/invite/)([a-zA-Z0-9-]+)", invite_link)
    if not invite_code_match:
        await ctx.send("⚠️ Invitație invalidă! Folosește un link de tipul: discord.gg/xxxx")
        return

    invite_code = invite_code_match.group(1)
    status = await ctx.send(f"🔍 Preiau informații despre invitația `{invite_code}`...")

    try:
        invite = await bot.fetch_invite(invite_code)

        if not invite.guild:
            await status.edit(content="❌ Nu am putut accesa serverul din invitație!")
            return

        target_guild = bot.get_guild(invite.guild.id)

        if not target_guild:
            await status.edit(content=f"🚪 Mă alătur la serverul **{invite.guild.name}**...")
            try:
                target_guild = await bot.accept_invite(invite)
            except Exception as e:
                await status.edit(content=f"❌ Nu m-am putut alătura serverului: {e}")
                return

        save_name = invite_code
        await status.edit(content=f"📦 Salvez serverul **{target_guild.name}** ca `{save_name}`...")
        result = await save_guild(target_guild, save_name)

        await status.edit(
            content=(
                f"✅ Serverul **{target_guild.name}** a fost salvat cu succes în Supabase!\n"
                f"📁 Nume backup: `{result['save_name']}`\n"
                f"👥 Roluri salvate: {len([r for r in target_guild.roles if not r.is_default() and not r.managed])}\n"
                f"📂 Categorii: {len(target_guild.categories)}\n"
                f"💬 Canale: {len([c for c in target_guild.channels if not isinstance(c, discord.CategoryChannel)])}\n"
                f"🔧 Pentru a-l clona: mergi pe un server gol (unde ești admin) și scrie: `!load {save_name}`"
            )
        )

    except discord.NotFound:
        await status.edit(content="❌ Invitația nu există sau a expirat!")
    except discord.Forbidden:
        await status.edit(content="❌ Nu am permisiunea să accesez invitația (poate ești banat de pe acel server)!")
    except Exception as e:
        print(f"❌ Eroare la clonare: {e}")
        traceback.print_exc()
        await status.edit(content=f"❌ A apărut o eroare: {e}")


def main() -> None:
    print("🔐 Se conectează cu contul tău Discord...")
    keep_alive()
    bot.run(USER_TOKEN)


if __name__ == "__main__":
    main()
