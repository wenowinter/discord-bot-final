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
        self.total_rounds: int = 8
        self.picked_numbers: Set[int] = set()
        self.user_teams: Dict[str, str] = {
            "ann0d0m1n1": "Ajax",            # ⚪🔴
            "FakenSzit": "Real Madryt",      # ⚪🟣
            "TommyAlex": "Juventus",         # ⚪⚫
            "pogoda": "Ossasuna"             # 🔴🟣
            "wenoid": "Galatasaray",         # 🟡🔴
            "wordlifepl": "Celtic",          # ⚪🟢
            "mikoprotek": "Inter",           # 🔵⚫
        }
        self.picked_players: Dict[str, List[int]] = {name.lower(): [] for name in ["wenoid", "wordlifepl"]}  # INITIALIZED
        self.players_database: Dict[int, str] = {}
        self.draft_started: bool = False
        self.team_draft_started: bool = True  # POMIJAMY WYBÓR DRUŻYN
        self.current_team_selector_index: int = 0
        self.pick_deadline: datetime = None
        self.pick_timer_task = None
        self.reminder_tasks: List[asyncio.Task] = []
        self.bonus_round_started: bool = False
        self.bonus_round_players: Set[str] = set()
        self.bonus_deadline: datetime = None
        self.bonus_end_time: datetime = None

draft = DraftState()

# ========== STAŁE ========== #
TEAM_COLORS = {
    "Galatasaray": ["🟡", "🔴"],
    "Ossasuna": ["🔴", "🟣"],
    "Bayern": ["🔴", "🔵"],
    "Renopuren": ["🔵", "⚪"],
    "Liverpool": ["🔴", "⚪"],
    "Man City": ["🔵", "⚪"],
    "Man United": ["🔴", "⚫"],
    "Ajax": ["🔴", "⚪"],
    "Celtic": ["🟢", "⚪"],
    "PSG": ["🔵", "🔴"],
    "Real Madryt": ["⚪", "🟣"],
    "Barcelona": ["🔵", "🔴"],
    "Milan": ["🔴", "⚫"],
    "Inter": ["🔵", "⚫"],
    "Juventus": ["⚪", "⚫"],
    "Slavia Praga": ["🔴", "⚪"],
    "Borussia": ["🟡", "⚫"],
    "AS Roma": ["🔴", "🟠"],
}

PLAYERS_URL = "https://gist.githubusercontent.com/wenowinter/c3151d1a3e34ec235176fccb91a6b107/raw/54daa05bd11b065cb52e8274961269f5efc52191/majklab.txt"
SELECTION_TIME = timedelta(hours=16)
BONUS_SIGNUP_TIME = timedelta(hours=10)
BONUS_SELECTION_TIME = timedelta(hours=10)
PARTICIPANTS = list(draft.user_teams.keys())  # Używa przypisanych graczy

# ========== FUNKCJE POMOCNICZE ========== #
def find_member_by_name(members: List[discord.Member], name: str) -> discord.Member:
    name_lower = name.lower()
    # Najpierw szukaj dokładnego dopasowania
    for m in members:
        if name_lower == m.display_name.lower() or name_lower == m.name.lower():
            return m
    # Potem częściowe
    for m in members:
        if name_lower in m.display_name.lower() or name_lower in m.name.lower():
            return m
    return None

async def load_players() -> Dict[int, str]:
    try:
        response = requests.get(PLAYERS_URL)
        response.raise_for_status()
        players_dict = {}
        for line in response.text.splitlines():
            if line.strip():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    try:
                        player_id = int(parts[0])
                        players_dict[player_id] = parts[1]
                    except ValueError:
                        continue
        return players_dict
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
    """Rozpoczyna draft od razu od wyboru zawodników (pomija wybór drużyn)"""
    if draft.bonus_round_started and draft.bonus_end_time and datetime.utcnow() < draft.bonus_end_time:
        remaining = draft.bonus_end_time - datetime.utcnow()
        hours = int(remaining.total_seconds() // 3600)
        mins = int((remaining.total_seconds() % 3600) // 60)
        await ctx.send(f"Nie można rozpocząć nowego draftu - trwa runda dodatkowa (pozostało {hours}h {mins}m)")
        return

    if draft.draft_started:
        await ctx.send("Draft już trwa!")
        return

    # Pomiń wybór drużyn - od razu zaczynamy draft zawodników
    draft.draft_started = True
    draft.players = [
        find_member_by_name(ctx.guild.members, name)
        for name in PARTICIPANTS
    ]
    
    if None in draft.players:
        missing = [name for name, member in zip(PARTICIPANTS, draft.players) if member is None]
        await ctx.send(f"❌ Nie znaleziono graczy: {', '.join(missing)}")
        draft.draft_started = False
        return

    await ctx.send(
        "🏁 **Rozpoczynamy draft zawodników!**\n"
        "**Przypisane drużyny:**\n" +
        "\n".join([f"- {name}: {team}" for name, team in draft.user_teams.items()]) +
        "\n\n**Kolejność wyboru:**\n" +
        "\n".join(f"{i+1}. {p.display_name}" for i, p in enumerate(draft.players))
    )
    await next_pick(ctx.channel)

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
    draft.bonus_end_time = datetime.utcnow() + BONUS_SIGNUP_TIME + BONUS_SELECTION_TIME
    
    await channel.send(
        "🏁 **Draft podstawowy zakończony!**\n\n"
        "Rozpoczyna się runda dodatkowa. Wpisz **!bonus** w ciągu następnych "
        f"**{BONUS_SIGNUP_TIME.seconds//3600} godzin**, aby wybrać dodatkowych 5 zawodników."
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
            # Uruchom timer dla wyboru w rundzie bonusowej
            draft.pick_timer_task = asyncio.create_task(
                bonus_selection_timer(channel)
            )
        else:
            await channel.send(
                "⏰ Czas na rejestrację do rundy dodatkowej zakończony!\n"
                "Nikt nie zapisał się do rundy dodatkowej.\n\n"
                "🏆 **Draft oficjalnie zakończony!**"
            )
            draft.bonus_round_started = False

async def bonus_selection_timer(channel):
    await asyncio.sleep((draft.bonus_end_time - datetime.utcnow()).total_seconds())
    
    if draft.bonus_round_started:
        # Zakończ rundę bonusową jeśli czas minął
        draft.bonus_round_started = False
        await channel.send("⏰ Czas na wybór w rundzie dodatkowej zakończony!")

@bot.command()
async def bonus(ctx):
    if not draft.bonus_round_started:
        return await ctx.send("Runda dodatkowa nie jest aktywna!")
    
    if datetime.utcnow() > draft.bonus_deadline:
        return await ctx.send("Czas na rejestrację do rundy dodatkowej już minął!")
    
    user_id = str(ctx.author.id)
    if user_id in draft.bonus_round_players:
        return await ctx.send("Już jesteś zarejestrowany do rundy dodatkowej!")
    
    participant_names = [p.display_name.lower() for p in draft.players]
    if ctx.author.display_name.lower() not in participant_names:
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
    
    # Sprawdź czy są duplikaty w obecnym wyborze
    if len(picks) != len(set(picks)):
        return await ctx.send("❌ Nie możesz wybrać tego samego zawodnika więcej niż raz w tej samej turze!")
    
    invalid = [p for p in picks if p not in draft.players_database]
    if invalid:
        return await ctx.send(f"Nieznani zawodnicy: {', '.join(map(str, invalid))}")
    
    duplicates = [p for p in picks if p in draft.picked_numbers]
    if duplicates:
        return await ctx.send(f"Już wybrani: {', '.join(map(str, duplicates))}")
    
    user_name = ctx.author.display_name.lower()
    if user_name not in draft.picked_players:
        draft.picked_players[user_name] = []
    
    draft.picked_players[user_name].extend(picks)
    draft.picked_numbers.update(picks)
    
    await ctx.send(
        f"✅ {ctx.author.display_name} wybrał dodatkowych zawodników w rundzie bonusowej: "
        f"{', '.join(f'{p} ({draft.players_database[p]})' for p in picks)}"
    )
    
    draft.bonus_round_players.remove(user_id)
    
    if not draft.bonus_round_players:
        draft.bonus_round_started = False
        await ctx.send("🏆 **Wszystkie wybory zostały dokonane. Draft oficjalnie zakończony!**")

@bot.command()
async def wybieram(ctx, *, choice):
    if draft.draft_started:
        await handle_player_selection(ctx, choice)
    else:
        await ctx.send("Draft nie jest aktywny. Użyj !start")

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
    
    # Sprawdź czy są duplikaty w obecnym wyborze
    if len(picks) != len(set(picks)):
        return await ctx.send("❌ Nie możesz wybrać tego samego zawodnika więcej niż raz w tej samej turze!")

    invalid = [p for p in picks if p not in draft.players_database]
    if invalid:
        return await ctx.send(f"Nieznani zawodnicy: {', '.join(map(str, invalid))}")

    duplicates = [p for p in picks if p in draft.picked_numbers]
    if duplicates:
        return await ctx.send(f"Już wybrani: {', '.join(map(str, duplicates))}")

    user_name = ctx.author.display_name.lower()
    if user_name not in draft.picked_players:
        draft.picked_players[user_name] = []
    
    draft.picked_players[user_name].extend(picks)
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
    draft.team_draft_started = True
    draft.bonus_round_started = False
    draft.players.clear()
    draft.current_index = 0
    draft.current_round = 0
    draft.current_team_selector_index = 0
    draft.picked_numbers.clear()
    draft.picked_players = {name.lower(): [] for name in PARTICIPANTS}  # RESET
    draft.bonus_round_players.clear()
    draft.bonus_end_time = None

    if draft.pick_timer_task:
        draft.pick_timer_task.cancel()
    
    for task in draft.reminder_tasks:
        task.cancel()
    draft.reminder_tasks.clear()

    await ctx.send("Draft zresetowany. Użyj !start, aby rozpocząć nowy draft.")

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
    await ctx.send("https://i.ibb.co/tw1tD1Ny/412206195_1406350803614829-5742951929454962748-n-removebg-preview-1.png")

@bot.command()
async def komar(ctx):
    await ctx.send("https://i.ibb.co/zT3813dG/1746106198604.jpg")

@bot.command()
async def papa(ctx):
    await ctx.send("https://wykop.pl/cdn/c3201142/comment_1632743224LPCEeyBmCmXNxbUkJK3s6n,w400.gif")

@bot.command()
async def paei100(ctx):
    await ctx.send("https://i.ibb.co/JRXhrkmx/Comment-Ql-Hxr-Fps-Ot-LG2-XTGSe-BCsn-Gk2d-Yim-ATE.jpg")
                   
@bot.command()
async def boniek(ctx):
    await ctx.send("https://cdn.laczynaspilka.pl/cms2/prod/sites/default/files/styles/bpp_large/public/2021-02/bonio.png")

@bot.command()
async def eusebio(ctx):
    await ctx.send("https://nationalmuseumpublications.co.za/wp-content/uploads/2024/04/Eusebio-top.jpg")

@bot.command()
async def nazario(ctx):
    await ctx.send("https://a.allegroimg.com/s512/118e5b/252f2287456f94a14902af7ff206/Plakat-RONALDO-NAZARIO-PILKA-NOZNA-100x70-cm-95")
    



@bot.command()
async def pomoc(ctx):
    help_msg = [
        "**📋 Lista komend:**",
        "• `!start` - Rozpoczyna draft (pomija wybór drużyn)",
        "• `!bonus` - Zapisuje Cię do rundy dodatkowej",
        "• `!bonusstatus` - Pokazuje status rundy dodatkowej",
        "• `!druzyny` - Pokazuje dostępne drużyny",
        "• `!wybieram [numery]` - Wybiera zawodników (np. `!wybieram 1575, 42`)",
        "• `!wybieram_bonus [numery]` - Wybiera dodatkowych zawodników",
        "• `!lista` - Pokazuje wybranych zawodników",
        "• `!czas` - Pokazuje pozostały czas",
        "• `!pomoc` - Ta wiadomość",
        "• `!lubicz` - Obrazek Lubicz",
        "• `!komar` - Obrazek Komar",
        "• `!reset` - Resetuje draft (admin)"
        "• `!nazario` - Obrazek Nazario",
        "• `!paei100` - Kto tam wie",
        "• `!boniek` - Obrazek Boniek",
        "• `!eusebio` - Obrazek Eusebio",
    ]
    await ctx.send("\n".join(help_msg))

# ========== URUCHOMIENIE BOTA ========== #
if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        raise ValueError("Brak tokenu Discord w zmiennych środowiskowych!")
    bot.run(TOKEN)
