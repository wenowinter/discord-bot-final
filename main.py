import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import requests
import random
import os

# ========== FLASK SERVER ========== #
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot aktywny!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
# ========== END FLASK ========== #



intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Konfiguracja - oryginalne nazwy użytkowników
usernames = ["Wenoid", "wordlifepl"]
team_selection_order = ["Wenoid", "wordlifepl"]
player_selection_order = ["Wenoid", "wordlifepl"]
total_rounds = 3
picks_per_round = [1, 1, 3]
PLAYERS_URL = "https://gist.githubusercontent.com/wenowinter/31a3d22985e6171b06f15061a8c3613e/raw/50121c8b83d84e626b79caee280574d8d1033826/mekambe1.txt"

# Kolory dla drużyn
team_colors = {
    "Real Vardrit": "⚪",
    "Barcelona": "🔵",
    "AS Roma": "🟡",
    "Liverpool": "🔴"
}

# Czas na wybór
TEAM_SELECTION_TIME = timedelta(minutes=180)  # Zmieniono na 60 minut
PLAYER_SELECTION_TIME = timedelta(minutes=180)  # Zmieniono na 60 minut

# Stan draftu
players = []
current_index = 0
current_round = 0
picked_numbers = set()
picked_players = {username.lower(): [] for username in usernames}
pick_deadline = None
pick_timer_task = None
reminder_tasks = []
draft_started = False
team_draft_started = False
players_database = {}
user_teams = {}  # Klucze w lowercase
current_team_selector_index = 0


def find_member_by_name(members, name):
    """Znajduje użytkownika ignorując wielkość liter"""
    name_lower = name.lower()
    for member in members:
        if member.display_name.lower() == name_lower:
            return member
    return None


async def load_players():
    """Ładuje zawodników z zewnętrznego URL"""
    try:
        response = requests.get(PLAYERS_URL)
        response.raise_for_status()
        players_db = {}
        for line in response.text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                try:
                    player_id = int(parts[0])
                    players_db[player_id] = parts[1]
                except ValueError:
                    continue
        return players_db
    except Exception as e:
        print(f"Błąd ładowania pliku zawodników: {e}")
        return {}


@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user}')
    global players_database
    players_database = await load_players()
    if players_database:
        print(f"Załadowano {len(players_database)} zawodników")
    else:
        print("UWAGA: Wczytano tymczasowe dane!")
        players_database = {i: f"Zawodnik {i}" for i in range(1, 101)}


async def send_reminders(channel, user, selection_type, deadline):
    """Planuje i wysyła przypomnienia przed upływem czasu"""
    global reminder_tasks

    # Anuluj istniejące zadania przypomnień
    for task in reminder_tasks:
        task.cancel()
    reminder_tasks = []

    # Ustaw przypomnienia na 4 godziny i 2 godziny przed terminem
    reminder_2h = deadline - timedelta(hours=2)
    reminder_1h = deadline - timedelta(hours=1)

    # Zaplanuj przypomnienie na 4 godziny
    wait_time = (reminder_2h - datetime.utcnow()).total_seconds()
    if wait_time > 0:
        task = asyncio.create_task(
            send_reminder(channel, user, selection_type, "2 godziny",
                         wait_time))
        reminder_tasks.append(task)

    # Zaplanuj przypomnienie na 2 godziny
    wait_time = (reminder_1h - datetime.utcnow()).total_seconds()
    if wait_time > 0:
        task = asyncio.create_task(
            send_reminder(channel, user, selection_type, "1 godzinę",
                         wait_time))
        reminder_tasks.append(task)


async def send_reminder(channel, user, selection_type, time_left, wait_time):
    """Wysyła przypomnienie po określonym czasie oczekiwania"""
    await asyncio.sleep(wait_time)

    # Wysyłaj przypomnienie tylko jeśli odpowiedni draft jest nadal aktywny
    if ((selection_type == "team" and team_draft_started) or
            (selection_type == "player" and draft_started)):
        await channel.send(
            f"⏰ PRZYPOMNIENIE: {user.mention} masz jeszcze {time_left} na wybór {selection_type}!"
        )


@bot.command()
async def druzyny(ctx):
    """Wyświetla dostępne drużyny"""
    msg = ["**Dostępne drużyny:**"]
    for team, color in team_colors.items():
        # Znajdź użytkownika, który wybrał daną drużynę
        team_owner = None
        for user, user_team in user_teams.items():
            if user_team.lower() == team.lower():
                team_owner = user
                break
        
        if team_owner:
            msg.append(f"{color} {team} (wybrana przez: {team_owner})")
        else:
            msg.append(f"{color} {team}")
    await ctx.send("\n".join(msg))


@bot.command()
async def start(ctx):
    """Rozpoczyna proces wyboru drużyn"""
    global team_draft_started, current_team_selector_index, user_teams

    if draft_started or team_draft_started:
        await ctx.send("Draft już trwa!")
        return

    team_draft_started = True
    current_team_selector_index = 0
    user_teams = {}

    await ctx.send(
        "Rozpoczynamy wybór drużyn! Kolejność wyboru:\n" + "\n".join(
            [f"{i+1}. {name}" for i, name in enumerate(team_selection_order)]))
    await next_team_selection(ctx.channel)


async def next_team_selection(channel):
    """Obsługuje następny wybór drużyny"""
    global current_team_selector_index, pick_deadline, pick_timer_task, team_draft_started

    # Sprawdź, czy wszyscy użytkownicy wybrali drużyny
    if current_team_selector_index >= len(team_selection_order):
        team_draft_started = False
        team_summary = ["**Wybieranie drużyn zakończone!**"]
        for user, team in user_teams.items():
            team_summary.append(f"{team_colors.get(team, '⚫')} {user}: {team}")

        await channel.send("\n".join(team_summary))
        await channel.send("Rozpoczynamy draft zawodników!")
        await start_player_draft(channel)
        return

    # Pobierz aktualnego wybierającego
    current_selector_name = team_selection_order[current_team_selector_index]
    current_selector = find_member_by_name(channel.guild.members,
                                           current_selector_name)

    if not current_selector:
        await channel.send(
            f"Nie znaleziono użytkownika: {current_selector_name}")
        current_team_selector_index += 1
        await next_team_selection(channel)
        return

    # Ustaw termin wyboru drużyny
    pick_deadline = datetime.utcnow() + TEAM_SELECTION_TIME

    # Pobierz dostępne drużyny
    available_teams = [
        f"{team_colors.get(t, '⚫')} {t}" for t in team_colors.keys()
        if t.lower() not in [team.lower() for team in user_teams.values()]
    ]

    # Powiadom aktualnego wybierającego
    await channel.send(
        f"{current_selector.mention}, teraz wybierasz drużynę. "
        f"Masz {TEAM_SELECTION_TIME.seconds//60} minut!\n"
        f"Dostępne drużyny: {', '.join(available_teams)}\n"
        f"Użyj komendy `!wybieram [nazwa_drużyny]` (np. `!wybieram Liverpool`)"
    )

    # Anuluj istniejący timer i rozpocznij nowy
    if pick_timer_task:
        pick_timer_task.cancel()
    pick_timer_task = asyncio.create_task(
        team_selection_timer(channel, current_selector))

    # Zaplanuj przypomnienia
    await send_reminders(channel, current_selector, "team", pick_deadline)


async def team_selection_timer(channel, selector):
    """Timer dla wyboru drużyny"""
    global current_team_selector_index

    # Oblicz pozostały czas i czekaj
    remaining_time = (pick_deadline - datetime.utcnow()).total_seconds()
    if remaining_time > 0:
        await asyncio.sleep(remaining_time)

    # Sprawdź, czy to nadal tura tego samego wybierającego
    if (team_draft_started and 
        current_team_selector_index < len(team_selection_order) and 
        team_selection_order[current_team_selector_index].lower() == selector.display_name.lower()):

        await channel.send(
            f"⏰ Czas minął! {selector.mention} nie wybrał drużyny. Losowo przypisano mu drużynę."
        )

        # Znajdź dostępne drużyny
        available_teams = [
            t for t in team_colors.keys()
            if t.lower() not in [team.lower() for team in user_teams.values()]
        ]

        # Losowo przypisz drużynę, jeśli dostępna
        if available_teams:
            selected_team = random.choice(available_teams)
            user_teams[selector.display_name.lower()] = selected_team
            await channel.send(
                f"Przypisano {selector.mention} drużynę: {team_colors.get(selected_team, '⚫')} {selected_team}"
            )

        # Przejdź do następnego wybierającego
        current_team_selector_index += 1
        await next_team_selection(channel)


async def start_player_draft(channel):
    """Rozpoczyna draft zawodników"""
    global draft_started, players, current_index, current_round, picked_numbers, picked_players

    # Znajdź wszystkich członków, którzy uczestniczą
    members = channel.guild.members
    players = []
    for name in player_selection_order:
        member = find_member_by_name(members, name)
        if member:
            players.append(member)
        else:
            await channel.send(f"Nie znaleziono użytkownika: {name}")
            return

    # Inicjalizuj stan draftu
    draft_started = True
    current_index = 0
    current_round = 0
    picked_numbers = set()
    picked_players = {username.lower(): [] for username in usernames}

    # Wyświetl przydzielone drużyny
    team_info = ["**Przydzielone drużyny:**"]
    for user, team in user_teams.items():
        team_info.append(f"{team_colors.get(team, '⚫')} {user}: {team}")

    await channel.send("\n".join(team_info))
    await channel.send(
        "\n**Kolejność wyboru zawodników:**\n" +
        "\n".join([f"{i+1}. {p.display_name}" for i, p in enumerate(players)]))

    # Rozpocznij pierwszy wybór
    await next_pick(channel)


async def next_pick(channel):
    """Obsługuje następny wybór zawodnika"""
    global pick_deadline, pick_timer_task, draft_started, current_index, current_round

    # Sprawdź, czy wszystkie rundy są zakończone
    if current_round >= total_rounds:
        await channel.send("🏁 Draft zakończony!")
        draft_started = False
        return

    # Sprawdź, czy wszyscy gracze wybrali w bieżącej rundzie
    if current_index >= len(players):
        current_index = 0
        current_round += 1
        if current_round >= total_rounds:
            await next_pick(channel)
            return

    # Pobierz aktualnego gracza
    current_player = players[current_index]
    pick_deadline = datetime.utcnow() + PLAYER_SELECTION_TIME

    # Pobierz drużynę gracza
    player_team = user_teams.get(current_player.display_name.lower(), "Nieznana")
    team_color = team_colors.get(player_team, "⚫")

    # Powiadom aktualnego gracza
    await channel.send(
        f"{team_color} {current_player.mention}, teraz wybierasz **{picks_per_round[current_round]} zawodników**. "
        f"Masz {PLAYER_SELECTION_TIME.seconds//60} minut!")

    # Anuluj istniejący timer i rozpocznij nowy
    if pick_timer_task:
        pick_timer_task.cancel()
    pick_timer_task = asyncio.create_task(
        player_selection_timer(channel, current_player))

    # Zaplanuj przypomnienia
    await send_reminders(channel, current_player, "player", pick_deadline)


async def player_selection_timer(channel, player):
    """Timer dla wyboru zawodnika"""
    global current_index

    # Oblicz pozostały czas i czekaj
    remaining_time = (pick_deadline - datetime.utcnow()).total_seconds()
    if remaining_time > 0:
        await asyncio.sleep(remaining_time)

    # Sprawdź, czy to nadal tura tego samego gracza
    if draft_started and current_index < len(players) and players[current_index] == player:
        await channel.send(
            f"⏰ Czas minął! {player.mention} nie wybrał zawodników. Przechodzimy dalej."
        )
        current_index += 1
        await next_pick(channel)


@bot.command()
async def wybieram(ctx, *, choice):
    """Komenda do wyboru drużyny lub zawodników"""
    global current_index, picked_numbers, current_team_selector_index

    # Tryb wyboru drużyny
    if team_draft_started:
        # Sprawdź, czy to kolej użytkownika
        if current_team_selector_index >= len(team_selection_order):
            await ctx.send("Wybór drużyn już się zakończył!")
            return

        current_selector_name = team_selection_order[current_team_selector_index]
        if ctx.author.display_name.lower() != current_selector_name.lower():
            await ctx.send("Nie jest teraz Twoja kolej na wybór drużyny!")
            return

        # Znajdź wybraną drużynę
        selected_team = None
        for team in team_colors.keys():
            if team.lower() == choice.lower():
                selected_team = team
                break

        if not selected_team:
            await ctx.send(
                "Nie ma takiej drużyny! Użyj !druzyny aby zobaczyć dostępne opcje."
            )
            return

        # Sprawdź, czy drużyna jest dostępna
        if selected_team.lower() in [t.lower() for t in user_teams.values()]:
            await ctx.send("Ta drużyna została już wybrana! Wybierz inną.")
            return

        # Przypisz drużynę
        user_teams[ctx.author.display_name.lower()] = selected_team
        await ctx.send(
            f"{ctx.author.display_name} wybrał drużynę: {team_colors.get(selected_team, '⚫')} {selected_team}"
        )

        # Przejdź do następnego wyboru drużyny
        current_team_selector_index += 1
        await next_team_selection(ctx.channel)
        return

    # Tryb wyboru zawodników
    elif draft_started:
        # Sprawdź, czy to kolej użytkownika
        if current_index >= len(players):
            await ctx.send("Aktualnie nikt nie wybiera zawodników.")
            return

        current_player = players[current_index]
        if ctx.author != current_player:
            await ctx.send("Nie jest teraz Twoja kolej.")
            return

        # Parsuj wybrane numery zawodników
        try:
            picks = [int(p.strip()) for p in choice.split(',')]
        except ValueError:
            await ctx.send("Podaj numery zawodników oddzielone przecinkami.")
            return

        # Sprawdź, czy wybrano właściwą liczbę zawodników
        expected_picks = picks_per_round[current_round]
        if len(picks) != expected_picks:
            await ctx.send(
                f"Musisz wybrać dokładnie {expected_picks} zawodników."
            )
            return

        # Sprawdź, czy wybrani zawodnicy istnieją
        invalid_picks = [p for p in picks if p not in players_database]
        if invalid_picks:
            await ctx.send(
                f"{ctx.author.mention} Nie ma takiego zawodnika: {', '.join(map(str, invalid_picks))}! Wybierz poprawnych graczy."
            )
            return

        # Sprawdź, czy zawodnicy nie zostali już wybrani
        duplicates = [p for p in picks if p in picked_numbers]
        if duplicates:
            await ctx.send(
                f"{ctx.author.mention}, numer(y) {', '.join(map(str, duplicates))} zostały już wybrane! Wybierz inne."
            )
            return

        # Zapisz wybory
        pick_details = [f"{p} ({players_database[p]})" for p in picks]
        picked_players[ctx.author.display_name.lower()].extend(picks)
        picked_numbers.update(picks)

        await ctx.send(
            f"{ctx.author.display_name} wybrał: {', '.join(pick_details)}"
        )

        # Przejdź do następnego wyboru
        current_index += 1
        await next_pick(ctx.channel)
    else:
        await ctx.send(
            "Draft jeszcze się nie rozpoczął. Użyj !start aby rozpocząć."
        )


@bot.command()
async def lista(ctx):
    """Wyświetla listę wybranych zawodników"""
    if not players_database:
        await ctx.send("❌ Błąd: nie załadowano listy zawodników!")
        return

    # Jeśli nikt jeszcze nie wybrał zawodników
    all_empty = True
    for picks in picked_players.values():
        if picks:
            all_empty = False
            break

    if all_empty:
        await ctx.send("Nikt jeszcze nie wybrał zawodników.")
        return

    msg = ["**Wybrani zawodnicy:**"]
    for user, picks in picked_players.items():
        if not picks:
            continue

        team = user_teams.get(user.lower(), "Nieznana")
        team_color = team_colors.get(team, "⚫")
        player_details = [f"{p} ({players_database[p]})" for p in picks]
        msg.append(
            f"{team_color} **{user}** ({team}): {', '.join(player_details)}"
        )

    # Podziel długie wiadomości
    full_msg = "\n".join(msg)
    if len(full_msg) <= 2000:
        await ctx.send(full_msg)
    else:
        parts = []
        current_part = [msg[0]]  # Dodaj nagłówek do pierwszej części
        current_length = len(msg[0])

        for line in msg[1:]:
            if current_length + len(line) + 1 <= 1900:  # Zostawia margines
                current_part.append(line)
                current_length += len(line) + 1
            else:
                parts.append("\n".join(current_part))
                current_part = [line]
                current_length = len(line)

        if current_part:
            parts.append("\n".join(current_part))

        for i, part in enumerate(parts):
            await ctx.send(f"{part}\n{f'(Część {i+1}/{len(parts)})' if len(parts) > 1 else ''}")


@bot.command()
async def reset(ctx):
    """Resetuje stan draftu"""
    global draft_started, players, current_index, current_round, picked_numbers
    global picked_players, pick_timer_task, user_teams, team_draft_started
    global current_team_selector_index, reminder_tasks

    if ctx.author.guild_permissions.administrator:
        draft_started = False
        team_draft_started = False
        players = []
        current_index = 0
        current_round = 0
        current_team_selector_index = 0
        picked_numbers = set()
        picked_players = {username.lower(): [] for username in usernames}
        user_teams = {}

        # Anuluj wszystkie zadania
        if pick_timer_task:
            pick_timer_task.cancel()

        for task in reminder_tasks:
            task.cancel()
        reminder_tasks = []

        await ctx.send("Draft zresetowany.")
    else:
        await ctx.send("❌ Tylko administrator może zresetować draft.")


@bot.command()
async def czas(ctx):
    """Pokazuje pozostały czas na wybór"""
    global pick_deadline

    if not (draft_started or team_draft_started) or pick_deadline is None:
        await ctx.send("Draft jeszcze się nie rozpoczął albo nie trwa wybór.")
        return

    # W trybie wyboru drużyny
    if team_draft_started:
        if current_team_selector_index >= len(team_selection_order):
            await ctx.send("Aktualnie nikt nie wybiera drużyny.")
            return

        current_selector_name = team_selection_order[current_team_selector_index]
        if ctx.author.display_name.lower() != current_selector_name.lower():
            await ctx.send("To nie Twoja kolej, nie podglądaj! 😎")
            return
    # W trybie draftu zawodników
    elif draft_started:
        if current_index >= len(players):
            await ctx.send("Aktualnie nikt nie wybiera zawodników.")
            return

        current_player = players[current_index]
        if ctx.author != current_player:
            await ctx.send("To nie Twoja kolej, nie podglądaj! 😎")
            return

    # Oblicz pozostały czas
    remaining = pick_deadline - datetime.utcnow()
    seconds = int(remaining.total_seconds())
    if seconds <= 0:
        await ctx.send("Twój czas już minął! ⏰")
    else:
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            await ctx.send(
                f"Masz jeszcze **{hours} godzin, {minutes} minut i {sec:02d} sekund** na wybór."
            )
        else:
            await ctx.send(
                f"Masz jeszcze **{minutes} minut i {sec:02d} sekund** na wybór."
            )


@bot.command()
async def lubicz(ctx):
    """Wyświetla obrazek Lubicz"""
    await ctx.send(
        "https://i.ibb.co/tw1tD1Ny/412206195-1406350803614829-5742951929454962748-n-removebg-preview-1.png"
    )

@bot.command()
async def komar(ctx):
    """Wyświetla obrazek Komar"""
    await ctx.send(
        "https://scontent.fpoz4-1.fna.fbcdn.net/v/t39.30808-6/462362759_3871042979836522_4405035252432652447_n.jpg?_nc_cat=103&ccb=1-7&_nc_sid=6ee11a&_nc_ohc=mLtEcPyAeiwQ7kNvwEQ0kN6&_nc_oc=AdkOQC_KOMghLeoWDifpuwrjt13CvuIDYUt3Vwps1vUGakoskHkkl6xSxqYDUbkbKpE&_nc_zt=23&_nc_ht=scontent.fpoz4-1.fna&_nc_gid=OomLe8A4aLtMLUmIYtQ5_w&oh=00_AfEO44DS7ODe3W_cjKgVEW1fij8-aEJAYKl9_RP6PzHPDQ&oe=680DD11A"
    )


@bot.command()
async def pomoc(ctx):
    """Wyświetla dostępne komendy"""
    help_text = [
        "**📋 Lista komend:**",
        "• `!start` - Rozpoczyna draft",
        "• `!druzyny` - Pokazuje dostępne drużyny",
        "• `!wybieram [drużyna/zawodnicy]` - Wybiera drużynę lub zawodników",
        "• `!lista` - Pokazuje wszystkich wybranych zawodników",
        "• `!czas` - Pokazuje pozostały czas na Twój wybór",
        "• `!pomoc` - Wyświetla listę komend",
        "• `!lubicz` - Wyświetla obrazek",
        "• `!reset` - Resetuje draft (tylko dla administratorów)"
    ]
    await ctx.send("\n".join(help_text))


# Zabezpieczenie tokena
# Pobierz token ZE ZMIENNYCH ŚRODOWISKOWYCH
TOKEN = os.environ.get('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Nie znaleziono tokenu w zmiennych środowiskowych!")

bot.run(TOKEN)
