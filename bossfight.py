# NOTE: This file will try to import safe_load_data / safe_save_data /
# _lock_channel / normalize_text from b1jou. If not present, simple fallbacks
# are used (but you should prefer the ones in b1jou so file locking & storage stays consistent).

import discord
from discord.ext import commands
import asyncio
import random
import unicodedata
import json
import pathlib
from datetime import datetime

# ---------------------------
# Attempt to reuse helpers from b1jou.py (your main)
# If those functions are not exported, fall back to local implementations.
# ---------------------------
try:
    from b1jou import safe_load_data, safe_save_data, _lock_channel, normalize_text
except Exception:
    # Fallbacks (rudimentary) - replace with your b1jou versions if available
    FILE_LOCK = asyncio.Lock()
    TRIVIA_DATA_FILE = "trivia_data.json"

    async def safe_load_data() -> dict:
        async with FILE_LOCK:
            p = pathlib.Path(TRIVIA_DATA_FILE)
            if not p.exists() or p.stat().st_size == 0:
                return {}
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}

    async def safe_save_data(data: dict):
        async with FILE_LOCK:
            tmp = pathlib.Path(TRIVIA_DATA_FILE + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(TRIVIA_DATA_FILE)

    async def _lock_channel(chan: discord.TextChannel, *, allow_send: bool):
        ow = chan.overwrites_for(chan.guild.default_role)
        ow.send_messages = allow_send
        await chan.set_permissions(chan.guild.default_role, overwrite=ow)

    def normalize_text(txt: str) -> str:
        return unicodedata.normalize("NFKC", txt).replace("’", "'").lower().strip()

# ---------------------------
# Config (tweak as needed)
# ---------------------------
BOSS_START_HP = 10000
PLAYER_START_HP = 100
TURN_TIME = 5  # seconds
HIT_DAMAGE_RANGE = (80, 180)         # per "hit" per player per turn
CRIT_HIT_DAMAGE_RANGE = (400, 900)   # critical hit damage
CRIT_HIT_CHANCE = 0.45               # chance to land critical (on attempt)
SPEEDRUN_TRIVIA_QUESTIONS = 10
SOLO_TRIVIA_TAG_COUNT = 10
TYPING_ROUNDS = 30

# Storage key for points
POINTS_KEY = "boss_points"   # structure: { "user_id": points }

# Placeholder custom questions & words (you said you'll provide later)
PLACEHOLDER_SPEEDRUN = [
    {"q": "What color is the sky on a clear day?", "answers": ["blue"]},
    {"q": "2 + 2 = ?", "answers": ["4", "four"]},
    {"q": "What's the first month of the year?", "answers": ["january"]},
    {"q": "What animal barks?", "answers": ["dog"]},
    {"q": "Name a primary color.", "answers": ["red", "blue", "yellow"]},
    {"q": "Opposite of 'up'?", "answers": ["down"]},
    {"q": "Water freezes at 0 degrees on which scale?", "answers": ["celsius"]},
    {"q": "Sun rises in the ____.", "answers": ["east"]},
    {"q": "What do bees produce?", "answers": ["honey"]},
    {"q": "Which planet is known as the red planet?", "answers": ["mars"]},
]
PLACEHOLDER_TYPING_WORDS = [f"word{i}" for i in range(1, TYPING_ROUNDS + 1)]

# ---------------------------
# Internal state
# ---------------------------
_state = {
    "active": False,
    "boss_hp": BOSS_START_HP,
    "phase": 1,                # 1..5
    "players": {},             # user_id -> {"hp": int, "phase_death": None or phase}
    "turn_hits": set(),        # user_ids who hit this turn
    "boss_channel_id": None,
    "event_lock": False,       # prevents new turns when event running
    "one_time_done": {
        "speedrun": False,
        "solo": False,
        "typing": False,
    },
    "solo_tagged": [],         # list of tagged user_ids (for solo trivia)
    "final_mode": False,
}

# ---------------------------
# Utility helpers
# ---------------------------
def get_alive_players():
    return {uid: p for uid, p in _state["players"].items() if p["hp"] > 0}

def user_mention(bot, uid):
    m = bot.get_user(uid)
    return m.mention if m else f"<@{uid}>"

async def award_points(bot, winners_map):
    """
    winners_map: dict user_id -> points_to_add
    Saves points into persistent storage via safe_load_data / safe_save_data.
    Returns the saved snapshot for those users.
    """
    data = await safe_load_data()
    if not isinstance(data, dict):
        data = {}
    points = data.get(POINTS_KEY, {})
    if not isinstance(points, dict):
        points = {}

    changed = {}
    for uid, add in winners_map.items():
        sid = str(uid)
        old = int(points.get(sid, 0))
        new = old + int(add)
        points[sid] = new
        changed[uid] = {"old": old, "new": new}

    data[POINTS_KEY] = points
    await safe_save_data(data)
    return changed

def embed_simple(title, desc=None, color=0xFF8800):
    e = discord.Embed(title=title, description=desc or "", color=color)
    e.timestamp = datetime.utcnow()
    return e

# ---------------------------
# Core logic: commands & loops
# ---------------------------
async def start_bossfight(ctx):
    if _state["active"]:
        return await ctx.send("A bossfight is already active in another channel.")
    _state["active"] = True
    _state["boss_hp"] = BOSS_START_HP
    _state["phase"] = 1
    _state["players"].clear()
    _state["turn_hits"].clear()
    _state["boss_channel_id"] = ctx.channel.id
    _state["event_lock"] = False
    _state["one_time_done"] = {"speedrun": False, "solo": False, "typing": False}
    _state["solo_tagged"] = []
    _state["final_mode"] = False

    await ctx.send(embed=embed_simple("🔥 Bossfight Started!",
        "Register with `!bossjoin`. Each registrant gets 100 HP.\nType `hit` during turns to attack."))
    # Small delay then start the turn loop
    await asyncio.sleep(2)
    # spawn background task so command returns immediately
    asyncio.create_task(turn_loop(ctx.bot, ctx.channel))

async def join_bossfight(ctx):
    if not _state["active"] or ctx.channel.id != _state["boss_channel_id"]:
        return await ctx.send("No active bossfight in this channel.")
    uid = ctx.author.id
    if uid in _state["players"]:
        return await ctx.send("You're already registered for this bossfight.")
    _state["players"][uid] = {"hp": PLAYER_START_HP, "phase_death": None}
    return await ctx.send(embed=embed_simple("✅ Registered",
        f"{ctx.author.mention} joined the bossfight with {PLAYER_START_HP} HP."))

async def turn_loop(bot, channel: discord.TextChannel):
    """
    Main loop for turns. Runs until boss dead or fight canceled.
    """
    while _state["active"] and _state["boss_hp"] > 0:
        if _state["event_lock"]:
            await asyncio.sleep(1)
            continue

        if len(get_alive_players()) == 0:
            # if no players alive/registered, end fight
            await channel.send(embed=embed_simple("Fight ended", "No players remain — bossfight ended."))
            _state["active"] = False
            return

        _state["turn_hits"].clear()
        await channel.send(embed=embed_simple(f"Turn — Boss HP: {_state['boss_hp']}",
            f"Type `hit` (once) within the next {TURN_TIME} seconds to attack!"))

        # wait TURN_TIME seconds to collect hits
        await asyncio.sleep(TURN_TIME)

        # resolve hits
        total_damage = 0
        hits_count = 0
        for uid in list(_state["turn_hits"]):
            if uid in get_alive_players():
                dmg = random.randint(*HIT_DAMAGE_RANGE)
                total_damage += dmg
                hits_count += 1

        if hits_count:
            _state["boss_hp"] = max(0, _state["boss_hp"] - total_damage)
            await channel.send(embed=embed_simple("💥 Hits Resolved",
                f"{hits_count} players hit the boss this turn for a total of {total_damage} damage.\nBoss HP: {_state['boss_hp']}"))
        else:
            await channel.send("No hits this turn!")

        # If boss is alive, boss may attack after turn (we'll do a simple mechanic: small AoE)
        if _state["boss_hp"] > 0:
            # small boss attack that scales with number of remaining players
            alive = get_alive_players()
            if alive:
                # boss does a light retaliatory attack: 10-30 damage randomly to all alive
                retaliation = random.randint(10, 30)
                for uid in list(alive.keys()):
                    _state["players"][uid]["hp"] -= retaliation
                    if _state["players"][uid]["hp"] <= 0 and _state["players"][uid]["phase_death"] is None:
                        _state["players"][uid]["phase_death"] = _state["phase"]
                await channel.send(f"⚔️ Boss retaliates for {retaliation} damage to everyone still alive.")

        # check phase transitions and trigger events (one-time each)
        # Phase transitions: <=7500 -> speedrun, <=5000 -> solo, <=2500 -> typing, <=500 -> final
        if _state["phase"] == 1 and _state["boss_hp"] <= 7500 and not _state["one_time_done"]["speedrun"]:
            _state["phase"] = 2
            asyncio.create_task(event_speedrun_trivia(bot, channel))
        elif _state["phase"] == 2 and _state["boss_hp"] <= 5000 and not _state["one_time_done"]["solo"]:
            _state["phase"] = 3
            asyncio.create_task(event_solo_trivia(bot, channel))
        elif _state["phase"] == 3 and _state["boss_hp"] <= 2500 and not _state["one_time_done"]["typing"]:
            _state["phase"] = 4
            asyncio.create_task(event_typing_challenge(bot, channel))
        elif _state["phase"] == 4 and _state["boss_hp"] <= 500 and not _state["final_mode"]:
            _state["phase"] = 5
            _state["final_mode"] = True
            asyncio.create_task(event_final_phase(bot, channel))

        # small loop pause
        await asyncio.sleep(1)

    # boss dead or fight ended
    if _state["boss_hp"] <= 0:
        await finish_bossfight(bot, channel)

async def event_speedrun_trivia(bot, channel):
    """
    Trigger a speedrun trivia event with custom (hardcoded/placeholders) questions.
    Players answer normally; more correct answers = more boss damage.
    If nobody answers correctly at all, boss deals a critical AoE to all registered players.
    """
    _state["event_lock"] = True
    _state["one_time_done"]["speedrun"] = True
    await channel.send(embed=embed_simple("⚡ Speedrun Trivia Event!", 
        f"{SPEEDRUN_TRIVIA_QUESTIONS} questions — fastest correct answers reduce the boss HP.\nAnswer in-channel normally."))
    # load questions (placeholder list). You will replace these with your real list.
    pool = list(PLACEHOLDER_SPEEDRUN)
    random.shuffle(pool)
    asked = pool[:SPEEDRUN_TRIVIA_QUESTIONS]
    correct_counts = 0

    def check_answer(m):
        if m.author.bot or m.channel.id != channel.id:
            return False
        # compare normalized message to question answers (we'll check current question when iterating)
        return True

    # For speedrun, we'll ask sequentially similar to your speedrun system: first correct gets points
    for qobj in asked:
        q = qobj["q"]
        answers = [normalize_text(a) for a in qobj["answers"]]
        await channel.send(embed=embed_simple("Question", q))
        accepted = None
        try:
            msg = await bot.wait_for("message", timeout=8.0, check=lambda m: (not m.author.bot) and m.channel.id == channel.id and normalize_text(m.content) in answers)
            accepted = msg
        except asyncio.TimeoutError:
            accepted = None

        if accepted:
            correct_counts += 1
            # damage scales with how many people answered so far (we keep it simple: fixed damage per correct)
            await channel.send(f"✅ {accepted.author.mention} answered correctly!")
        else:
            await channel.send("No correct answers for that question.")

    # After questions, decide damage
    if correct_counts > 0:
        # total damage: each correct -> random 200..500
        total = sum(random.randint(200, 500) for _ in range(correct_counts))
        _state["boss_hp"] = max(0, _state["boss_hp"] - total)
        await channel.send(embed=embed_simple("💥 Speedrun Result", f"{correct_counts} correct answers reduced the boss for {total} HP!\nBoss HP: {_state['boss_hp']}"))
    else:
        # nobody answered => boss does critical full-damage to all registered players
        await channel.send(embed=embed_simple("❌ No correct answers", "Boss enrages and does a critical attack to all registered players!"))
        for uid in list(_state["players"].keys()):
            if _state["players"][uid]["hp"] > 0:
                dmg = random.randint(400, 800)
                _state["players"][uid]["hp"] -= dmg
                if _state["players"][uid]["hp"] <= 0 and _state["players"][uid]["phase_death"] is None:
                    _state["players"][uid]["phase_death"] = _state["phase"]

    _state["event_lock"] = False

async def event_solo_trivia(bot, channel):
    """
    Solo trivia: boss tags a registered player (random) and only that player can answer for the round.
    Repeat until SOLO_TRIVIA_TAG_COUNT players have been tagged (unique).
    If tagged player fails to answer in 10s, they take critical damage.
    Channel should be locked for others during each solo question (we use _lock_channel if present).
    """
    _state["event_lock"] = True
    _state["one_time_done"]["solo"] = True
    available = [uid for uid in _state["players"].keys() if _state["players"][uid]["hp"] > 0]
    if len(available) == 0:
        await channel.send("No available players for solo trivia.")
        _state["event_lock"] = False
        return

    await channel.send(embed=embed_simple("🎯 Solo Trivia", f"Boss will tag {SOLO_TRIVIA_TAG_COUNT} players for solo questions. Only the tagged player may answer."))

    pool = list(PLACEHOLDER_SPEEDRUN)  # reuse placeholder Qs, shuffle
    random.shuffle(pool)
    tags_done = 0
    used_tagged = set()

    while tags_done < SOLO_TRIVIA_TAG_COUNT and available:
        # choose a random alive player not yet tagged (if possible)
        candidate = random.choice([uid for uid in available if uid not in used_tagged]) if len([u for u in available if u not in used_tagged])>0 else random.choice(available)
        used_tagged.add(candidate)
        tags_done += 1
        _state["solo_tagged"].append(candidate)
        user = bot.get_user(candidate)
        await channel.send(f"🔔 {user.mention} has been tagged for a solo question. Only they may answer for 10 seconds.")

        # temporarily lock channel for non-tagged users (deny send_messages)
        try:
            await _lock_channel(channel, allow_send=False)
        except Exception:
            # permission issues may happen; ignore
            pass

        # Ask a question
        qobj = pool[(tags_done - 1) % len(pool)]
        answers = [normalize_text(a) for a in qobj["answers"]]
        await channel.send(embed=embed_simple("Solo Question", qobj["q"]))

        def solo_check(m):
            return (not m.author.bot) and m.channel.id == channel.id and m.author.id == candidate

        answered_ok = False
        try:
            msg = await bot.wait_for("message", timeout=10.0, check=solo_check)
            if normalize_text(msg.content) in answers:
                answered_ok = True
        except asyncio.TimeoutError:
            answered_ok = False

        # unlock channel afterwards
        try:
            await _lock_channel(channel, allow_send=True)
        except Exception:
            pass

        if answered_ok:
            # reward: reduce boss HP by a significant amount
            dmg = random.randint(800, 1400)
            _state["boss_hp"] = max(0, _state["boss_hp"] - dmg)
            await channel.send(embed=embed_simple("✅ Correct!", f"{user.mention} answered correctly and dealt {dmg} damage!\nBoss HP: {_state['boss_hp']}"))
        else:
            # critical damage to that player
            dmg = random.randint(800, 1500)
            _state["players"][candidate]["hp"] -= dmg
            if _state["players"][candidate]["hp"] <= 0 and _state["players"][candidate]["phase_death"] is None:
                _state["players"][candidate]["phase_death"] = _state["phase"]
            await channel.send(embed=embed_simple("❌ Failed", f"{user.mention} failed to answer and took {dmg} critical damage."))

        # update available (filter out dead)
        available = [uid for uid in _state["players"].keys() if _state["players"][uid]["hp"] > 0]

    _state["event_lock"] = False

async def event_typing_challenge(bot, channel):
    """
    Typing challenge: boss provides words (case-insensitive) for TYPING_ROUNDS rounds.
    Players must type the exact word (case-insensitive) to avoid critical damage.
    Those that fail / AFK take critical damage.
    """
    _state["event_lock"] = True
    _state["one_time_done"]["typing"] = True
    await channel.send(embed=embed_simple("⌨️ Typing Challenge", f"{TYPING_ROUNDS} rounds — type the word displayed!"))

    rounds_words = PLACEHOLDER_TYPING_WORDS[:TYPING_ROUNDS]
    random.shuffle(rounds_words)

    for word in rounds_words:
        if len(get_alive_players()) == 0:
            break
        await channel.send(embed=embed_simple("Type this:", word))
        # collect responses for 6 seconds
        try:
            # gather messages in that channel for TURN_TIME seconds
            collected = []

            def check(m):
                if m.author.bot or m.channel.id != channel.id:
                    return False
                return True

            # wait for TURN_TIME seconds collecting
            end_at = asyncio.get_event_loop().time() + 6.0
            while True:
                timeout = max(0.0, end_at - asyncio.get_event_loop().time())
                if timeout <= 0:
                    break
                try:
                    m = await bot.wait_for("message", timeout=timeout, check=check)
                    collected.append(m)
                except asyncio.TimeoutError:
                    break

            # determine who typed correct
            correct_users = set()
            target = normalize_text(word)
            for m in collected:
                if normalize_text(m.content) == target:
                    if m.author.id in get_alive_players():
                        correct_users.add(m.author.id)

            # anyone who did NOT type correct takes critical damage
            for uid in list(get_alive_players().keys()):
                if uid not in correct_users:
                    dmg = random.randint(400, 900)
                    _state["players"][uid]["hp"] -= dmg
                    if _state["players"][uid]["hp"] <= 0 and _state["players"][uid]["phase_death"] is None:
                        _state["players"][uid]["phase_death"] = _state["phase"]
            await channel.send(f"Round complete — {len(correct_users)} players typed the word correctly.")
        except Exception as ex:
            # safety
            print("Typing challenge exception:", ex)

        await asyncio.sleep(1)

    _state["event_lock"] = False

async def event_final_phase(bot, channel):
    """
    Final phase: boss alternates between typing rounds and speedrun trivia (randomly)
    until boss dies (or fight ends). Boss is aggressive and deals stronger retaliations.
    """
    _state["event_lock"] = True
    await channel.send(embed=embed_simple("💀 Final Phase", "Boss is enraged — alternating typing & trivia events until death!"))

    # mini-loop until boss dies or all players dead
    while _state["boss_hp"] > 0 and len(get_alive_players()) > 0:
        choice = random.choice(["typing", "speedrun"])
        if choice == "typing":
            # single quick typing round
            word = random.choice(PLACEHOLDER_TYPING_WORDS)
            await channel.send(embed=embed_simple("Final Typing", word))

            try:
                collected = []

                def check(m):
                    if m.author.bot or m.channel.id != channel.id:
                        return False
                    return True

                # collect for 5 seconds
                end_at = asyncio.get_event_loop().time() + 5.0
                while True:
                    timeout = max(0.0, end_at - asyncio.get_event_loop().time())
                    if timeout <= 0:
                        break
                    try:
                        m = await bot.wait_for("message", timeout=timeout, check=check)
                        collected.append(m)
                    except asyncio.TimeoutError:
                        break

                target = normalize_text(word)
                correct_users = set()
                for m in collected:
                    if normalize_text(m.content) == target and m.author.id in get_alive_players():
                        correct_users.add(m.author.id)

                # correct users damage boss slightly
                dmg = sum(random.randint(200, 400) for _ in correct_users)
                if dmg > 0:
                    _state["boss_hp"] = max(0, _state["boss_hp"] - dmg)
                    await channel.send(f"Final typing: {len(correct_users)} players hit the boss for {dmg} damage. Boss HP: {_state['boss_hp']}")
                else:
                    # nobody correct -> boss critical to everyone
                    for uid in list(get_alive_players().keys()):
                        dd = random.randint(600, 1200)
                        _state["players"][uid]["hp"] -= dd
                        if _state["players"][uid]["hp"] <= 0 and _state["players"][uid]["phase_death"] is None:
                            _state["players"][uid]["phase_death"] = _state["phase"]
                    await channel.send("No correct answers — boss lands a massive attack on everyone!")

            except Exception as ex:
                print("final typing exception:", ex)

        else:  # speedrun mini (short)
            # ask a quick question from placeholder list
            qobj = random.choice(PLACEHOLDER_SPEEDRUN)
            answers = [normalize_text(a) for a in qobj["answers"]]
            await channel.send(embed=embed_simple("Final Trivia", qobj["q"]))
            try:
                msg = await bot.wait_for("message", timeout=6.0, check=lambda m: (not m.author.bot) and m.channel.id == channel.id and normalize_text(m.content) in answers)
                # first correct deals heavy damage
                dmg = random.randint(600, 1200)
                _state["boss_hp"] = max(0, _state["boss_hp"] - dmg)
                await channel.send(f"✅ {msg.author.mention} got it and dealt {dmg} damage! Boss HP: {_state['boss_hp']}")
            except asyncio.TimeoutError:
                # nobody answered -> boss hits everyone
                for uid in list(get_alive_players().keys()):
                    dd = random.randint(700, 1300)
                    _state["players"][uid]["hp"] -= dd
                    if _state["players"][uid]["hp"] <= 0 and _state["players"][uid]["phase_death"] is None:
                        _state["players"][uid]["phase_death"] = _state["phase"]
                await channel.send("No correct answers — boss slams everyone with force!")

        # small pause between final events
        await asyncio.sleep(1)

    _state["event_lock"] = False
    # If boss died here, finish_bossfight in turn_loop will handle awarding. Otherwise, if all players died, end fight
    if len(get_alive_players()) == 0 and _state["boss_hp"] > 0:
        await channel.send(embed=embed_simple("Fight Over", "All players have fallen. Boss remains victorious."))
        # proceed to finish to award points accordingly
        await finish_bossfight(bot, channel)

# ---------------------------
# Message listener: handle "hit" and "critical hit"
# ---------------------------
async def on_message_listener(msg: discord.Message):
    if msg.author.bot:
        return
    if not _state["active"] or msg.channel.id != _state["boss_channel_id"]:
        return
    content = normalize_text(msg.content)

    # only accept hits if not in an event_lock (unless critical allowed in phase>=4)
    if content == "hit":
        if _state["event_lock"]:
            return  # hits disabled during events
        uid = msg.author.id
        if uid not in get_alive_players():
            return
        # only one hit per turn
        if uid in _state["turn_hits"]:
            return
        _state["turn_hits"].add(uid)
        await msg.add_reaction("⚔️")

    elif content == "critical hit":
        uid = msg.author.id
        if uid not in get_alive_players():
            return
        if _state["phase"] < 4:
            await msg.channel.send(f"{msg.author.mention}, critical hits are only available in Phase 4+.")
            return
        # attempt critical
        if random.random() <= CRIT_HIT_CHANCE:
            dmg = random.randint(*CRIT_HIT_DAMAGE_RANGE)
            _state["boss_hp"] = max(0, _state["boss_hp"] - dmg)
            await msg.channel.send(embed=embed_simple("💥 Critical!", f"{msg.author.mention} landed a critical hit for {dmg} damage! Boss HP: {_state['boss_hp']}"))
        else:
            await msg.channel.send(embed=embed_simple("❌ Missed", f"{msg.author.mention}'s critical hit missed!"))

# ---------------------------
# Finish logic and awarding points
# ---------------------------
async def finish_bossfight(bot, channel):
    """
    Called when boss HP <= 0 or when fight ends. Awards points based on survival/phase death.
    - Survived to end (alive when boss died): 10k points
    - Died in phase 1 (before 7500): 7.5k
    - Died in phase 2 (7500->2500): 5k
    - Died in phase 3+ (<=2500): 2.5k
    """
    # Determine results
    winners_map = {}  # uid -> points to award
    survivors = []
    died_map = {}  # uid -> phase_death (int)
    for uid, pdata in _state["players"].items():
        if pdata["hp"] > 0:
            survivors.append(uid)
            winners_map[uid] = 10000
        else:
            # phase_death may be None if they died to retaliation after join; treat conservatively
            pd = pdata.get("phase_death") or 1
            died_map[uid] = pd
            if pd == 1:
                winners_map[uid] = 7500
            elif pd == 2:
                winners_map[uid] = 5000
            else:
                winners_map[uid] = 2500

    # save awards
    changed = await award_points(bot, winners_map)

    # Build embed summary
    embed = discord.Embed(title="🏆 Bossfight Results", color=0x00FF88)
    embed.add_field(name="Boss HP", value=str(_state["boss_hp"]), inline=False)
    if survivors:
        embed.add_field(name="Survivors", value=", ".join(user_mention(bot, uid) for uid in survivors), inline=False)
    if died_map:
        died_lines = []
        for uid, phase_dead in died_map.items():
            died_lines.append(f"{user_mention(bot, uid)} — died at phase {phase_dead}")
        embed.add_field(name="Fallen", value="\n".join(died_lines), inline=False)

    # show point changes
    pc_lines = []
    for uid, info in changed.items():
        pc_lines.append(f"{user_mention(bot, uid)}: {info['old']} → {info['new']}")
    if pc_lines:
        embed.add_field(name="Points Awarded", value="\n".join(pc_lines), inline=False)

    await channel.send(embed=embed)

    # reset state
    _state["active"] = False
    _state["boss_channel_id"] = None
    _state["event_lock"] = False

# ---------------------------
# Setup function to be called by b1jou.py
# ---------------------------
def setup(bot: commands.Bot):
    @bot.command(name="bossstart")
    async def _bossstart(ctx):
        await start_bossfight(ctx)

    @bot.command(name="bossjoin")
    async def _bossjoin(ctx):
        await join_bossfight(ctx)

    # optionally let an admin show current state
    @bot.command(name="bossstatus")
    async def _bossstatus(ctx):
        if not _state["active"]:
            return await ctx.send("No active bossfight.")
        lines = [
            f"Boss HP: {_state['boss_hp']}",
            f"Phase: {_state['phase']}",
            f"Registered: {len(_state['players'])}",
        ]
        e = embed_simple("Boss Status", "\n".join(lines))
        await ctx.send(embed=e)

    # register listener
    @bot.listen("on_message")
    async def _on_message(msg: discord.Message):
        await on_message_listener(msg)

    # expose a small helper to allow manual cancellation if needed
    @bot.command(name="bosscancel")
    @commands.has_permissions(manage_guild=True)
    async def _bosscancel(ctx):
        if not _state["active"]:
            return await ctx.send("No active bossfight.")
        _state["active"] = False
        await ctx.send("Bossfight cancelled by an admin.")