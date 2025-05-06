import discord
from discord.ext import commands
import os
import asyncio
from datetime import datetime, timedelta
import requests
import random
from typing import Dict, List, Set

# ========== KONFIGURACJA BOTA ========== #
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=None,
    case_insensitive=True
)

# ========== STAN DRAFTU ========== #
class DraftState:
    def __init__(self):
        self.players: List[discord.Member] = []
        self.current_index: int = 0
        self.current_round: int = 0
        self.total_rounds: int = 8  # 3 rundy po 1 zawodnika + 5 rund po 3 zawodników
        self.picked_numbers: Set[int] = set()
        self.picked_players: Dict[str, List[int]] = {}
        self.user_teams: Dict[str, str] = {}
        self.players_database: Dict[int, str] = {}
        self.draft_started: bool = False
        self.team_draft_started: bool = False
        self.current_team_selector_index: int = 0
        self.pick_deadline: datetime = None
        self.pick_timer_task = None
        self.reminder_tasks: List[asyncio.Task] = []
        self.bonus_round_started: bool = False
        self.bonus_round_players: Set[str] = set()
        self.bonus_deadline: datetime = None
        self.bonus_end_time: datetime = None  # Czas zakończenia rundy bonusowej

draft = DraftState()

# ========== STAŁE ========== #
TEAM_COLORS = {
    "Jagiellonia": ["🟡", "🔴"],
    "Legia": ["🟢", "⚪"],
    "Bayern": ["🔴", "🔵"],
    "Renopuren": ["🔵", "⚪"],
    "Liverpool": ["🔴", "⚪"],
    "Man City": ["🔵", "⚪"],
    "Man United": ["🔴", "⚫"],
    "Arsenal": ["🔴", "⚪"],
    "Celtic": ["🟢", "⚪"],
    "PSG": ["🔵", "🔴"],
    "Real Madryt": ["⚪", "🟣"],
    "Barcelona": ["🔵", "🔴"],
    "Milan": ["🔴", "⚫"],
    "Inter": ["🔵", "⚫"],
    "Juventus": ["⚪", "⚫"],
    "Slavia Praga": ["🔴", "⚪"],
    "Borussia": ["🟡", "⚫"],
    "AS Roma": ["🔴", "🟠"]
}

PLAYERS_URL = "https://gist.githubusercontent.com/wenowinter/31a3d22985e6171b06f15061a8c3613e/raw/50121c8b83d84e626b79caee280574d8d1033826/mekambe1.txt"
SELECTION_TIME = timedelta(hours=16)  # Zmieniono na 16 godzin
BONUS_SIGNUP_TIME = timedelta(hours=10)  # Zmieniono na 10 godzin
BONUS_SELECTION_TIME = timedelta(hours=10)  # Zmieniono na 10 godzin

# Lista uczestników
PARTICIPANTS = ["Karlos", "MiszczPL89", "Szwedzik", "Wenoid", "mikoprotek", "MatteyG", "ANN0D0M1N1", "flap", "WordLifePL", "Mario001", "Pogoda"]

# ========== FUNKCJE POMOCNICZE ========== #
def find_member_by_name(members: List[discord.Member], name: str) -> discord.Member:
    name_lower = name.lower()
    return next((m for m in members if m.display_name.lower() == name_lower), None)

async def load_players() -> Dict[int, str]:
    try:
        response = requests.get(PLAYERS_URL)
        response.raise_for_status()
        return {
            int(parts[0]): parts[1]
            for line in response.text.splitlines()
            if (parts := line.strip().split(maxsplit=1)) and len(parts) == 2
        }
    except Exception as e:
        print(f"Błąd ładowania zawodników: {e}")
        return {i: f"Zawodnik {i}" for i in range(1, 101)}

async def schedule_reminders(channel, user, deadline):
    for task in draft.reminder_tasks:
        task.cancel()
    
    reminders = [
        (deadline - timedelta(hours=8), "8 godzin"),
        (deadline - timedelta(hours=4), "4 godziny"), 
        (deadline - timedelta(hours=1), "1 godzinę")
    ]

    draft.reminder_tasks = [
        asyncio.create_task(
            send_reminder(channel, user, msg, (when - datetime.utcnow()).total_seconds())
        )
        for when, msg in reminders 
        if (when - datetime.utcnow()).total_seconds() > 0
    ]

async def send_reminder(channel, user, msg, wait_time):
    await asyncio.sleep(wait_time)
    if draft.draft_started:
        await channel.send(f"⏰ PRZYPOMNIENIE: {user.mention} masz jeszcze {msg} na wybór!")

# ========== KOMENDY BOTA ========== #
@bot.event
async def on_ready():
    print(f'Bot {bot.user} gotowy!')
    draft.players_database = await load_players()
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="!pomoc"
    ))

@bot.command()
async def druzyny(ctx):
    teams_info = []
    for team, colors in TEAM_COLORS.items():
        owner = next((u for u, t in draft.user_teams.items() if t.lower() == team.lower()), None)
        team_colors = "".join(colors)
        teams_info.append(f"{team_colors} {team}" + (f" (wybrana przez: {owner})" if owner else ""))
    
    await ctx.send("**Dostępne drużyny:**\n" + "\n".join(teams_info))

@bot.command()
async def start(ctx):
    # Sprawdzamy czy trwa runda bonusowa
    if draft.bonus_round_started and draft.bonus_end_time and datetime.utcnow() < draft.bonus_end_time:
        remaining = draft.bonus_end_time - datetime.utcnow()
        hours = int(remaining.total_seconds() // 3600)
        mins = int((remaining.total_seconds() % 3600) // 60)
        await ctx.send(f"Nie można rozpocząć nowego draftu - trwa runda dodatkowa (pozostało {hours}h {mins}m)")
        return

    if draft.draft_started or draft.team_draft_started:
        await ctx.send("Draft już trwa!")
        return

    draft.team_draft_started = True
    draft.current_team_selector_index = 0
    draft.user_teams.clear()

    order = "\n".join(f"{i+1}. {name}" for i, name in enumerate(PARTICIPANTS))
    await ctx.send(f"Rozpoczynamy wybór drużyn! Kolejność:\n{order}")
    await next_team_selection(ctx.channel)

async def next_team_selection(channel):
    if draft.current_team_selector_index >= len(PARTICIPANTS):
        await finish_team_selection(channel)
        return

    selector_name = PARTICIPANTS[draft.current_team_selector_index]
    selector = find_member_by_name(channel.guild.members, selector_name)
    
    if not selector:
        await channel.send(f"Nie znaleziono: {selector_name}")
        draft.current_team_selector_index += 1
        return await next_team_selection(channel)

    draft.pick_deadline = datetime.utcnow() + SELECTION_TIME
    available = [f"{''.join(TEAM_COLORS[t])} {t}" for t in TEAM_COLORS 
                if t.lower() not in [t.lower() for t in draft.user_teams.values()]]

    await channel.send(
        f"{selector.mention}, wybierz drużynę ({SELECTION_TIME.seconds//3600} godzin):\n"
        f"Dostępne drużyny - użyj `!druzyny` aby zobaczyć listę\n"
        f"Użyj `!wybieram [nazwa]` np. `!wybieram Liverpool`"
    )

    if draft.pick_timer_task:
        draft.pick_timer_task.cancel()
    
    draft.pick_timer_task = asyncio.create_task(
        team_selection_timer(channel, selector)
    )
    await schedule_reminders(channel, selector, draft.pick_deadline)

async def team_selection_timer(channel, selector):
    await asyncio.sleep((draft.pick_deadline - datetime.utcnow()).total_seconds())
    
    if (draft.team_draft_started and 
        draft.current_team_selector_index < len(PARTICIPANTS) and
        PARTICIPANTS[draft.current_team_selector_index].lower() == selector.display_name.lower()):
        
        available = [t for t in TEAM_COLORS 
                    if t.lower() not in [t.lower() for t in draft.user_teams.values()]]
        
        if available:
            selected = random.choice(available)
            draft.user_teams[selector.display_name.lower()] = selected
            await channel.send(
                f"⏰ Czas minął! Przypisano {selector.mention} drużynę: {''.join(TEAM_COLORS.get(selected, ['⚫']))} {selected}"
            )

        draft.current_team_selector_index += 1
        await next_team_selection(channel)

async def finish_team_selection(channel):
    draft.team_draft_started = False
    summary = ["**Wybieranie drużyn zakończone!**"] + [
        f"{''.join(TEAM_COLORS.get(t, ['⚫']))} {u}: {t}" 
        for u, t in draft.user_teams.items()
    ]
    
    await channel.send("\n".join(summary))
    await start_player_draft(channel)

async def start_player_draft(channel):
    draft.players = [
        find_member_by_name(channel.guild.members, name)
        for name in PARTICIPANTS
    ]
    
    if None in draft.players:
        await channel.send("Nie znaleziono wszystkich graczy!")
        return

    draft.draft_started = True
    draft.current_index = 0
    draft.current_round = 0
    draft.picked_numbers.clear()
    draft.picked_players = {u.lower(): [] for u in PARTICIPANTS}

    await channel.send(
        "**Kolejność wyboru zawodników:**\n" +
        "\n".join(f"{i+1}. {p.display_name}" for i, p in enumerate(draft.players))
    )
    await next_pick(channel)

async def next_pick(channel):
    if draft.current_round >= draft.total_rounds:
        await finish_main_draft(channel)
        return

    if draft.current_index >= len(draft.players):
        draft.current_round += 1
        draft.current_index = 0
        
        if draft.current_round >= draft.total_rounds:
            await finish_main_draft(channel)
            return
        
        if draft.current_round > 0:
            draft.players.reverse()
            await channel.send(f"🔄 **ROTACJA KOLEJNOŚCI** - Nowa runda #{draft.current_round + 1}")
            
    player = draft.players[draft.current_index]
    team = draft.user_teams.get(player.display_name.lower(), "Nieznana")
    
    picks_per_player = 1 if draft.current_round < 3 else 3
    
    await channel.send(
        f"{''.join(TEAM_COLORS.get(team, ['⚫']))} {player.mention}, wybierz "
        f"{picks_per_player} zawodników ({SELECTION_TIME.seconds//3600} godzin)!"
    )

    draft.pick_deadline = datetime.utcnow() + SELECTION_TIME
    if draft.pick_timer_task:
        draft.pick_timer_task.cancel()
    
    draft.pick_timer_task = asyncio.create_task(
        player_selection_timer(channel, player)
    )
    await schedule_reminders(channel, player, draft.pick_deadline)

async def player_selection_timer(channel, player):
    await asyncio.sleep((draft.pick_deadline - datetime.utcnow()).total_seconds())
    
    if (draft.draft_started and 
        draft.current_index < len(draft.players) and 
        draft.players[draft.current_index] == player):
        
        await channel.send(f"⏰ Czas minął! {player.mention} nie wybrał zawodnika.")
        draft.current_index += 1
        await next_pick(channel)

async def finish_main_draft(channel):
    draft.draft_started = False
    draft.bonus_round_started = True
    draft.bonus_round_players.clear()
    draft.bonus_deadline = datetime.utcnow() + BONUS_SIGNUP_TIME
    draft.bonus_end_time = datetime.utcnow() + BONUS_SIGNUP_TIME + BONUS_SELECTION_TIME  # Ustawiamy całkowity czas trwania rundy bonusowej
    
    await channel.send(
        "🏁 **Draft podstawowy zakończony!**\n\n"
        "Rozpoczyna się runda dodatkowa. Wpisz **!bonus** w ciągu następnych "
        f"**{BONUS_SIGNUP_TIME.seconds//3600} godzin**, aby wybrać dodatkowych 5 zawodników.\n"
        f"Nowy draft będzie można rozpocząć o {draft.bonus_end_time.strftime('%H:%M')}"
    )
    
    if draft.pick_timer_task:
        draft.pick_timer_task.cancel()
    
    draft.pick_timer_task = asyncio.create_task(
        bonus_registration_timer(channel)
    )

async def bonus_registration_timer(channel):
    await asyncio.sleep((draft.bonus_deadline - datetime.utcnow()).total_seconds())
    
    if draft.bonus_round_started:
        if draft.bonus_round_players:
            players_list = ", ".join([f"<@{player}>" for player in draft.bonus_round_players])
            await channel.send(
                f"⏰ Czas na rejestrację do rundy dodatkowej zakończony!\n"
                f"Zarejestrowani gracze ({len(draft.bonus_round_players)}): {players_list}\n\n"
                f"Macie **{BONUS_SELECTION_TIME.seconds//3600} godzin** na wybranie 5 dodatkowych zawodników.\n"
                f"Użyjcie `!wybieram_bonus [numery zawodników]`"
            )
        else:
            await channel.send(
                "⏰ Czas na rejestrację do rundy dodatkowej zakończony!\n"
                "Nikt nie zapisał się do rundy dodatkowej.\n\n"
                "🏆 **Draft oficjalnie zakończony!**"
            )
            draft.bonus_round_started = False
            draft.bonus_end_time = None

@bot.command()
async def bonus(ctx):
    if not draft.bonus_round_started:
        return await ctx.send("Runda dodatkowa nie jest aktywna!")
    
    if datetime.utcnow() > draft.bonus_deadline:
        return await ctx.send("Czas na rejestrację do rundy dodatkowej już minął!")
    
    user_id = str(ctx.author.id)
    if user_id in draft.bonus_round_players:
        return await ctx.send("Już jesteś zarejestrowany do rundy dodatkowej!")
    
    if ctx.author.display_name.lower() not in [p.display_name.lower() for p in draft.players]:
        return await ctx.send("Tylko uczestnicy draftu mogą zapisać się do rundy dodatkowej!")
    
    draft.bonus_round_players.add(user_id)
    remaining = (draft.bonus_deadline - datetime.utcnow()).total_seconds()
    hours, remainder = divmod(int(remaining), 3600)
    mins, secs = divmod(remainder, 60)
    
    await ctx.send(
        f"✅ {ctx.author.mention} został zarejestrowany do rundy dodatkowej!\n"
        f"Pozostały czas na rejestrację: {hours} godzin, {mins} minut i {secs} sekund.\n"
        f"Po zakończeniu rejestracji będziesz mieć {BONUS_SELECTION_TIME.seconds//3600} godzin na wybranie 5 dodatkowych zawodników."
    )

@bot.command()
async def wybieram_bonus(ctx, *, choice):
    if not draft.bonus_round_started:
        return await ctx.send("Runda dodatkowa nie jest aktywna!")
    
    user_id = str(ctx.author.id)
    if user_id not in draft.bonus_round_players:
        return await ctx.send("Nie jesteś zarejestrowany w rundzie dodatkowej! Użyj najpierw !bonus")
    
    if datetime.utcnow() <= draft.bonus_deadline:
        return await ctx.send(
            "Rejestracja do rundy dodatkowej wciąż trwa. Poczekaj na jej zakończenie aby wybrać zawodników!"
        )
    
    try:
        picks = [int(p.strip()) for p in choice.split(',')]
    except ValueError:
        return await ctx.send("Podaj numery oddzielone przecinkami")
    
    if len(picks) != 5:
        return await ctx.send("Wybierz dokładnie 5 zawodników")
    
    invalid = [p for p in picks if p not in draft.players_database]
    if invalid:
        return await ctx.send(f"Nieznani zawodnicy: {', '.join(map(str, invalid))}")
    
    duplicates = [p for p in picks if p in draft.picked_numbers]
    if duplicates:
        return await ctx.send(f"Już wybrani: {', '.join(map(str, duplicates))}")
    
    user_name = ctx.author.display_name.lower()
    draft.picked_players[user_name].extend(picks)
    draft.picked_numbers.update(picks)
    
    await ctx.send(
        f"✅ {ctx.author.display_name} wybrał dodatkowych zawodników w rundzie bonusowej: "
        f"{', '.join(f'{p} ({draft.players_database[p]})' for p in picks)}"
    )
    
    draft.bonus_round_players.remove(user_id)
    
    if not draft.bonus_round_players:
        draft.bonus_round_started = False
        draft.bonus_end_time = None
        await ctx.send("🏆 **Wszystkie wybory zostały dokonane. Draft oficjalnie zakończony!**")

@bot.command()
async def wybieram(ctx, *, choice):
    if draft.team_draft_started:
        await handle_team_selection(ctx, choice)
    elif draft.draft_started:
        await handle_player_selection(ctx, choice)
    else:
        await ctx.send("Draft nie jest aktywny. Użyj !start")

async def handle_team_selection(ctx, choice):
    if draft.current_team_selector_index >= len(PARTICIPANTS):
        await ctx.send("Wybór drużyn zakończony!")
        return

    selector_name = PARTICIPANTS[draft.current_team_selector_index]
    if ctx.author.display_name.lower() != selector_name.lower():
        await ctx.send("Nie twoja kolej!")
        return

    selected = next((t for t in TEAM_COLORS if t.lower() == choice.lower()), None)
    if not selected:
        return await ctx.send("Nie ma takiej drużyny! Użyj !druzyny")

    if selected.lower() in [t.lower() for t in draft.user_teams.values()]:
        return await ctx.send("Drużyna już wybrana!")

    draft.user_teams[ctx.author.display_name.lower()] = selected
    await ctx.send(
        f"{ctx.author.display_name} wybrał: {''.join(TEAM_COLORS.get(selected, ['⚫']))} {selected}"
    )
    draft.current_team_selector_index += 1
    await next_team_selection(ctx.channel)

async def handle_player_selection(ctx, choice):
    if not draft.draft_started or draft.current_index >= len(draft.players):
        return await ctx.send("Nikt teraz nie wybiera")

    current_player = draft.players[draft.current_index]
    if ctx.author != current_player:
        return await ctx.send(f"Nie twoja kolej! Teraz wybiera {current_player.mention}")

    try:
        picks = [int(p.strip()) for p in choice.split(',')]
    except ValueError:
        return await ctx.send("Podaj numery oddzielone przecinkami")

    expected = 1 if draft.current_round < 3 else 3
    
    if len(picks) != expected:
        return await ctx.send(f"Wybierz dokładnie {expected} zawodników")

    invalid = [p for p in picks if p not in draft.players_database]
    if invalid:
        return await ctx.send(f"Nieznani zawodnicy: {', '.join(map(str, invalid))}")

    duplicates = [p for p in picks if p in draft.picked_numbers]
    if duplicates:
        return await ctx.send(f"Już wybrani: {', '.join(map(str, duplicates))}")

    draft.picked_players[ctx.author.display_name.lower()].extend(picks)
    draft.picked_numbers.update(picks)
    
    await ctx.send(
        f"{ctx.author.display_name} wybrał: {', '.join(f'{p} ({draft.players_database[p]})' for p in picks)}"
    )
    draft.current_index += 1
    await next_pick(ctx.channel)

@bot.command()
async def lista(ctx):
    if not draft.players_database:
        return await ctx.send("❌ Błąd: brak danych zawodników")

    if all(not p for p in draft.picked_players.values()):
        return await ctx.send("Nikt jeszcze nie wybrał zawodników")

    chunks = []
    current_chunk = ["**Wybrani zawodnicy:**"]
    
    for user, picks in draft.picked_players.items():
        if not picks:
            continue
            
        team = draft.user_teams.get(user, "Nieznana")
        players = ", ".join(f"{p} ({draft.players_database[p]})" for p in sorted(picks))
        team_colors = "".join(TEAM_COLORS.get(team, ['⚫']))
        line = f"{team_colors} **{user}** ({team}): {players}"
        
        if len("\n".join(current_chunk + [line])) > 1900:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for i, chunk in enumerate(chunks):
        await ctx.send(chunk + (f"\n(Część {i+1}/{len(chunks)})" if len(chunks) > 1 else ""))

@bot.command()
async def reset(ctx):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("❌ Tylko administrator może zresetować draft")

    draft.draft_started = False
    draft.team_draft_started = False
    draft.bonus_round_started = False
    draft.players.clear()
    draft.current_index = 0
    draft.current_round = 0
    draft.current_team_selector_index = 0
    draft.picked_numbers.clear()
    draft.picked_players = {u.lower(): [] for u in PARTICIPANTS}
    draft.user_teams.clear()
    draft.bonus_round_players.clear()
    draft.bonus_end_time = None

    if draft.pick_timer_task:
        draft.pick_timer_task.cancel()
    
    for task in draft.reminder_tasks:
        task.cancel()
    draft.reminder_tasks.clear()

    await ctx.send("Draft zresetowany.")

@bot.command()
async def czas(ctx):
    if draft.bonus_round_started:
        if datetime.utcnow() > draft.bonus_deadline and draft.bonus_end_time:
            remaining = draft.bonus_end_time - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                mins, sec = divmod(remainder, 60)
                await ctx.send(f"⏳ Pozostały czas na wybór w rundzie dodatkowej: {hours} godzin, {mins} minut i {sec:02d} sekund")
                return
            else:
                await ctx.send("⏰ Runda dodatkowa zakończona!")
                return
        
        if draft.bonus_deadline:
            remaining = draft.bonus_deadline - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                mins, sec = divmod(remainder, 60)
                await ctx.send(f"⏳ Pozostały czas na rejestrację do rundy dodatkowej: {hours} godzin, {mins} minut i {sec:02d} sekund")
                return
    
    if not (draft.draft_started or draft.team_draft_started) or not draft.pick_deadline:
        return await ctx.send("Brak aktywnych timerów")

    remaining = draft.pick_deadline - datetime.utcnow()
    if remaining.total_seconds() <= 0:
        return await ctx.send("⏰ Czas minął!")

    hours, remainder = divmod(int(remaining.total_seconds()), 3600)
    mins, sec = divmod(remainder, 60)
    
    time_str = f"{hours} godzin, {mins} minut i {sec:02d} sekund"
    await ctx.send(f"⏳ Pozostały czas: {time_str}")

@bot.command()
async def bonusstatus(ctx):
    """Pokazuje status rundy dodatkowej"""
    if draft.bonus_round_started:
        if datetime.utcnow() > draft.bonus_deadline and draft.bonus_end_time:
            remaining = draft.bonus_end_time - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                mins, sec = divmod(remainder, 60)
                await ctx.send(f"⏳ Runda dodatkowa - czas na wybór: {hours} godzin, {mins} minut i {sec:02d} sekund")
            else:
                await ctx.send("⏰ Runda dodatkowa zakończona!")
        elif draft.bonus_deadline:
            remaining = draft.bonus_deadline - datetime.utcnow()
            if remaining.total_seconds() > 0:
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                mins, sec = divmod(remainder, 60)
                await ctx.send(f"⏳ Runda dodatkowa - czas na rejestrację: {hours} godzin, {mins} minut i {sec:02d} sekund")
            else:
                await ctx.send("🔄 Runda dodatkowa - czas na wybór zawodników")
    else:
        await ctx.send("ℹ️ Brak aktywnej rundy dodatkowej")

@bot.command()
async def lubicz(ctx):
    await ctx.send("https://i.ibb.co/tw1tD1Ny/412206195-1406350803614829-5742951929454962748-n-removebg-preview-1.png")

@bot.command()
async def komar(ctx):
    await ctx.send("https://i.ibb.co/zT3813dG/1746106198604.jpg")

# ... (Twój istniejący kod pozostaje DOKŁADNIE taki sam aż do komendy !pomoc)

@bot.command()
async def pomoc(ctx):
    help_msg = [
        "**📋 Lista komend:**",
        "• `!start` - Rozpoczyna draft (zablokowane podczas rundy dodatkowej)",
        "• `!bonus` - Zapisuje Cię do rundy dodatkowej",
        "• `!bonusstatus` - Pokazuje status rundy dodatkowej",
        "• `!druzyny` - Pokazuje dostępne drużyny",
        "• `!wybieram [drużyna/zawodnicy]` - Wybiera drużynę lub zawodników",
        "• `!wybieram_bonus [zawodnicy]` - Wybiera dodatkowych zawodników",
        "• `!lista` - Pokazuje wybranych zawodników",
        "• `!czas` - Pokazuje pozostały czas",
        "• `!pomoc` - Ta wiadomość",
        "• `!lubicz` - Obrazek Lubicz",
        "• `!komar` - Obrazek Komar",
        "• `!reset` - Resetuje draft (admin)",
        "• `!napraw_karlosa [nowy_nick]` - Naprawia pierwszego gracza (admin)"  # DODANE
    ]
    await ctx.send("\n".join(help_msg))

# ===== NOWA KOMENDA ===== #
@bot.command(name='napraw_karlosa')
@commands.has_permissions(administrator=True)
async def napraw_karlosa(ctx, nowy_nick: str):
    """Zamienia starego Karlosa (pierwszego gracza) na nowego"""
    if not draft.players:
        return await ctx.send("❌ Lista graczy jest pusta!")
    
    nowy_karlos = find_member_by_name(ctx.guild.members, nowy_nick)
    if not nowy_karlos:
        return await ctx.send(f"❌ Nie znaleziono gracza o nicku '{nowy_nick}'!")
    
    stary_karlos = draft.players[0]
    draft.players[0] = nowy_karlos
    
    stary_nick = stary_karlos.display_name.lower()
    if stary_nick in draft.user_teams:
        draft.user_teams[nowy_karlos.display_name.lower()] = draft.user_teams.pop(stary_nick)
    
    await ctx.send(
        f"✅ Pomyślnie zamieniono {stary_karlos.display_name} na {nowy_karlos.display_name}!\n"
        f"Nowy Karlos przejmuje:"
        f"\n- Kolejność w drafcie"
        f"\n- Przypisaną drużynę (jeśli była)"
    )

# ========== URUCHOMIENIE BOTA ========== #
if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        raise ValueError("Brak tokenu Discord w zmiennych środowiskowych!")
    bot.run(TOKEN)
