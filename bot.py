"""Mao-bot för Discord (prototyp). Kör: python bot.py"""
from __future__ import annotations

import io
import logging
import math
import os
from typing import Optional

import discord
from discord import app_commands
from dotenv import load_dotenv

import cards
from game import EFFECTS, RANKS, REASONS, Game, Outcome

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.getenv("GUILD_ID")  # valfritt: gör att kommandon dyker upp direkt på just din server

logging.basicConfig(level=logging.INFO)
games: dict[int, Game] = {}  # kanal-id -> spel (ligger i minnet, försvinner vid omstart)


# ---------------------------------------------------------------- hjälpare
def png_file(data: bytes, name: str) -> discord.File:
    return discord.File(io.BytesIO(data), filename=name)


def table_text(game: Game, event: str = "") -> str:
    lines = [event] if event else []
    lines.append(f"**Översta kortet:** {game.top.label}")
    lines.append(f"**Ordning:** {game.order_text()}")
    return "\n".join(lines)


async def post_table(game: Game, channel: discord.abc.Messageable, event: str = "") -> None:
    """Skickar ett nytt bordsmeddelande och tar bort det gamla, så att det alltid ligger längst ner."""
    if game.table_msg is not None:
        try:
            await game.table_msg.delete()
        except discord.HTTPException:
            pass
    if game.finished:
        game.table_msg = None
        await channel.send(event + "\nStarta en ny runda med `/mao starta`.")
        return
    game.table_msg = await channel.send(
        content=table_text(game, event),
        file=png_file(cards.render_top(game.top), "pile.png"),
        view=TableView(game.channel_id, game.seq, game.last is not None),
    )


def hand_parts(game: Game, uid: int, page: int = 0, note: str = ""):
    """Returnerar (text, png eller None, view eller None, sida) för spelarens privata handvy."""
    hand = game.sorted_hand(uid)
    if not hand:
        return (note or "Du har inga kort kvar."), None, None, 0
    pages = math.ceil(len(hand) / 25)
    page = max(0, min(page, pages - 1))
    text = f"{note}\n" if note else ""
    text += f"Din hand ({len(hand)} kort). Översta kortet: {game.top.label}."
    if pages > 1:
        text += f"\nSida {page + 1}/{pages} i dropdown-menyn."
    return text, cards.render_hand(hand), HandView(game.channel_id, hand, page), page


async def send_hand(interaction: discord.Interaction, game: Game, note: str = "") -> None:
    text, png, view, page = hand_parts(game, interaction.user.id, 0, note)
    kwargs = {"content": text, "ephemeral": True}
    if png:
        kwargs["file"] = png_file(png, "hand.png")
    if view:
        kwargs["view"] = view
    await interaction.response.send_message(**kwargs)
    game.hand_ix[interaction.user.id] = (interaction, page)  # så att handen kan uppdateras live


async def refresh_hand(interaction: discord.Interaction, game: Game, page: int, note: str) -> None:
    """Uppdaterar det privata handmeddelandet som knappen/dropdownen sitter i."""
    text, png, view, page = hand_parts(game, interaction.user.id, page, note)
    await interaction.response.edit_message(
        content=text, attachments=[png_file(png, "hand.png")] if png else [], view=view
    )
    game.hand_ix[interaction.user.id] = (interaction, page)


async def refresh_all_hands(game: Game, skip: int | None = None) -> None:
    """Uppdaterar allas öppna handvyer. Discord tillåter det i ca 15 min efter spelarens senaste klick;
    går det inte längre trycker spelaren på 'Visa min hand' igen."""
    for uid, (ix, page) in list(game.hand_ix.items()):
        if uid == skip or uid not in game.hands:
            continue
        text, png, view, page = hand_parts(game, uid, page)
        try:
            await ix.edit_original_response(
                content=text, attachments=[png_file(png, "hand.png")] if png else [], view=view
            )
            game.hand_ix[uid] = (ix, page)
        except discord.HTTPException:
            game.hand_ix.pop(uid, None)


async def after_event(game: Game, channel: discord.abc.Messageable, event: str, skip: int | None = None) -> None:
    await post_table(game, channel, event)
    await refresh_all_hands(game, skip)


def find_game(interaction: discord.Interaction) -> Game | None:
    return games.get(interaction.channel_id)


# ---------------------------------------------------------------- vyer
class TableView(discord.ui.View):
    def __init__(self, channel_id: int, seq: int, can_penalize: bool):
        super().__init__(timeout=None)
        self.channel_id, self.seq = channel_id, seq
        self.not_your_turn.disabled = self.wrong_card.disabled = not can_penalize

    @discord.ui.button(label="Visa min hand", emoji="🃏", style=discord.ButtonStyle.primary)
    async def show_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = games.get(self.channel_id)
        if game is None or interaction.user.id not in game.players:
            await interaction.response.send_message("Du är inte med i det här spelet.", ephemeral=True)
            return
        await send_hand(interaction, game)

    async def _judge(self, interaction: discord.Interaction, reason: str) -> None:
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.send_message("Spelet är slut.", ephemeral=True)
            return
        out = game.penalize_last(self.seq, interaction.user.id, reason)
        if not out.ok:
            await interaction.response.send_message(out.private, ephemeral=True)
            return
        await interaction.response.defer()
        await after_event(game, interaction.channel, out.event)

    @discord.ui.button(label="Inte din tur", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def not_your_turn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._judge(interaction, "Inte din tur")

    @discord.ui.button(label="Fel kort", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def wrong_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._judge(interaction, "Fel kort")


class HandView(discord.ui.View):
    def __init__(self, channel_id: int, hand: list, page: int):
        super().__init__(timeout=900)
        self.channel_id, self.page = channel_id, page
        pages = math.ceil(len(hand) / 25)

        chunk = hand[page * 25:(page + 1) * 25]
        select = discord.ui.Select(
            placeholder="Välj kort att lägga",
            options=[discord.SelectOption(label=c.label, value=c.id) for c in chunk],
            row=0,
        )
        select.callback = self.on_select
        self.add_item(select)

        draw = discord.ui.Button(label="Dra kort", emoji="➕", style=discord.ButtonStyle.secondary, row=1)
        draw.callback = self.on_draw
        self.add_item(draw)
        pass_btn = discord.ui.Button(label="Passa", emoji="⏭️", style=discord.ButtonStyle.secondary, row=1)
        pass_btn.callback = self.on_pass
        self.add_item(pass_btn)
        if pages > 1:
            prev = discord.ui.Button(label="◀", disabled=page == 0, row=1)
            nxt = discord.ui.Button(label="▶", disabled=page >= pages - 1, row=1)
            prev.callback, nxt.callback = self.on_prev, self.on_next
            self.add_item(prev)
            self.add_item(nxt)

    async def _act(self, interaction: discord.Interaction, action: str, card_id: str = "") -> None:
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.edit_message(content="Spelet är slut.", attachments=[], view=None)
            return
        uid = interaction.user.id
        out: Outcome = {
            "play": lambda: game.play(uid, card_id),
            "draw": lambda: game.draw(uid),
            "pass": lambda: game.pass_turn(uid),
        }[action]()
        await refresh_hand(interaction, game, self.page, out.private)
        if out.event:
            await after_event(game, interaction.channel, out.event, skip=uid)

    async def on_select(self, interaction: discord.Interaction):
        await self._act(interaction, "play", interaction.data["values"][0])

    async def on_draw(self, interaction: discord.Interaction):
        await self._act(interaction, "draw")

    async def on_pass(self, interaction: discord.Interaction):
        await self._act(interaction, "pass")

    async def on_prev(self, interaction: discord.Interaction):
        await refresh_hand(interaction, games[self.channel_id], self.page - 1, "")

    async def on_next(self, interaction: discord.Interaction):
        await refresh_hand(interaction, games[self.channel_id], self.page + 1, "")


# ---------------------------------------------------------------- bot och kommandon
class MaoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())  # inga privilegierade intents behövs
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        logging.info("Inloggad som %s", self.user)


bot = MaoBot()
mao = app_commands.Group(name="mao", description="Kortspelet Mao", guild_only=True)


@mao.command(name="ny", description="Skapa ett nytt spel i den här kanalen (du blir regelhållare)")
async def ny(interaction: discord.Interaction):
    old = find_game(interaction)
    if old and old.started and not old.finished:
        await interaction.response.send_message("Det pågår redan ett spel här. Avsluta med `/mao slut`.", ephemeral=True)
        return
    game = Game(channel_id=interaction.channel_id, host_id=interaction.user.id)
    game.add_player(interaction.user.id, interaction.user.display_name)
    games[interaction.channel_id] = game
    await interaction.response.send_message(
        f"🃏 **Mao!** {interaction.user.mention} är regelhållare.\n"
        "Skriv `/mao delta` för att vara med. När alla är här skriver regelhållaren `/mao starta`."
    )


@mao.command(name="delta", description="Gå med i spelet")
async def delta(interaction: discord.Interaction):
    game = find_game(interaction)
    if game is None:
        await interaction.response.send_message("Inget spel här. Starta ett med `/mao ny`.", ephemeral=True)
        return
    if not game.add_player(interaction.user.id, interaction.user.display_name):
        await interaction.response.send_message("Du är redan med, eller så har spelet startat.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"**{interaction.user.display_name}** är med! Spelare: {', '.join(game.name(p) for p in game.players)}"
    )


@mao.command(name="starta", description="Dela ut och starta rundan (regelhållare)")
@app_commands.describe(kort="Antal kort per spelare (standard 5)")
async def starta(interaction: discord.Interaction, kort: app_commands.Range[int, 3, 12] = 5):
    game = find_game(interaction)
    if game is None or interaction.user.id not in game.keepers:
        await interaction.response.send_message("Bara regelhållaren kan starta, och det måste finnas ett spel.", ephemeral=True)
        return
    error = game.start(kort)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_message(
        f"Korten är utdelade! **{game.name(game.players[0])}** börjar.\n"
        f"Så sitter ni i cirkeln: {game.order_text(with_counts=False)}\n"
        "Ni avgör själva vems tur det är och vad som är rätt. Regelhållarna dömer. Tryck på **Visa min hand** nedan."
    )
    await post_table(game, interaction.channel)
    await refresh_all_hands(game)


@mao.command(name="hand", description="Visa din hand (bara du ser den)")
async def hand(interaction: discord.Interaction):
    game = find_game(interaction)
    if game is None or interaction.user.id not in game.players or not game.started:
        await interaction.response.send_message("Du är inte med i ett pågående spel här.", ephemeral=True)
        return
    await send_hand(interaction, game)


@mao.command(name="regelhallare", description="Gör någon till regelhållare (eller ta bort rollen)")
@app_commands.describe(spelare="Vem som ska få eller förlora rollen")
async def regelhallare(interaction: discord.Interaction, spelare: discord.Member):
    game = find_game(interaction)
    if game is None or interaction.user.id not in game.keepers:
        await interaction.response.send_message("Bara regelhållare kan göra det här.", ephemeral=True)
        return
    if spelare.id in game.keepers:
        if len(game.keepers) == 1:
            await interaction.response.send_message("Det måste finnas minst en regelhållare.", ephemeral=True)
            return
        game.keepers.discard(spelare.id)
        await interaction.response.send_message(f"{spelare.display_name} är inte längre regelhållare.")
    else:
        game.keepers.add(spelare.id)
        game.names.setdefault(spelare.id, spelare.display_name)
        await interaction.response.send_message(f"{spelare.display_name} är nu regelhållare.")


@mao.command(name="effekt", description="Hemlig effekt för en valör (syns bara för dig)")
@app_commands.describe(valor="Vilken valör som ska ha effekten", effekt="Vad som händer när kortet läggs")
@app_commands.choices(
    valor=[app_commands.Choice(name=r, value=r) for r in RANKS],
    effekt=[app_commands.Choice(name=f"{k}: {v}", value=k) for k, v in EFFECTS.items()]
    + [app_commands.Choice(name="ingen (ta bort)", value="none")],
)
async def effekt(interaction: discord.Interaction, valor: app_commands.Choice[str], effekt: app_commands.Choice[str]):
    game = find_game(interaction)
    if game is None or interaction.user.id not in game.keepers:
        await interaction.response.send_message("Bara regelhållare kan sätta effekter.", ephemeral=True)
        return
    if effekt.value == "none":
        game.effects.pop(valor.value, None)
    else:
        game.effects[valor.value] = effekt.value
    overview = "\n".join(f"{r}: {EFFECTS[e]}" for r, e in game.effects.items()) or "inga effekter"
    await interaction.response.send_message(f"Sparat (bara du ser det). Aktiva effekter:\n{overview}", ephemeral=True)


@mao.command(name="slut", description="Avsluta spelet (regelhållare)")
async def slut(interaction: discord.Interaction):
    game = find_game(interaction)
    if game is None or interaction.user.id not in game.keepers:
        await interaction.response.send_message("Bara regelhållaren kan avsluta spelet.", ephemeral=True)
        return
    if game.table_msg is not None:
        try:
            await game.table_msg.delete()
        except discord.HTTPException:
            pass
    del games[interaction.channel_id]
    await interaction.response.send_message("Spelet är avslutat. Starta ett nytt med `/mao ny`.")


@bot.tree.command(name="straff", description="Dela ut ett straffkort (regelhållare)")
@app_commands.guild_only()
@app_commands.describe(spelare="Vem som får straffkortet", anledning="Kort och vag anledning", egen_text="Egen anledning (ersätter listan)", ta_tillbaka="Ta tillbaka spelarens senaste kort (om det var senaste draget)")
@app_commands.choices(anledning=[app_commands.Choice(name=r, value=r) for r in REASONS])
async def straff(
    interaction: discord.Interaction,
    spelare: discord.Member,
    anledning: Optional[app_commands.Choice[str]] = None,
    egen_text: Optional[str] = None,
    ta_tillbaka: bool = False,
):
    game = find_game(interaction)
    if game is None or not game.started or game.finished:
        await interaction.response.send_message("Inget pågående spel här.", ephemeral=True)
        return
    if interaction.user.id not in game.keepers:
        await interaction.response.send_message("Bara regelhållare kan dela ut straffkort.", ephemeral=True)
        return
    if spelare.id not in game.players:
        await interaction.response.send_message("Den spelaren är inte med i spelet.", ephemeral=True)
        return
    reason = (egen_text or (anledning.value if anledning else "")).strip()
    if not reason:
        await interaction.response.send_message("Välj en anledning eller skriv en egen.", ephemeral=True)
        return
    if ta_tillbaka:
        out = game.penalize_last(game.seq, interaction.user.id, reason[:80], target=spelare.id)
        if not out.ok:
            await interaction.response.send_message(out.private, ephemeral=True)
            return
    else:
        out = game.penalty(spelare.id, reason[:80])
    await interaction.response.send_message("Straffkort utdelat.", ephemeral=True)
    await after_event(game, interaction.channel, out.event)


bot.tree.add_command(mao)

if __name__ == "__main__":
    bot.run(TOKEN)
