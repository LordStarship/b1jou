import discord
from discord.ext import commands, tasks
from discord import ui, Interaction
import os, json, random, csv, time, asyncio, pathlib
from datetime import datetime, timedelta
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
TRIVIA_CSV = 'trivia_sheet.csv'             # trivia question file
TRIVIA_DATA_FILE = 'trivia_data.json'       # trivia data file
TRIVIA_CHANNEL_ID = 1387653760175706172     # channel to send trivia
QUIZ_LENGTH_SEC      = 270                  # 4m 30 s players can answer
POST_ANSWER_WINDOW   = 5                    # window that stays open after 1st correct
INTER_ROUND_COOLDOWN = 300                  # total cycle time = 5 min
PRE_ANNOUNCE_SEC     = 5                    # “Trivia in 5 seconds!” heads‑up
BACKUP_CHANNEL_ID = 1389077962116038848     # channel to receive backup
BACKUP_INTERVAL_MINUTES = 60                # backup every 1 hour
#############################

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

# Pray command
@bot.command()
async def pray(ctx, *args):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    guild_id = str(ctx.guild.id) if ctx.guild else "DM"
    user_id = str(ctx.author.id)
    today = datetime.utcnow().date()

    # Firestore fetch
    user_data = await load_user_data(guild_id, user_id)
    guild_ref = db.collection('guilds').document(guild_id)
    guild_doc = guild_ref.get()
    guild_data = guild_doc.to_dict() if guild_doc.exists else {"global": 0}

    last_prayed_str = user_data.get("last_prayed")
    last_prayed_date = datetime.strptime(last_prayed_str, "%Y-%m-%d").date() if last_prayed_str else None

    is_first_pray = last_prayed_date is None
    is_spica_pray = len(args) == 0
    continued_streak = False
    reset_streak = False

    if is_spica_pray:
        if last_prayed_date == today:
            pass  # already prayed today
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
    db.collection('guilds').document(guild_id).collection('leaderboard').document(user_id).set({
        "user_id": user_id,
        "count": user_data["count"],
        "streak": user_data["streak"]
    }, merge=True)

    # Build embed
    streak = user_data["streak"]
    mentions = [m for m in ctx.message.mentions]
    quote = random.choice(PRAYER_QUOTES)

    embed = discord.Embed(color=discord.Color.purple())
    embed.set_author(name="Prayers of the Wishful")
    embed.set_thumbnail(url=THUMBNAIL_URL)
    embed.set_image(url=IMAGE_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])

    # Responses
    if len(mentions) == 1 and mentions[0].id == bot.user.id:
        embed.title = "⁉️ A prayer is sent... to me!?"
        embed.description = f"R-really? you would pray for me? thank you!! ///\n\n> {quote}"
    elif is_spica_pray:
        embed.title = "🙏 A prayer is sent to Spica!"
        embed.description = f"**{ctx.author.display_name}** has prayed for **Spica the Dreamer!**\nHer journey toward the throne of Procyon shall succeed!\n"
        if continued_streak:
            embed.description += f"🔥 **Daily Streak:** `{streak}` days! Keep praying for the Dreamer! 🔥\n"
        elif reset_streak:
            embed.description += "😢 Your daily streak was broken. Let's start again today!\n"
        embed.description += f"\n> {quote}"
    elif mentions:
        if len(mentions) == 1:
            embed.title = f"💫 A prayer is sent to {mentions[0].display_name}!"
            embed.description = f"**{ctx.author.display_name}** has prayed for **{mentions[0].display_name}**! How sweet!\n\n> {quote}"
        elif len(mentions) == 2:
            embed.title = f"✨ Prayers are sent to {mentions[0].display_name} and {mentions[1].display_name}!"
            embed.description = f"**{ctx.author.display_name}** prays for their friends, **{mentions[0].display_name}** and **{mentions[1].display_name}**! How caring!\n\n> {quote}"
        elif len(mentions) == 3:
            embed.title = f"🌟 Lots of prayers for {mentions[0].display_name}, {mentions[1].display_name}, and {mentions[2].display_name}!"
            embed.description = f"Wow! It seems like **{ctx.author.display_name}** has a lot of friends!\nSuch a kind soul!\n\n> {quote}"
        else:
            embed.title = "🌌 Prayers are sent to everyone!"
            embed.description = f"Lots of prayers are sent to everybody!\n**{ctx.author.display_name}** loves everyone so much they're willing to send many!\n\n> {quote}"
    else:
        text_target = " ".join(args)
        embed.title = "⭐ A prayer is sent to somebody!"
        embed.description = f"**{ctx.author.display_name}** sends a prayer for **{text_target}**!\nWhoever they are, they have a lovely friend praying for them!\n\n> {quote}"

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

# PING COMMMAND
@bot.command()
async def jou(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in DEBUG_CHANNELS:
        return

    before = time.monotonic()
    msg = await ctx.send("B1jou is calculating...")
    ping = (time.monotonic() - before) * 1000
    await msg.edit(content=f"🏓 Pong! Latency: `{int(ping)}ms`")

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
    async def assign_role(self, interaction: Interaction, button: ui.Button):
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
    async def assign_role(self, interaction: Interaction, button: ui.Button):
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
trivia_list = []
trivia_task = None
trivia_running = False
current_q = None
answerers = {}
round_started_at = 0
answered = False

async def _lock_channel(chan: discord.TextChannel, *, allow_send: bool):
    overwrites = chan.overwrites_for(chan.guild.default_role)
    overwrites.send_messages = allow_send
    await chan.set_permissions(chan.guild.default_role, overwrite=overwrites)

def load_trivia():
    global trivia_list
    trivia_list.clear()
    path = pathlib.Path(TRIVIA_CSV)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path.resolve()}")
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            answers = [a.strip().lower() for a in row['answers'].split('|') if a.strip()]
            if row['question'] and answers:
                trivia_list.append({"q": row['question'], "answers": answers})
    if not trivia_list:
        raise RuntimeError("No trivia questions were loaded – check your CSV.")
    random.shuffle(trivia_list)
    print(f"[DEBUG] Loaded {len(trivia_list)} trivia questions.")

def load_trivia_data():
    path = pathlib.Path(TRIVIA_DATA_FILE)
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        print("[TRIVIA] Corrupted JSON – starting with empty data.")
        return {}

def save_trivia_data(data):
    tmp = pathlib.Path(TRIVIA_DATA_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(TRIVIA_DATA_FILE)
    
async def trivia_loop(channel: discord.TextChannel):
    global current_q, answerers, round_started_at, answered, trivia_running
    data = load_trivia_data()

    try:
        while trivia_running:
            # 1) pick & announce a question  ────────────────────────────────
            current_q = trivia_list.pop(0)
            trivia_list.append(current_q)
            answerers.clear()
            answered = False

            # open the channel for answers
            await _lock_channel(channel, allow_send=True)

            embed = discord.Embed(
                title="🌌 Spica's Trivia Challenge",
                description=current_q["q"],
                color=discord.Color.purple()
            ).set_thumbnail(url=THUMBNAIL_URL
            ).set_footer(text="You have 4 min 30 s — first correct gets 2 pts, everyone else 1 pt!")

            await channel.send(embed=embed)

            round_started_at = time.monotonic()

            # 2) wait until either window expires or no correct answer came  ─
            await asyncio.sleep(QUIZ_LENGTH_SEC)

            # if nobody answered correctly, announce & skip scoring
            if not answered:
                await channel.send(
                    embed=discord.Embed(
                        title="⏱️ Time’s Up!",
                        description="Nobody got it right… maybe next time, Dreamers.",
                        color=discord.Color.dark_grey()
                    )
                )
            else:
                # keep channel open POST_ANSWER_WINDOW secs after 1st correct
                await asyncio.sleep(POST_ANSWER_WINDOW)

                # compile & store results
                results = sorted(answerers.values(), key=lambda r: r['time'])
                leaderboard_lines = []
                for res in results:
                    uid = str(res['user'].id)
                    data[uid] = data.get(uid, 0) + res['points']
                    leaderboard_lines.append(
                        f"{res['user'].display_name} — `{res['points']} pt` ({int(res['time']*1000)} ms)"
                    )
                save_trivia_data(data)

                await channel.send(
                    embed=discord.Embed(
                        title="📜 Round Results",
                        description="\n".join(leaderboard_lines),
                        color=discord.Color.gold()
                    ).set_thumbnail(url=THUMBNAIL_URL)
                )

            # 3) lock channel & wait until next round ───────────────────────
            await _lock_channel(channel, allow_send=False)

            # announce upcoming round 5 s before restart
            elapsed = time.monotonic() - round_started_at
            wait_remaining = max(0, INTER_ROUND_COOLDOWN - elapsed - PRE_ANNOUNCE_SEC)
            await asyncio.sleep(wait_remaining)

            await channel.send("✨ Trivia resumes in **5 seconds**…")
            await asyncio.sleep(PRE_ANNOUNCE_SEC)

    finally:
        # make sure the channel is re‑opened if loop is cancelled
        await _lock_channel(channel, allow_send=True)
        trivia_running = False
        current_q = None


# ── start / stop / top commands (tiny changes) ───────────────────────────────
@bot.command()
async def starttrivia(ctx):
    global trivia_task, trivia_running
    if ctx.channel.id != TRIVIA_CHANNEL_ID:  # hard‑coded channel guard
        return
    if trivia_running:
        await ctx.send("Trivia is already running!")
        return
    load_trivia()
    trivia_running = True
    await ctx.send("🌠 Trivia has begun! May the stars guide your knowledge!")
    trivia_task = asyncio.create_task(trivia_loop(ctx.channel))

@bot.command()
async def stoptrivia(ctx):
    global trivia_task, trivia_running
    if ctx.channel.id != TRIVIA_CHANNEL_ID:
        return
    if not trivia_running:
        await ctx.send("Trivia isn’t running.")
        return
    trivia_running = False
    trivia_task.cancel()
    await ctx.send("🌌 Trivia session ended. Until next time, Dreamers.")

@bot.command()
async def triviatop(ctx):
    data = load_trivia_data()
    if not data:
        await ctx.send("Nobody has scored yet!")
        return

    top5 = sorted(data.items(), key=lambda t: t[1], reverse=True)[:5]
    lines = []
    for i, (uid, pts) in enumerate(top5, 1):
        user = await bot.fetch_user(int(uid))
        lines.append(f"**{i}.** {user.display_name} — `{pts}` pts")

    embed = (discord.Embed(title="🌟 Trivia Leaderboard",
                           description="\n".join(lines),
                           color=discord.Color.blue())
             .set_thumbnail(url=THUMBNAIL_URL))
    await ctx.send(embed=embed)


# ── answer listener (only small tweak: we stop accepting answers once channel locked) ──
@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    global answered, answerers, current_q, round_started_at  

    if (
        message.channel.id != TRIVIA_CHANNEL_ID
        or message.author.bot
        or current_q is None
        or not message.channel.permissions_for(message.author).send_messages
    ):
        return

    content = message.content.lower().strip()
    if any(ans == content for ans in current_q["answers"]):
        if message.author.id in answerers:
            return  # already counted
        timestamp = time.monotonic()
        answerers[message.author.id] = {
            "user": message.author,
            "points": 2 if not answered else 1,
            "time": timestamp - round_started_at
        }
        if not answered:
            answered = True

# Backup Trivia Data 
@tasks.loop(minutes=BACKUP_INTERVAL_MINUTES)
async def backup_trivia_data():
    try:
        with open(TRIVIA_DATA_FILE, "rb") as f:
            content = f.read()
            if not content:
                print("[BACKUP] trivia_data.json is empty, skipping.")
                return

        filename = f"trivia_data_backup_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M')}.json"
        channel = bot.get_channel(BACKUP_CHANNEL_ID)
        if not channel:
            print("[BACKUP] Backup channel not found.")
            return

        file = discord.File(fp=TRIVIA_DATA_FILE, filename=filename)
        await channel.send(
            content=f"🗂️ **Backup at UTC {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}**",
            file=file
        )
        print(f"[BACKUP] Sent backup: {filename}")

    except Exception as e:
        print(f"[BACKUP] Failed to send backup: {e}")

@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user}")
    backup_trivia_data.start()

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
        name="`b!jou`",
        value="(Debug) Ping test for latency, usable only in bot debug channels 🛠️",
        inline=False
    )

    embed.add_field(
        name="`Auto Member Join`",
        value=(
            "I will automatically greet newcomers in the welcome channel.\n"
            "Admins can take action via buttons: `Assign Member`, `Kick`, or `Ban`."
        ),
        inline=False
    )

    embed.set_thumbnail(url=THUMBNAIL_URL)
    embed.set_footer(
        text="Use your prayers wisely, Dreamer...",
        icon_url=get_footer_info(ctx.guild)["icon_url"]
    )

    await ctx.send(embed=embed)

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