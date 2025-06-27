import discord
from discord.ext import commands
from discord import ui, Interaction
import os, json, random, csv, time, asyncio
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread

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
DATA_FILE = 'data.json'
TRIVIA_CSV = 'trivia_sheet.csv'
TRIVIA_DATA_FILE = 'trivia_data.json'
TRIVIA_CHANNEL_ID = 1387653760175706172  
#############################

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="b!", intents=intents)
bot.remove_command('help')

# State
trivia_list = []
trivia_task = None
trivia_running = False
current_q = None
answerers = {}
round_started_at = 0
answered = False

# JSON data helper
def ensure_data():
    if not os.path.exists(DATA_FILE) or os.path.getsize(DATA_FILE) == 0:
        with open(DATA_FILE, 'w') as f:
            json.dump({'scores': {}}, f)

def load_data():
    ensure_data()
    return json.load(open(DATA_FILE))

def save_data(d):
    json.dump(d, open(DATA_FILE, 'w'), indent=2)
    
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

@bot.command()
async def pray(ctx, *args):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    data = load_data()
    guild_id = str(ctx.guild.id) if ctx.guild else "DM"
    user_id = str(ctx.author.id)
    today = datetime.utcnow().date()

    if guild_id not in data['guilds']:
        data['guilds'][guild_id] = {'global': 0, 'users': {}}

    guild_data = data['guilds'][guild_id]
    is_new_user = user_id not in guild_data['users']
    user_data = guild_data['users'].get(user_id, {})
    user_data.setdefault("count", 0)
    user_data.setdefault("streak", 1 if is_new_user else 0)
    user_data.setdefault("last_prayed", None)

    last_prayed_str = user_data.get("last_prayed")
    last_prayed_date = datetime.strptime(last_prayed_str, "%Y-%m-%d").date() if last_prayed_str else None

    continued_streak = False
    reset_streak = False
    is_first_pray = last_prayed_date is None
    is_spica_pray = len(args) == 0

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

    user_data["count"] += 1
    user_data["last_prayed"] = str(today)
    guild_data['global'] += 1
    guild_data['users'][user_id] = user_data
    data['guilds'][guild_id] = guild_data
    save_data(data)

    streak = user_data["streak"]
    mentions = [m for m in ctx.message.mentions]

    embed = discord.Embed(color=discord.Color.purple())
    embed.set_author(name="Prayers of the Wishful")
    embed.set_thumbnail(url=THUMBNAIL_URL)
    embed.set_image(url=IMAGE_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])

    quote = random.choice(PRAYER_QUOTES)

    # 💖 Prayed to the bot itself
    if len(mentions) == 1 and mentions[0].id == bot.user.id:
        embed.title = "A prayer is sent... to me!?"
        embed.description = f"R-really? you would pray for me? thank you!! ///\n\n> {quote}"

    elif is_spica_pray:
        embed.title = "A prayer is sent to Spica!"
        description = (
            f"**{ctx.author.display_name}** has prayed for **Spica the Dreamer!**\n"
            f"Her journey towards the throne of Procyon shall succeed!\n"
        )
        if continued_streak:
            description += f"🔥 **Daily Streak:** `{streak}` days! Keep praying for the Dreamer! 🔥\n"
        elif reset_streak:
            description += "😢 Your daily streak was broken. Let's start again today!\n"
        description += f"\n> {quote}"
        embed.description = description

    elif mentions:
        if len(mentions) == 1:
            embed.title = f"A prayer is sent to {mentions[0].display_name}!"
            embed.description = f"**{ctx.author.display_name}** has prayed for **{mentions[0].display_name}**! How sweet!\n\n> {quote}"
        elif len(mentions) == 2:
            embed.title = f"Prayers are sent to {mentions[0].display_name} and {mentions[1].display_name}!"
            embed.description = f"**{ctx.author.display_name}** prays for their friends, **{mentions[0].display_name}** and **{mentions[1].display_name}**! How caring!\n\n> {quote}"
        elif len(mentions) == 3:
            embed.title = f"Lots of prayers for **{mentions[0].display_name}**, **{mentions[1].display_name}**, and **{mentions[2].display_name}**!\n\n> {quote}"
            embed.description = (
                f"Wow! It seems like **{ctx.author.display_name}** has a lot of friends!\n"
                f"Such a kind soul!\n\n> {quote}"
            )
        else:
            embed.title = "🌌 Prayers are sent to everyone!"
            embed.description = (
                f"Lots of prayers are sent to everybody!\n"
                f"**{ctx.author.display_name}** loves everyone so much they're willing to send many!\n\n> {quote}"
            )
    else:
        text_target = " ".join(args)
        embed.title = "A prayer is sent to somebody!"
        embed.description = (
            f"**{ctx.author.display_name}** sends a prayer for **{text_target}**!\n"
            f"Whoever they are, they sure have a lovely friend praying for them even when they're separated!\n\n> {quote}"
        )

    await ctx.send(embed=embed)

@bot.command()
async def stats(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    data = load_data()
    guild_id = str(ctx.guild.id)
    user_id = str(ctx.author.id)
    guild_data = data["guilds"].get(guild_id, {"global": 0, "users": {}})
    user_data = guild_data["users"].get(user_id, {"count": 0, "streak": 0})

    embed = discord.Embed(
        title="📊 Prayer Stats",
        description=(
            f"{ctx.author.mention}, here are your stats:\n\n"
            f"**Your total prayers:** `{user_data['count']}`\n"
            f"🔥 **Current streak:** `{user_data['streak']}`\n"
            f"🌌 **Global prayers:** `{guild_data['global']}`"
        ),
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=THUMBNAIL_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])
    await ctx.send(embed=embed)

@bot.command()
async def top(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in ALLOWED_CHANNELS:
        return

    data = load_data()
    guild_id = str(ctx.guild.id)
    guild_data = data["guilds"].get(guild_id, {"users": {}})
    top_users = sorted(guild_data['users'].items(), key=lambda x: x[1]['count'], reverse=True)[:5]

    desc = ""
    for i, (uid, stats) in enumerate(top_users, start=1):
        user = await bot.fetch_user(int(uid))
        desc += f"**{i}.** {user.name} — `{stats['count']}` prayers (🔥 {stats['streak']}d streak)\n"

    embed = discord.Embed(
        title="🏆 Top Prayers",
        description=desc if desc else "No prayers yet!",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=THUMBNAIL_URL)
    footer_info = get_footer_info(ctx.guild)
    embed.set_footer(text=footer_info['text'], icon_url=footer_info['icon_url'])
    await ctx.send(embed=embed)

@bot.command()
async def jou(ctx):
    if ctx.guild and (ctx.guild.id, ctx.channel.id) not in DEBUG_CHANNELS:
        return

    before = time.monotonic()
    msg = await ctx.send("B1jou is calculating...")
    ping = (time.monotonic() - before) * 1000
    await msg.edit(content=f"🏓 Pong! Latency: `{int(ping)}ms`")

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
        
### ---------TRIVIA------------ ###

def load_trivia():
    global trivia_list
    trivia_list.clear()
    with open(TRIVIA_CSV, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            answers = [a.strip().lower() for a in row['answers'].split('|')]
            trivia_list.append({"q": row['question'], "answers": answers})
    random.shuffle(trivia_list)
    print(f"[DEBUG] Loaded {len(trivia_list)} trivia questions.")

def load_trivia_data():
    if not os.path.exists(TRIVIA_DATA_FILE):
        with open(TRIVIA_DATA_FILE, 'w') as f:
            json.dump({}, f)
    with open(TRIVIA_DATA_FILE, 'r') as f:
        return json.load(f)

def save_trivia_data(data):
    with open(TRIVIA_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

async def trivia_loop(channel):
    print("[DEBUG] Entered trivia_loop")
    global current_q, answerers, round_started_at, answered, trivia_running
    data = load_trivia_data()

    while trivia_running:
        current_q = trivia_list.pop(0)
        trivia_list.append(current_q)
        answerers.clear()
        answered = False

        embed = discord.Embed(
            title="🌌 Spica's Trivia Challenge",
            description=f"{current_q['q']}",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=THUMBNAIL_URL)
        embed.set_footer(text="Answer within 4 minutes 30 seconds or the stars move on...")
        print(f"[DEBUG] Asking question: {current_q['q']}")
        await channel.send(embed=embed)
        print("[DEBUG] Sent trivia question, sleeping for 4.5 minutes...")

        round_started_at = time.monotonic()
        await asyncio.sleep(270)  # 4.5 minutes

        if not answered:
            await channel.send(embed=discord.Embed(
                title="⏱️ Time's Up!",
                description="Nobody got it right... Maybe next time, Dreamers.",
                color=discord.Color.dark_grey()
            ))
            await asyncio.sleep(30)
            continue

        await asyncio.sleep(5)

        results = sorted(answerers.values(), key=lambda x: x['time'])
        leaderboard = []
        for res in results:
            user_id = str(res['user'].id)
            if user_id not in data:
                data[user_id] = 0
            data[user_id] += res['points']
            leaderboard.append(f"{res['user'].display_name} — `{res['points']}pt` ({int(res['time'] * 1000)}ms)")

        save_trivia_data(data)

        embed = discord.Embed(
            title="📜 Round Results",
            description="\n".join(leaderboard),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=THUMBNAIL_URL)
        await channel.send(embed=embed)

        elapsed = time.monotonic() - round_started_at
        await asyncio.sleep(max(0, 300 - elapsed))

@bot.command()
async def starttrivia(ctx):
    global trivia_task, trivia_running
    if ctx.channel.id != TRIVIA_CHANNEL_ID:
        return
    if trivia_running:
        await ctx.send("Trivia is already running!")
        return
    load_trivia()
    trivia_running = True
    await ctx.send("🌠 Trivia has begun! May the stars guide your knowledge!")
    trivia_task = asyncio.create_task(trivia_loop(ctx.channel))
    print("[DEBUG] Trivia task started:", trivia_task)

@bot.command()
async def stoptrivia(ctx):
    global trivia_task, trivia_running
    if ctx.channel.id != TRIVIA_CHANNEL_ID:
        return
    if not trivia_running:
        await ctx.send("Trivia isn't running.")
        return
    trivia_running = False
    if trivia_task:
        trivia_task.cancel()
    await ctx.send("🌌 Trivia session ended. Until next time, Dreamers.")

@bot.command()
async def triviatop(ctx):
    data = load_trivia_data()
    if not data:
        await ctx.send("Nobody has scored yet!")
        return
    sorted_scores = sorted(data.items(), key=lambda x: x[1], reverse=True)[:5]
    desc = ""
    for i, (uid, pts) in enumerate(sorted_scores, start=1):
        user = await bot.fetch_user(int(uid))
        desc += f"**{i}.** {user.display_name} — `{pts}` points\n"
    embed = discord.Embed(
        title="🌟 Trivia Leaderboard",
        description=desc,
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=THUMBNAIL_URL)
    await ctx.send(embed=embed)

@bot.event
async def on_message(message):
    await bot.process_commands(message)
    global current_q, answered, answerers, round_started_at
    if message.author.bot or message.channel.id != TRIVIA_CHANNEL_ID or not current_q:
        return
    content = message.content.lower()
    if any(ans in content for ans in current_q["answers"]):
        if message.author.id in answerers:
            return
        now = time.monotonic()
        elapsed = now - round_started_at
        points = 2 if not answered else 1
        answerers[message.author.id] = {
            "user": message.author,
            "points": points,
            "time": elapsed
        }
        if not answered:
            answered = True

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