import discord
from discord.ext import commands
import os
import asyncio
from datetime import datetime, timedelta
import requests
import random
from typing import Dict, List, Set

# ========== KONFIGURACJA INTENCJI ========== #
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ========== INICJALIZACJA BOTA ========== #
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=None,  # Wyłączamy domyślną komendę pomocy
    case_insensitive=True  # Komendy nieczułe na wielkość liter
)

# ========== KONFIGURACJA DRAFTU ========== #
class DraftState:
    def __init__(self):
        self.players: List[discord.Member] = []
        self.current_index: int = 0
        self.current_round: int = 0
        self.picked_numbers: Set[int] = set()
        self.picked_players: Dict[str, List[int]] = {u.lower(): [] for u in ["Wenoid", "wordlifepl"]}
        self.user_teams: Dict[str, str] = {}
        self.players_database: Dict[int, str] = {}
        self.draft_started: bool = False
        self.team_draft_started: bool = False
        self.current_team_selector_index: int = 0

draft = DraftState()

# Stałe konfiguracyjne
TEAM_COLORS = {
    "Real Vardrit": "⚪",
    "Barcelona": "🔵",
    "AS Roma": "🟡",
    "Liverpool": "🔴"
}
PLAYERS_URL = "https://gist.githubusercontent.com/wenowinter/31a3d22985e6171b06f15061a8c3613e/raw/50121c8b83d84e626b79caee280574d8d1033826/mekambe1.txt"
SELECTION_TIME = timedelta(minutes=180)

# ========== FUNKCJE POMOCNICZE ========== #
def find_member_by_name(members: List[discord.Member], name: str) -> discord.Member:
    """Znajduje członka serwera po nazwie (case-insensitive)"""
    name_lower = name.lower()
    return next((m for m in members if m.display_name.lower() == name_lower), None)

async def load_players() -> Dict[int, str]:
    """Ładuje zawodników z zewnętrznego URL"""
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

# ========== ZARZĄDZANIE CZASEM ========== #
class TimerManager:
    def __init__(self):
        self.pick_deadline: datetime = None
        self.pick_timer_task: asyncio.Task = None
        self.reminder_tasks: List[asyncio.Task] = []

    async def schedule_reminders(self, channel, user, selection_type, deadline):
        """Planuje przypomnienia przed upływem czasu"""
        for task in self.reminder_tasks:
            task.cancel()
        
        reminders = [
            (deadline - timedelta(hours=2), "2 godziny"),
            (deadline - timedelta(hours=1), "1 godzinę")
        ]

        self.reminder_tasks = [
            asyncio.create_task(self.send_reminder(
                channel, user, selection_type, msg, (when - datetime.utcnow()).total_seconds()
            ))
            for when, msg in reminders if (when - datetime.utcnow()).total_seconds() > 0
        ]

    async def send_reminder(self, channel, user, selection_type, msg, wait_time):
        """Wysyła przypomnienie po określonym czasie"""
        await asyncio.sleep(wait_time)
        if ((selection_type == "team" and draft.team_draft_started) or 
            (selection_type == "player" and draft.draft_started)):
            await channel.send(f"⏰ PRZYPOMNIENIE: {user.mention} masz jeszcze {msg} na wybór {selection_type}!")

timer = TimerManager()

# ========== KOMENDY ========== #
@bot.event
async def on_ready():
    """Inicjalizacja bota po uruchomieniu"""
    print(f'Bot {bot.user} gotowy!')
    draft.players_database = await load_players()
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="!pomoc"
    ))

@bot.command()
async def druzyny(ctx):
    """Pokazuje dostępne drużyny z informacją o właścicielach"""
    teams_info = []
    for team, emoji in TEAM_COLORS.items():
        owner = next((u for u, t in draft.user_teams.items() if t.lower() == team.lower()), None)
        teams_info.append(f"{emoji} {team}" + (f" (wybrana przez: {owner})" if owner else ""))
    
    await ctx.send("**Dostępne drużyny:**\n" + "\n".join(teams_info))

@bot.command()
async def start(ctx):
    """Rozpoczyna proces draftu"""
    if draft.draft_started or draft.team_draft_started:
        await ctx.send("Draft już trwa!")
        return

    draft.team_draft_started = True
    draft.current_team_selector_index = 0
    draft.user_teams.clear()

    order = "\n".join(f"{i+1}. {name}" for i, name in enumerate(["Wenoid", "wordlifepl"]))
    await ctx.send(f"Rozpoczynamy wybór drużyn! Kolejność:\n{order}")
    await next_team_selection(ctx.channel)

async def next_team_selection(channel):
    """Obsługuje kolejny wybór drużyny"""
    if draft.current_team_selector_index >= len(["Wenoid", "wordlifepl"]):
        await finish_team_selection(channel)
        return

    selector_name = ["Wenoid", "wordlifepl"][draft.current_team_selector_index]
    selector = find_member_by_name(channel.guild.members, selector_name)
    
    if not selector:
        await channel.send(f"Nie znaleziono: {selector_name}")
        draft.current_team_selector_index += 1
        return await next_team_selection(channel)

    draft.pick_deadline = datetime.utcnow() + SELECTION_TIME
    available = [f"{TEAM_COLORS[t]} {t}" for t in TEAM_COLORS 
                if t.lower() not in [t.lower() for t in draft.user_teams.values()]]

    await channel.send(
        f"{selector.mention}, wybierz drużynę ({SELECTION_TIME.seconds//60} minut):\n"
        f"Dostępne: {', '.join(available)}\n"
        f"Użyj `!wybieram [nazwa]` np. `!wybieram Liverpool`"
    )

    if timer.pick_timer_task:
        timer.pick_timer_task.cancel()
    
    timer.pick_timer_task = asyncio.create_task(
        team_selection_timer(channel, selector)
    )
    await timer.schedule_reminders(channel, selector, "team", draft.pick_deadline)

async def team_selection_timer(channel, selector):
    """Obsługuje timeout wyboru drużyny"""
    await asyncio.sleep((draft.pick_deadline - datetime.utcnow()).total_seconds())
    
    if (draft.team_draft_started and 
        draft.current_team_selector_index < len(["Wenoid", "wordlifepl"]) and
        ["Wenoid", "wordlifepl"][draft.current_team_selector_index].lower() == selector.display_name.lower()):
        
        available = [t for t in TEAM_COLORS 
                    if t.lower() not in [t.lower() for t in draft.user_teams.values()]]
        
        if available:
            selected = random.choice(available)
            draft.user_teams[selector.display_name.lower()] = selected
            await channel.send(
                f"⏰ Czas minął! Przypisano {selector.mention} drużynę: {TEAM_COLORS.get(selected)} {selected}"
            )

        draft.current_team_selector_index += 1
        await next_team_selection(channel)

async def finish_team_selection(channel):
    """Finalizuje wybór drużyn i rozpoczyna draft zawodników"""
    draft.team_draft_started = False
    summary = ["**Wybieranie drużyn zakończone!**"] + [
        f"{TEAM_COLORS.get(t, '⚫')} {u}: {t}" 
        for u, t in draft.user_teams.items()
    ]
    
    await channel.send("\n".join(summary))
    await start_player_draft(channel)

async def start_player_draft(channel):
    """Rozpoczyna draft zawodników"""
    draft.players = [
        find_member_by_name(channel.guild.members, name)
        for name in ["Wenoid", "wordlifepl"]
    ]
    
    if None in draft.players:
        await channel.send("Nie znaleziono wszystkich graczy!")
        return

    draft.draft_started = True
    draft.current_index = 0
    draft.current_round = 0
    draft.picked_numbers.clear()
    draft.picked_players = {u.lower(): [] for u in ["Wenoid", "wordlifepl"]}

    await channel.send(
        "**Kolejność wyboru zawodników:**\n" +
        "\n".join(f"{i+1}. {p.display_name}" for i, p in enumerate(draft.players))
    )
    await next_pick(channel)

async def next_pick(channel):
    """Obsługuje następny wybór zawodnika"""
    if draft.current_round >= 3:  # total_rounds
        await channel.send("🏁 Draft zakończony!")
        draft.draft_started = False
        return

    if draft.current_index >= len(draft.players):
        draft.current_index = 0
        draft.current_round += 1
        if draft.current_round >= 3:  # total_rounds
            return await next_pick(channel)

    player = draft.players[draft.current_index]
    team = draft.user_teams.get(player.display_name.lower(), "Nieznana")
    
    await channel.send(
        f"{TEAM_COLORS.get(team, '⚫')} {player.mention}, wybierz "
        f"{[1, 1, 3][draft.current_round]} zawodników ({SELECTION_TIME.seconds//60} minut)!"
    )

    draft.pick_deadline = datetime.utcnow() + SELECTION_TIME
    if timer.pick_timer_task:
        timer.pick_timer_task.cancel()
    
    timer.pick_timer_task = asyncio.create_task(
        player_selection_timer(channel, player)
    )
    await timer.schedule_reminders(channel, player, "player", draft.pick_deadline)

async def player_selection_timer(channel, player):
    """Obsługuje timeout wyboru zawodnika"""
    await asyncio.sleep((draft.pick_deadline - datetime.utcnow()).total_seconds())
    
    if (draft.draft_started and 
        draft.current_index < len(draft.players) and 
        draft.players[draft.current_index] == player):
        
        await channel.send(f"⏰ Czas minął! {player.mention} nie wybrał zawodników.")
        draft.current_index += 1
        await next_pick(channel)

@bot.command()
async def wybieram(ctx, *, choice):
    """Obsługuje wybór drużyny lub zawodników"""
    if draft.team_draft_started:
        await handle_team_selection(ctx, choice)
    elif draft.draft_started:
        await handle_player_selection(ctx, choice)
    else:
        await ctx.send("Draft nie jest aktywny. Użyj !start")

async def handle_team_selection(ctx, choice):
    if draft.current_team_selector_index >= len(["Wenoid", "wordlifepl"]):
        await ctx.send("Wybór drużyn zakończony!")
        return

    selector_name = ["Wenoid", "wordlifepl"][draft.current_team_selector_index]
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
        f"{ctx.author.display_name} wybrał: {TEAM_COLORS.get(selected)} {selected}"
    )
    draft.current_team_selector_index += 1
    await next_team_selection(ctx.channel)

async def handle_player_selection(ctx, choice):
    if draft.current_index >= len(draft.players):
        return await ctx.send("Nikt teraz nie wybiera")

    if ctx.author != draft.players[draft.current_index]:
        return await ctx.send("Nie twoja kolej!")

    try:
        picks = [int(p.strip()) for p in choice.split(',')]
    except ValueError:
        return await ctx.send("Podaj numery oddzielone przecinkami")

    expected = [1, 1, 3][draft.current_round]
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
    """Wyświetla listę wybranych zawodników"""
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
        players = ", ".join(f"{p} ({draft.players_database[p]})" for p in picks)
        line = f"{TEAM_COLORS.get(team, '⚫')} **{user}** ({team}): {players}"
        
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
    """Resetuje stan draftu"""
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("❌ Tylko administrator może zresetować draft")

    draft.draft_started = False
    draft.team_draft_started = False
    draft.players.clear()
    draft.current_index = 0
    draft.current_round = 0
    draft.current_team_selector_index = 0
    draft.picked_numbers.clear()
    draft.picked_players = {u.lower(): [] for u in ["Wenoid", "wordlifepl"]}
    draft.user_teams.clear()

    if timer.pick_timer_task:
        timer.pick_timer_task.cancel()
    
    for task in timer.reminder_tasks:
        task.cancel()
    timer.reminder_tasks.clear()

    await ctx.send("Draft zresetowany.")

@bot.command()
async def czas(ctx):
    """Pokazuje pozostały czas na wybór"""
    if not (draft.draft_started or draft.team_draft_started) or not timer.pick_deadline:
        return await ctx.send("Brak aktywnych timerów")

    remaining = timer.pick_deadline - datetime.utcnow()
    if remaining.total_seconds() <= 0:
        return await ctx.send("⏰ Czas minął!")

    mins, sec = divmod(int(remaining.total_seconds()), 60)
    hours, mins = divmod(mins, 60)
    
    time_str = (
        f"{hours} godzin, {mins} minut i {sec:02d} sekund" if hours else
        f"{mins} minut i {sec:02d} sekund"
    )
    await ctx.send(f"⏳ Pozostały czas: {time_str}")

@bot.command()
async def lubicz(ctx):
    """Wyświetla obrazek Lubicz"""
    await ctx.send("https://i.ibb.co/tw1tD1Ny/412206195-1406350803614829-5742951929454962748-n-removebg-preview-1.png")

@bot.command()
async def komar(ctx):
    """Wyświetla obrazek Komar"""
    await ctx.send("https://scontent.fpoz4-1.fna.fbcdn.net/v/t39.30808-6/462362759_3871042979836522_4405035252432652447_n.jpg?_nc_cat=103&ccb=1-7&_nc_sid=6ee11a&_nc_ohc=mLtEcPyAeiwQ7kNvwEQ0kN6&_nc_oc=AdkOQC_KOMghLeoWDifpuwrjt13CvuIDYUt3Vwps1vUGakoskHkkl6xSxqYDUbkbKpE&_nc_zt=23&_nc_ht=scontent.fpoz4-1.fna&_nc_gid=OomLe8A4aLtMLUmIYtQ5_w&oh=00_AfEO44DS7ODe3W_cjKgVEW1fij8-aEJAYKl9_RP6PzHPDQ&oe=680DD11A")

@bot.command()
async def pomoc(ctx):
    """Wyświetla dostępne komendy"""
    help_msg = [
        "**📋 Lista komend:**",
        "• `!start` - Rozpoczyna draft",
        "• `!druzyny` - Pokazuje dostępne drużyny",
        "• `!wybieram [drużyna/zawodnicy]` - Wybiera drużynę lub zawodników",
        "• `!lista` - Pokazuje wybranych zawodników",
        "• `!czas` - Pokazuje pozostały czas",
        "• `!pomoc` - Ta wiadomość",
        "• `!lubicz` - Obrazek Lubicz",
        "• `!reset` - Resetuje draft (admin)"
    ]
    await ctx.send("\n".join(help_msg))

# ========== URuchomienie BOTA ========== #
if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        raise ValueError("Brak tokenu Discord w zmiennych środowiskowych!")
    
    bot.run(TOKEN)
