import discord
from discord.ext import commands, tasks
from discord import ui, Interaction
import os, json, random, csv, time, asyncio, pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from flask import Flask
from threading import Thread
import firebase_admin
from firebase_admin import credentials, firestore

cred_json = os.environ['FIREBASE_CREDENTIALS_JSON']
cred = credentials.Certificate(json.loads(cred_json))
firebase_admin.initialize_app(cred)
db = firestore.client()

# ✅ Allowed (guild_id, channel_id) pairs
ALLOWED_CHANNELS = {
    (1386929798831538248, 1387620244746534994),  # Cavern of Dreams, Prayers of the Wishful
    (1386929798831538248, 1387653760175706172),  # Cavern of Dreams, bot-commands
    (715855925285486682, 715855925285486685),    # LordStarship's Server, debugging
}

# 🛠 Debugging channels for b!jou
DEBUG_CHANNELS = {
    (1386929798831538248, 1387653760175706172),
    (715855925285486682, 715855925285486685),
}

########## CONFIG ##########
DISCORD_EPOCH = 1420070400000               # discord snowflake
TRIVIA_CSV = 'trivia_sheet.csv'             # trivia question file
TRIVIA_DATA_FILE = 'trivia_data.json'       # trivia data file
QUIZ_LENGTH_SEC      = 270                  # 4m 30s players can answer
QUIZ_LENGTH_SEC_LOOP = 30                   # 30s for fast trivia
POST_ANSWER_WINDOW   = 3                    # window that stays open after 1st correct
INTER_ROUND_COOLDOWN = 300                  # total cycle time = 5 min
PRE_ANNOUNCE_SEC     = 5                    # “Trivia in 5 seconds!” heads‑up
BACKUP_CHANNEL_ID = 1389077962116038848     # channel to receive backup
BACKUP_INTERVAL_MINUTES = 60                # backup every 1 hour
DEFAULT_TARGET_NAME = "Spica"               # used when b!hit has no mention
TEMPLATE_FILE       = "hit_templates.csv"   # templates for the hit
DAMAGE_FILE         = "damage_phrases.csv"  # templates for the damage
#############################

TRIVIA_MODE1_CHANNELS = {
    1387653760175706172,    # Cavern of Dreams #bot-commands
    715855925285486685,     # LordStarship debug
    1389860314488635504,    # Classic Trivia channel
    # Add more channel IDs for Mode 1 here
}

TRIVIA_MODE2_CHANNELS = {
    1387653760175706172,    # Cavern of Dreams #bot-commands
    715855925285486685,     # LordStarship debug
    1389860487499612190,    # Speedrun Trivia channel
    # Add more channel IDs for Mode 2 here
}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="b!", intents=intents)
bot.remove_command('help')

# JSON data helper
async def load_user_data(guild_id, user_id):
    doc = db.collection('guilds').document(guild_id).collection('users').document(user_id).get()
    return doc.to_dict() if doc.exists else {"count": 0, "streak": 0, "last_prayed": None}

async def save_user_data(guild_id, user_id, data):
    db.collection('guilds').document(guild_id).collection('users').document(user_id).set(data, merge=True)

async def increment_global_prayers(guild_id):
    ref = db.collection('guilds').document(guild_id)
    ref.set({"global": firestore.Increment(1)}, merge=True)
    
def get_footer_info(guild):
    if guild and guild.icon:
        return {"text": guild.name, "icon_url": guild.icon.url}
    return {"text": guild.name if guild else "DM", "icon_url": None}

PRAYER_QUOTES = [
    "*'May your dreams be guided by starlight.'*",
    "*'The cosmos hears your prayer.'*",
    "*'Even the faintest wish echoes across galaxies.'*"
]

THUMBNAIL_URL = "https://cdn.discordapp.com/attachments/1387623832549986325/1387658664185303061/th-913589016.jpeg"
IMAGE_URL = "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Fstatic.zerochan.net%2FGeopelia.full.4086266.jpg"

# PRAY COMMAND
@bot.command()
async def pray(ctx, *args):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    guild_id = str(ctx.guild.id) if ctx.guild else "DM"
    user_id = str(ctx.author.id)
    today = datetime.utcnow().date()

    # Load data
    user_data = await load_user_data(guild_id, user_id)
    guild_ref = db.collection("guilds").document(guild_id)
    guild_doc = guild_ref.get()
    guild_data = guild_doc.to_dict() if guild_doc.exists else {"global": 0}

    last_prayed_str = user_data.get("last_prayed")
    last_prayed_date = datetime.strptime(last_prayed_str, "%Y-%m-%d").date() if last_prayed_str else None

    is_first_pray = last_prayed_date is None
    continued_streak = False
    reset_streak = False

    # STREAK logic for Spica
    is_spica_pray = len(args) == 0 and len(ctx.message.mentions) == 0 and len(ctx.message.role_mentions) == 0

    if is_spica_pray:
        if last_prayed_date == today:
            pass
        elif last_prayed_date == today - timedelta(days=1):
            user_data["streak"] += 1
            continued_streak = True
        elif is_first_pray:
            user_data["streak"] = 1
        else:
            user_data["streak"] = 1
            reset_streak = True
    else:
        user_data["streak"] = user_data.get("streak", 0)

    user_data["count"] = user_data.get("count", 0) + 1
    user_data["last_prayed"] = str(today)

    await save_user_data(guild_id, user_id, user_data)
    await increment_global_prayers(guild_id)
    db.collection("guilds").document(guild_id).collection("leaderboard").document(user_id).set({
        "user_id": user_id,
        "count": user_data["count"],
        "streak": user_data["streak"]
    }, merge=True)

    # Build Embed
    streak = user_data["streak"]
    mentions = list(ctx.message.mentions)
    role_mentions = list(ctx.message.role_mentions)

    # Add from raw IDs
    for tok in args:
        if tok.isdigit():
            if ctx.guild:
                obj = ctx.guild.get_member(int(tok)) or ctx.guild.get_role(int(tok))
                if isinstance(obj, discord.Member) and obj not in mentions:
                    mentions.append(obj)
                elif isinstance(obj, discord.Role) and obj not in role_mentions:
                    role_mentions.append(obj)

    quote = random.choice(PRAYER_QUOTES)

    embed = discord.Embed(color=discord.Color.purple())
    embed.set_author(name="Prayers of the Wishful")
    embed.set_thumbnail(url=THUMBNAIL_URL)
    embed.set_image(url=IMAGE_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])

    # ===== MESSAGE HANDLING =====

    if len(mentions) == 1 and mentions[0].id == bot.user.id:
        embed.title = "⁉️ A prayer is sent... to me!?"
        embed.description = f"R-really? you would pray for me? thank you!! ///\n\n> {quote}"

    elif is_spica_pray:
        embed.title = "🙏 A prayer is sent to Spica!"
        embed.description = f"**{ctx.author.name}** has prayed for **Spica the Dreamer!**\nHer journey toward the throne of Procyon shall succeed!\n"
        if continued_streak:
            embed.description += f"🔥 **Daily Streak:** `{streak}` days! Keep praying for the Dreamer! 🔥\n"
        elif reset_streak:
            embed.description += "😢 Your daily streak was broken. Let's start again today!\n"
        embed.description += f"\n> {quote}"

    elif mentions:
        if len(mentions) == 1:
            embed.title = f"💫 A prayer is sent to {mentions[0].name}!"
            embed.description = f"**{ctx.author.name}** has prayed for **{mentions[0].name}**! How sweet!\n\n> {quote}"
        elif len(mentions) == 2:
            embed.title = f"✨ Prayers are sent to {mentions[0].name} and {mentions[1].name}!"
            embed.description = f"**{ctx.author.name}** prays for their friends, **{mentions[0].name}** and **{mentions[1].name}**! How caring!\n\n> {quote}"
        elif len(mentions) == 3:
            embed.title = f"🌟 Lots of prayers for {mentions[0].name}, {mentions[1].name}, and {mentions[2].name}!"
            embed.description = f"Wow! It seems like **{ctx.author.name}** has a lot of friends!\nSuch a kind soul!\n\n> {quote}"
        else:
            embed.title = "🌌 Prayers are sent to everyone!"
            embed.description = f"Lots of prayers are sent to everybody!\n**{ctx.author.name}** loves everyone so much they're willing to send many!\n\n> {quote}"

    elif role_mentions:
        role_names = ", ".join(f"@{r.name}" for r in role_mentions)
        embed.title = "🧑‍🤝‍🧑 Prayers to a whole role!"
        embed.description = f"**{ctx.author.name}** sends prayers to the roles: {role_names}\n\n> {quote}"

    else:
        text_target = " ".join(args)
        embed.title = "⭐ A prayer is sent to somebody!"
        embed.description = f"**{ctx.author.name}** sends a prayer for **{text_target}**!\nWhoever they are, they have a lovely friend praying for them!\n\n> {quote}"

    await ctx.send(embed=embed)

# b!stats (Shows stats of all prayers)
@bot.command()
async def stats(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    guild_id = str(ctx.guild.id)
    user_id = str(ctx.author.id)
    user_data = await load_user_data(guild_id, user_id)
    guild_doc = db.collection("guilds").document(guild_id).get()
    guild_data = guild_doc.to_dict() if guild_doc.exists else {"global": 0}

    embed = discord.Embed(
        title="📊 Prayer Stats",
        description=(
            f"{ctx.author.mention}, here are your stats:\n\n"
            f"**Your total prayers:** `{user_data.get('count', 0)}`\n"
            f"🔥 **Current streak:** `{user_data.get('streak', 0)}`\n"
            f"🌌 **Global prayers:** `{guild_data.get('global', 0)}`"
        ),
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=THUMBNAIL_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])
    await ctx.send(embed=embed)

# b!top (Top praying leaderboard)
@bot.command()
async def top(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    guild_id = str(ctx.guild.id)
    lb_ref = db.collection('guilds').document(guild_id).collection('leaderboard')
    query = lb_ref.order_by('count', direction=firestore.Query.DESCENDING).limit(5)
    top_docs = query.stream()

    desc = ""
    for doc in top_docs:
        data = doc.to_dict()
        user = await bot.fetch_user(int(data['user_id']))
        desc += f"**{user.name}** — `{data['count']}` prayers (🔥 {data['streak']}d streak)\n"

    embed = discord.Embed(
        title="🏆 Top Prayers",
        description=desc if desc else "No prayers yet!",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=THUMBNAIL_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])
    await ctx.send(embed=embed)

JOU_CSV = "bot_texts.csv"
JOU_LINES: list[str] = []

def load_jou_lines():
    path = pathlib.Path(JOU_CSV)
    if not path.exists():
        print(f"[JOU] '{JOU_CSV}' not found – using default lines.")
        return
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        JOU_LINES.clear()
        for row in reader:
            line = (row.get("line") or "").strip()
            if line:
                JOU_LINES.append(line)
    print(f"[JOU] Loaded {len(JOU_LINES)} fun lines.")
    
# Ping 
@bot.command()
async def jou(ctx, target: discord.Member | None = None):
    """
    Admin with no args   -> classic latency ping.
    Admin with target    -> fun line.
    Regular member no arg (target ignored) -> fun line at Spica (or provided target).
    """
    is_admin = ctx.author.guild_permissions.administrator if ctx.guild else False

    # ── Admin latency ping ──────────────────────────────
    if is_admin and target is None:
        before = time.monotonic()
        msg    = await ctx.send("B1jou is calculating…")
        ping   = (time.monotonic() - before) * 1000
        return await msg.edit(content=f"🏓 Pong! Latency: `{int(ping)} ms`")

    # ── Fun personalised line ──────────────────────────
    if not JOU_LINES:        # fallback if CSV empty / absent
        JOU_LINES.extend([
            "**{author}** yeets a cosmic brick at **{target}**! Ouch!",
            "**{author}** shares an existential meme with **{target}**.",
            "**{author}** activates RGB powers against **{target}**!",
        ])

    line  = random.choice(JOU_LINES)
    author_name  = ctx.author.display_name
    target_name  = target.display_name if target else DEFAULT_TARGET_NAME
    line_filled  = line.format(author=author_name, target=target_name)

    await ctx.send(line_filled)

# Member Joined Action
welcome_messages = {}
class JoinActionView(ui.View):
    def __init__(self, member):
        super().__init__(timeout=None)
        self.member = member

    async def disable_buttons(self, interaction: Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

        msg = welcome_messages.pop(self.member.id, None)
        if msg:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @ui.button(label="Assign Member Role", style=discord.ButtonStyle.success)
    async def assign_user_role(self, interaction: Interaction, button: ui.Button):
        role = interaction.guild.get_role(1387624217117462538)
        if not role:
            await interaction.response.send_message("Role not found.", ephemeral=True)
            return
        if role in self.member.roles:
            await interaction.response.send_message("They already have the role!", ephemeral=True)
            return
        try:
            await self.member.add_roles(role, reason="Granted by bot")
            await interaction.response.send_message(f"✨ {interaction.user.mention} assigned role to **{self.member.display_name}**.")
            await self.disable_buttons(interaction)
        except discord.Forbidden:
            await interaction.response.send_message("I lack permission to assign the role.", ephemeral=True)
            
    @ui.button(label="Assign Bot Role", style=discord.ButtonStyle.success)
    async def assign_bot_role(self, interaction: Interaction, button: ui.Button):
        role = interaction.guild.get_role(1387624066801733643)
        if not role:
            await interaction.response.send_message("Role not found.", ephemeral=True)
            return
        if role in self.member.roles:
            await interaction.response.send_message("They already have the role!", ephemeral=True)
            return
        try:
            await self.member.add_roles(role, reason="Granted by bot")
            await interaction.response.send_message(f"✨ {interaction.user.mention} assigned role to **{self.member.display_name}**.")
            await self.disable_buttons(interaction)
        except discord.Forbidden:
            await interaction.response.send_message("I lack permission to assign the role.", ephemeral=True)

    @ui.button(label="Kick", style=discord.ButtonStyle.danger)
    async def kick_user(self, interaction: Interaction, button: ui.Button):
        try:
            await self.member.kick(reason="Kicked by admin via bot")
            await interaction.response.send_message(f"🚪 {interaction.user.mention} kicked **{self.member.display_name}**.")
            await self.disable_buttons(interaction)
        except discord.Forbidden:
            await interaction.response.send_message("I cannot kick this user.", ephemeral=True)

    @ui.button(label="Ban", style=discord.ButtonStyle.danger)
    async def ban_user(self, interaction: Interaction, button: ui.Button):
        try:
            await self.member.ban(reason="Banned by admin via bot")
            await interaction.response.send_message(f"⛔ {interaction.user.mention} banned **{self.member.display_name}**.")
            await self.disable_buttons(interaction)
        except discord.Forbidden:
            await interaction.response.send_message("I cannot ban this user.", ephemeral=True)

# Automatic Message in both Admin and Welcome channel when User Joiened
@bot.event
async def on_member_join(member):
    if member.guild.id != 1386929798831538248:
        return

    welcome_channel = member.guild.get_channel(1387651996525269072)
    admin_channel = member.guild.get_channel(1387653760175706172)
    member_role = member.guild.get_role(1387624217117462538)
    admin_role = member.guild.get_role(1386931817545990246)

    if member_role in member.roles:
        return

    if welcome_channel:
        welcome_msg = await welcome_channel.send(
            f"🌟 Welcome to **Cavern of Dreams**, {member.mention}!\n"
            "Please wait patiently while the stars align and a council member grants you access!"
        )
        welcome_messages[member.id] = welcome_msg

    if admin_channel and admin_role:
        await admin_channel.send(
            f"🔔 **New arrival detected**: {member.mention}\n"
            f"{admin_role.mention}, please take action below.",
            view=JoinActionView(member)
        )
        
# Trivia
trivia_list: list[dict] = []
trivia_task: asyncio.Task | None = None
trivia_running = {
    1: False,  # Classic
    2: False   # Speedrun
}
current_q: dict | None = None
answerers: dict = {}
round_started_at = 0.0
answered = False
first_correct_event = asyncio.Event()
active_trivia_channel_id = {
    1: None,
    2: None
}
# Lock/Unlock Channel
async def _lock_channel(chan: discord.TextChannel, *, allow_send: bool):
    ow = chan.overwrites_for(chan.guild.default_role)
    ow.send_messages = allow_send
    await chan.set_permissions(chan.guild.default_role, overwrite=ow)

# Helper functions
def load_trivia():
    trivia_list.clear()
    path = pathlib.Path(TRIVIA_CSV)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path.resolve()}")
    with path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            q = row.get("question", "").strip()
            a = [x.strip().lower() for x in row.get("answers", "").split("|") if x.strip()]
            if q and a:
                trivia_list.append({"q": q, "answers": a})
    if not trivia_list:
        raise RuntimeError("No trivia questions found in CSV!")
    random.shuffle(trivia_list)
    print(f"[TRIVIA] Loaded {len(trivia_list)} questions.")

def load_trivia_data():
    p = pathlib.Path(TRIVIA_DATA_FILE)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        print("[TRIVIA] Corrupt JSON, resetting.")
        return {}

def save_trivia_data(data: dict):
    tmp = pathlib.Path(TRIVIA_DATA_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(TRIVIA_DATA_FILE)

# b!starttrivia 1
async def trivia_loop(channel: discord.TextChannel):
    global current_q, answerers, answered, round_started_at, trivia_running
    data = load_trivia_data()

    try:
        while trivia_running:
            if not trivia_list:
                load_trivia()  # reload and reshuffle

            current_q = random.choice(trivia_list)
            trivia_list.remove(current_q)
            answerers.clear()
            answered = False
            first_correct_event.clear()
            await _lock_channel(channel, allow_send=True)

            embed = (discord.Embed(title="🌌 Spica's Trivia Challenge",
                                   description=current_q["q"],
                                   color=discord.Color.purple())
                     .set_thumbnail(url=THUMBNAIL_URL))
            question_msg  = await channel.send(embed=embed)
            round_started_at = ((question_msg.id >> 22) + DISCORD_EPOCH)   

            try:
                await asyncio.wait_for(first_correct_event.wait(), timeout=QUIZ_LENGTH_SEC)
            except asyncio.TimeoutError:
                await channel.send(embed=discord.Embed(
                    title="⏱️ Time’s Up!",
                    description="Nobody got it right… maybe next time, Dreamers.",
                    color=discord.Color.dark_grey()))
            else:
                await asyncio.sleep(POST_ANSWER_WINDOW)

                # Tally code
                results = sorted(answerers.values(), key=lambda r: r['time_ms'])
                lines   = []

                for res in results:
                    uid        = str(res['user'].id)
                    prev       = data.get(uid)

                    if isinstance(prev, int):
                        prev = {"score": prev, "best_time": float("inf"), "best_question": ""}

                    prev = prev or {"score": 0, "best_time": float("inf"), "best_question": ""}

                    new_score = prev["score"] + res["points"]

                    if res["time_ms"] < prev["best_time"]:
                        best_time     = res["time_ms"]
                        best_question = current_q["q"]
                    else:
                        best_time     = prev["best_time"]
                        best_question = prev["best_question"]

                    data[uid] = {
                        "score": new_score,
                        "best_time": best_time,
                        "best_question": best_question
                    }

                    t = f"{res['time_ms']//1000}.{res['time_ms']%1000:03d}s"
                    lines.append(f"{res['user'].display_name} — `{res['points']} pt` ({t})")

                save_trivia_data(data)
                # ─────────────

                await channel.send(embed=discord.Embed(
                    title="📜 Round Results",
                    description="\n".join(lines),
                    color=discord.Color.gold())
                    .set_thumbnail(url=THUMBNAIL_URL))

            await _lock_channel(channel, allow_send=False)

            now_ms = ((discord.utils.time_snowflake(discord.utils.utcnow()) >> 22) + DISCORD_EPOCH)
            elapsed_ms = now_ms - round_started_at
            remaining_cooldown_ms = (INTER_ROUND_COOLDOWN * 1000) - elapsed_ms - (PRE_ANNOUNCE_SEC * 1000)
            if remaining_cooldown_ms > 0:
                await asyncio.sleep(remaining_cooldown_ms / 1000)

            await channel.send("✨ Trivia resumes in **5 seconds**…")
            await asyncio.sleep(PRE_ANNOUNCE_SEC)
    finally:
        await _lock_channel(channel, allow_send=True)
        trivia_running = False
        current_q = None

# b!starttrivia 2
async def speedrun_trivia_loop(channel: discord.TextChannel):
    global current_q, answerers, answered, round_started_at, trivia_running
    session_scores = {}
    questions_asked = 0
    data = load_trivia_data()

    try:
        while trivia_running and questions_asked < 30:
            if not trivia_list:
                load_trivia()  # reload and reshuffle

            current_q = random.choice(trivia_list)
            trivia_list.remove(current_q)
            answerers.clear()
            answered = False
            first_correct_event.clear()

            embed = (discord.Embed(title=f"Spica's Fast Trivia #{questions_asked+1}",
                    description=current_q["q"],
                    color=discord.Color.teal())
                    .set_thumbnail(url=THUMBNAIL_URL))

            question_msg  = await channel.send(embed=embed)
            round_started_at = ((question_msg.id >> 22) + DISCORD_EPOCH)  

            try:
                await asyncio.wait_for(first_correct_event.wait(), timeout=QUIZ_LENGTH_SEC_LOOP)
            except asyncio.TimeoutError:
                await channel.send(embed=discord.Embed(
                    title="⏱️ Time’s Up!",
                    description="Nobody got it right… maybe next one.",
                    color=discord.Color.dark_grey()))
            else:
                await asyncio.sleep(POST_ANSWER_WINDOW)

                # ── tally section ──
                results = sorted(answerers.values(), key=lambda r: r['time_ms'])
                lines   = []

                for res in results:
                    uid        = str(res['user'].id)
                    session_scores[uid] = session_scores.get(uid, 0) + res['points']
                    prev       = data.get(uid)

                    if isinstance(prev, int):
                        prev = {"score": prev, "best_time": float("inf"), "best_question": ""}

                    prev = prev or {"score": 0, "best_time": float("inf"), "best_question": ""}

                    new_score = prev["score"] + res["points"]

                    if res["time_ms"] < prev["best_time"]:
                        best_time     = res["time_ms"]
                        best_question = current_q["q"]
                    else:
                        best_time     = prev["best_time"]
                        best_question = prev["best_question"]

                    data[uid] = {
                        "score": new_score,
                        "best_time": best_time,
                        "best_question": best_question
                    }

                    t = f"{res['time_ms']//1000}.{res['time_ms']%1000:03d}s"
                    lines.append(f"{res['user'].display_name} — `{res['points']} pt` ({t})")

                save_trivia_data(data)
                # ─────────────
                
                await channel.send(embed=discord.Embed(
                    title="📜 Round Results",
                    description="\n".join(lines),
                    color=discord.Color.gold()))

            await asyncio.sleep(5)
            questions_asked += 1

        if session_scores:
            leaderboard = sorted(session_scores.items(), key=lambda t: t[1], reverse=True)
            lines = []
            for i, (uid, score) in enumerate(leaderboard, 1):
                user = await bot.fetch_user(int(uid))
                lines.append(f"**{i}.** {user.display_name} — `{score}` points")
            await channel.send(embed=discord.Embed(
                title="🏁 Speedrun Leaderboard",
                description="\n".join(lines),
                color=discord.Color.green())
                .set_thumbnail(url=THUMBNAIL_URL))
        else:
            await channel.send("No one scored any points this session.")
    finally:
        trivia_running = False
        current_q = None

# b!starttrivia main command
@bot.command()
@commands.has_permissions(administrator=True)
async def starttrivia(ctx, mode: int = 1):
    global trivia_task, trivia_running, active_trivia_channel_id

    channel_id = ctx.channel.id

    if mode == 1 and channel_id not in TRIVIA_MODE1_CHANNELS:
        return await ctx.send("❌ This channel is not allowed for Classic Trivia.")
    elif mode == 2 and channel_id not in TRIVIA_MODE2_CHANNELS:
        return await ctx.send("❌ This channel is not allowed for Speedrun Trivia.")

    if trivia_running[mode]:
        return await ctx.send("❗ That mode is already running!")

    load_trivia()
    trivia_running[mode] = True
    active_trivia_channel_id[mode] = ctx.channel.id

    if mode == 2:
        trivia_task = asyncio.create_task(speedrun_trivia_loop(ctx.channel))
        await ctx.send("💫 Speedrun Trivia started! 30 rapid-fire questions incoming!")
    else:
        trivia_task = asyncio.create_task(trivia_loop(ctx.channel))
        await ctx.send("🌠 Classic Trivia has begun! Answer wisely, Dreamers!")
        
# b!stoptrivia to stop trivia command
@bot.command()
@commands.has_permissions(administrator=True)
async def stoptrivia(ctx):
    global trivia_task, trivia_running

    cid = ctx.channel.id

    if cid in TRIVIA_MODE1_CHANNELS:
        mode = 1
    elif cid in TRIVIA_MODE2_CHANNELS:
        mode = 2
    else:
        return await ctx.send("❌ This channel can't stop trivia.")

    if not trivia_running.get(mode, False):
        return await ctx.send(f"❌ Trivia isn’t running for mode {mode}.")

    trivia_running[mode] = False
    if trivia_task:
        trivia_task.cancel()
        trivia_task = None

    await ctx.send(f"🛑 Trivia mode {mode} stopped. Until next time, Dreamers.")

# b!triviatop to view top points
@bot.command()
async def triviatop(ctx):
    data = load_trivia_data()
    if not data:
        return await ctx.send("Nobody has scored yet!")

    # Filter out users with valid structured data
    valid_data = {
        uid: stats if isinstance(stats, dict) else {"score": stats}
        for uid, stats in data.items()
    }

    top5 = sorted(valid_data.items(), key=lambda t: t[1].get("score", 0), reverse=True)[:5]
    lines = []
    footer_info = get_footer_info(ctx.guild)

    for i, (uid, stats) in enumerate(top5, 1):
        user = await bot.fetch_user(int(uid))
        score = stats.get("score", 0)
        best_time = stats.get("best_time")
        question = stats.get("best_question", "–")
        time_str = f"{best_time // 1000}.{best_time % 1000:03d}s" if isinstance(best_time, (int, float)) else "N/A"

        lines.append(
            f"**{i}. {user.display_name}** Total Points: `{score}` pts\n"
            f"PB: `{time_str}` on *{question}*"
        )

    await ctx.send(embed=discord.Embed(
        title="🌟 Trivia Leaderboard",
        description="\n".join(lines),
        color=discord.Color.blue()
    ).set_thumbnail(url=THUMBNAIL_URL)
    .set_footer(text=footer_info['text'], icon_url=footer_info['icon_url']))

# b!triviastats
@bot.command()
async def triviastats(ctx, target_input: str = None):
    # Default to command author if no argument is given
    if not target_input:
        target = ctx.author
    else:
        # Try to resolve as mention or ID
        try:
            if target_input.isdigit():
                target = await bot.fetch_user(int(target_input))
            else:
                target = await commands.MemberConverter().convert(ctx, target_input)
        except Exception:
            return await ctx.send("❌ Couldn't find that user.")
        
    uid = str(target.id)
    data = load_trivia_data()
    stats = data.get(uid)
    footer_info = get_footer_info(ctx.guild)

    if not stats:
        return await ctx.send(f"{target.display_name} hasn't scored yet!")

    # Handle legacy int-only score
    if isinstance(stats, int):
        stats = {"score": stats}

    score = stats.get("score", 0)
    best_time = stats.get("best_time")
    question = stats.get("best_question", "–")
    time_str = f"{best_time // 1000}.{best_time % 1000:03d}s" if isinstance(best_time, (int, float)) else "N/A"

    embed = discord.Embed(
        title=f"📊 Trivia Stats – {target.display_name}",
        description=(
            f"**Total Score:** `{score}` pts\n"
            f"**Fastest Answer:** `{time_str}`\n"
            f"**Best Question:** *{question}*"
        ),
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])
    await ctx.send(embed=embed)

# Listener function for answer
@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    global answered, answerers, current_q, round_started_at
    if (
        not any(trivia_running.values())
        or message.channel.id not in active_trivia_channel_id.values()
        or message.author.bot
        or current_q is None
        or not message.channel.permissions_for(message.author).send_messages
    ): return

    content = message.content.lower().strip()
    if any(ans == content for ans in current_q["answers"]):
        if message.author.id in answerers:
            return

        now_ms      = ((message.id >> 22) + DISCORD_EPOCH)   
        delta_ms    = now_ms - round_started_at            
        time_str    = f"{delta_ms//1000}.{delta_ms%1000:03d}s"

        answerers[message.author.id] = {
            "user":   message.author,
            "points": 2 if not answered else 1,
            "time_ms": delta_ms,
            "formatted_time": time_str
        }

        if not answered:
            answered = True
            first_correct_event.set()

# Hourly backup, on trivia_data.json
@tasks.loop(minutes=BACKUP_INTERVAL_MINUTES)
async def backup_trivia_data():
    try:
        p = pathlib.Path(TRIVIA_DATA_FILE)
        if not p.exists() or p.stat().st_size == 0:
            return
        channel = bot.get_channel(BACKUP_CHANNEL_ID)
        if channel is None:
            print("[BACKUP] backup channel not found")
            return
        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
        await channel.send(
            content=f"🗂️ **Trivia backup – UTC {ts}**",
            file=discord.File(fp=TRIVIA_DATA_FILE, filename=f"trivia_data_backup_{ts}.json"))
        print("[BACKUP] sent backup", ts)
    except Exception as e:
        print("[BACKUP] error:", e)
        
# b!backuptrivia for manual backup
@bot.command()
@commands.has_permissions(administrator=True)
async def backuptrivia(ctx):
    if ctx.channel.id != BACKUP_CHANNEL_ID:
        return await ctx.send("This command can only be used in the backup channel.")
    
    try:
        p = pathlib.Path(TRIVIA_DATA_FILE)
        if not p.exists() or p.stat().st_size == 0:
            return await ctx.send("⚠️ Trivia data file is empty or missing.")
        
        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M")
        await ctx.send(
            content=f"🗂️ **Manual Trivia Backup – UTC {ts}**",
            file=discord.File(fp=TRIVIA_DATA_FILE, filename=f"trivia_data_backup_{ts}.json"))
        print("[MANUAL BACKUP] Sent successfully")
    
    except Exception as e:
        print("[MANUAL BACKUP] Error:", e)
        await ctx.send("⚠️ Failed to send backup.")

# ─── Hit‑command assets ────────────────────────────────────────────
TEMPLATES: list[str] = []
DAMAGES:   list[str] = []
SPICA_HIT_LINES: list[str] = []

def load_spica_lines():
    path = pathlib.Path("spica_hit_lines.csv")
    if path.exists():
        with path.open(encoding='utf-8') as f:
            reader = csv.DictReader(f)
            SPICA_HIT_LINES.clear()
            SPICA_HIT_LINES.extend(row['line'] for row in reader if row.get('line'))

def _load_file(path: str) -> list[str]:
    out = []
    p = pathlib.Path(path)
    if not p.exists():
        print(f"[HIT] File not found: {p.resolve()}")
        return out
    with p.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].strip():
                out.append(row[0].strip())
    print(f"[HIT] loaded {len(out):>3} lines from {path}")
    return out

def load_hit_assets():
    """populate TEMPLATES and DAMAGES lists"""
    TEMPLATES[:] = _load_file(TEMPLATE_FILE)
    DAMAGES[:]   = _load_file(DAMAGE_FILE)

# load once at import time
load_hit_assets()

# ─── fun b!hit ------------------------------------------------------
@bot.command()
async def hit(ctx, target: discord.Member = None):
    attacker_name = ctx.author.display_name

    if target is None:
        # Hit Spica (fictional)
        if not SPICA_HIT_LINES:
            await ctx.send("⚠️ No Spica hit lines loaded.")
            return
        line = random.choice(SPICA_HIT_LINES).replace("{attacker}", attacker_name)
        embed = discord.Embed(
            title="🌠 A blow is dealt to Spica!",
            description=line,
            color=discord.Color.light_grey()
        )
    else:
        # Normal hit between users
        if not TEMPLATES or not DAMAGES:
            await ctx.send("⚠️ No hit templates or damage lines loaded.")
            return

        template = random.choice(TEMPLATES)
        damage = random.choice(DAMAGES)

        if "{damage}" in template:
            result = (template.replace("{attacker}", attacker_name).replace("{target}", target.display_name).replace("{damage}", damage.replace("{target}", target.display_name)))
        else:
            result = (template.replace("{attacker}", attacker_name).replace("{target}", target.display_name))
            result = f"{result}\n{damage.replace('{target}', target.display_name)}"

        embed = discord.Embed(
            title="💥 A hit has been landed!",
            description=result,
            color=discord.Color.red()
        )

    embed.set_footer(**get_footer_info(ctx.guild))
    await ctx.send(embed=embed)

# Help Command
@bot.command()
async def help(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    embed = discord.Embed(
        title="🌌 B1jou — Help Menu",
        description="May your journey through the stars be guided...\nHere's how you may interact with me:",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="`b!pray [@user1] [@user2] ...`",
        value=(
            "Send a prayer to Spica, yourself, or friends!\n"
            "- No mention = prayer to Spica 🌠\n"
            "- Mention up to 3 users = personalized messages\n"
            "- Mention 4+ users = group prayer!\n"
            "- Text without mention = prayer to a name"
        ),
        inline=False
    )

    embed.add_field(
        name="`b!hit [@target]`",
        value=(
            "👊 Bonk your friends (or Spica!) with random flavor text:\n"
            "- Example: `b!hit @someone`\n"
            "- No mention? It hits Spica by default\n"
            "- Uses randomized attack + damage messages!"
        ),
        inline=False
    )

    embed.add_field(
        name="`b!stats`",
        value="Shows your personal prayer count, streak, and server-wide totals 🔥",
        inline=False
    )

    embed.add_field(
        name="`b!top`",
        value="See the top 5 prayer senders in the server. Who is the most devout? 🏆",
        inline=False
    )

    embed.add_field(
        name="`b!jou [@target]`",
        value=(
            "Sends a random fun line. Defaults to Spica if no target! ✨"
        ),
        inline=False
    )

    embed.set_thumbnail(url=THUMBNAIL_URL)
    embed.set_footer(
        text="Use your prayers wisely, Dreamer...",
        icon_url=get_footer_info(ctx.guild)["icon_url"]
    )

    await ctx.send(embed=embed)


# Call loop when bot runs
@bot.event
async def on_ready():
    await bot.wait_until_ready()
    load_jou_lines()
    load_spica_lines()      
    if not backup_trivia_data.is_running():
        backup_trivia_data.start()
    print(f"[BOT] Logged in as {bot.user}")

# 🌐 Flask keep_alive() setup
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!", 200

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

# ⏳ Start webserver
keep_alive()

# 🛰️ Start bot
bot.run(os.environ['TOKEN'])