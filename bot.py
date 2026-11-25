"""Mao-bot för Discord (prototyp). Kör: python bot.py"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

import cards
from game import Game, Outcome

load_dotenv()
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.getenv("GUILD_ID")  # valfritt: gör att kommandon dyker upp direkt på just din server

logging.basicConfig(level=logging.INFO)
games: dict[int, Game] = {}  # kanal-id -> spel (ligger i minnet, försvinner vid omstart)
game_creation_lock = asyncio.Lock()


# ---------------------------------------------------------------- hjälpare
def png_file(data: bytes, name: str) -> discord.File:
    if not data or len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Ogiltig PNG-data for {name}")
    return discord.File(io.BytesIO(data), filename=name)


def table_text(game: Game, event: str = "") -> str:
    lines = [event] if event else []
    top = game.top
    lines.append(f"**Översta kortet:** {top.label if top else 'inget ännu – första kortet läggs från handen'}")
    lines.append(f"**Ordning:** {game.order_text()}")
    return "\n".join(lines)


async def post_table(game: Game, channel: discord.abc.Messageable, event: str = "") -> None:
    """Skickar ett nytt bordsmeddelande och tar bort det gamla, så att det alltid ligger längst ner."""
    if game.table_msg is not None:
        try:
            await game.table_msg.delete()
        except discord.HTTPException:
            logging.exception("Kunde inte ta bort föregående bordsmeddelande")
    if game.finished:
        view = TableView(
            game.channel_id,
            game.seq,
            game.last is not None,
            game.last_judgment is not None or game.last is not None,
            game.round_id,
            game.started and not game.finished,
        )
        game.table_msg = await channel.send(
            event + "\nStarta en ny runda med `/mao starta`. Om vinsten var fel, tryck **Felbedömning**.",
            view=view,
        )
        return
    view = TableView(
        game.channel_id,
        game.seq,
        game.last is not None,
        game.last_judgment is not None,
        game.round_id,
        game.started and not game.finished,
    )
    top = game.top
    if top is None:
        game.table_msg = await channel.send(content=table_text(game, event), view=view)
    else:
        game.table_msg = await channel.send(
            content=table_text(game, event),
            file=png_file(cards.render_top(top), "pile.png"),
            view=view,
        )


def hand_parts(game: Game, uid: int, page: int = 0, note: str = ""):
    """Returnerar (text, png eller None, view eller None, sida) för spelarens privata handvy."""
    hand = game.sorted_hand(uid)
    if not hand:
        return (note or "Du har inga kort kvar."), None, None, 0
    pages = math.ceil(len(hand) / 25)
    page = max(0, min(page, pages - 1))
    text = f"{note}\n" if note else ""
    top = game.top
    top_label = top.label if top else "inget ännu – lägg första kortet från handen"
    text += f"Din hand ({len(hand)} kort). Översta kortet: {top_label}."
    if pages > 1:
        text += f"\nSida {page + 1}/{pages} i dropdown-menyn."
    return text, cards.render_hand(hand), HandView(game.channel_id, hand, page, game.round_id, game.seq), page


async def send_hand(interaction: discord.Interaction, game: Game, note: str = "") -> None:
    round_id, seq = game.round_id, game.seq
    text, png, view, page = hand_parts(game, interaction.user.id, 0, note)
    kwargs = {"content": text, "ephemeral": True}
    if png:
        kwargs["file"] = png_file(png, "hand.png")
    if view:
        kwargs["view"] = view
    await interaction.response.send_message(**kwargs)
    async with game.lock:
        if games.get(game.channel_id) is not game or game.round_id != round_id:
            await interaction.edit_original_response(
                content="Den här handen tillhör en äldre runda. Öppna handen från det senaste bordsmeddelandet.",
                attachments=[],
                view=None,
            )
            return
        if game.seq != seq:
            text, png, view, page = hand_parts(game, interaction.user.id, page, "Spelet ändrades; handen har uppdaterats.")
            game.hand_ix[interaction.user.id] = (interaction, page)
            await interaction.edit_original_response(
                content=text,
                attachments=[png_file(png, "hand.png")] if png else [],
                view=view,
            )
            return
        game.hand_ix[interaction.user.id] = (interaction, page)


async def refresh_hand(interaction: discord.Interaction, game: Game, page: int, note: str) -> None:
    """Uppdaterar det privata handmeddelandet som knappen/dropdownen sitter i."""
    text, png, view, page = hand_parts(game, interaction.user.id, page, note)
    kwargs = {"content": text, "attachments": [png_file(png, "hand.png")] if png else [], "view": view}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    else:
        await interaction.response.edit_message(**kwargs)
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
            logging.exception("Kunde inte uppdatera handen för %s", uid)
            game.hand_ix.pop(uid, None)


async def after_event(game: Game, channel: discord.abc.Messageable, event: str, skip: int | None = None) -> None:
    vote = game.pending_vote
    if vote is not None and vote.seq != game.seq:
        await cancel_pending_vote(game, "Omröstningen avbröts eftersom spelet ändrades.")
    await post_table(game, channel, event)
    await refresh_all_hands(game, skip)


async def cancel_pending_vote(game: Game, message: str) -> None:
    vote = game.pending_vote
    if vote is None:
        return
    game.pending_vote = None
    vote.stop()
    if vote.message is not None:
        try:
            await vote.message.edit(content=message, view=None)
        except discord.HTTPException:
            logging.exception("Kunde inte uppdatera det avbrutna röstmeddelandet")


def find_game(interaction: discord.Interaction) -> Game | None:
    return games.get(interaction.channel_id)


async def send_private(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ---------------------------------------------------------------- vyer
class PenaltyModal(discord.ui.Modal, title="Dela ut straffkort"):
    def __init__(
        self,
        channel_id: int,
        seq: int,
        round_id: str,
        judge_uid: int,
        target_uid: int,
        take_back: bool,
    ):
        super().__init__(timeout=300)
        self.channel_id, self.seq = channel_id, seq
        self.round_id = round_id
        self.judge_uid, self.target_uid, self.take_back = judge_uid, target_uid, take_back
        self.reason = discord.ui.TextInput(
            label="Anledning",
            placeholder="Varför får spelaren ett straffkort?",
            max_length=80,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        game = games.get(self.channel_id)
        if game is None:
            await send_private(interaction, "Spelet är slut.")
            return
        await interaction.response.defer(ephemeral=True)
        async with game.lock:
            if games.get(self.channel_id) is not game or game.round_id != self.round_id:
                await send_private(interaction, "Den här straffmenyn tillhör en äldre runda.")
                return
            if interaction.user.id != self.judge_uid:
                await send_private(interaction, "Bara den som öppnade straffmenyn kan skicka den.")
                return
            if game.seq != self.seq:
                await send_private(interaction, "Spelet har ändrats. Öppna straffmenyn igen.")
                return
            reason = self.reason.value.strip()
            if not reason:
                await send_private(interaction, "Skriv en anledning.")
                return
            if self.take_back:
                if game.last is None or game.last.kind != "play" or game.last.uid != self.target_uid:
                    await send_private(
                        interaction,
                        "Kortet kan bara tas tillbaka från spelaren som lade det senast.",
                    )
                    return
                out = game.penalize_last(
                    self.seq,
                    interaction.user.id,
                    reason,
                    target=self.target_uid,
                    take_back=True,
                )
            else:
                out = game.penalty(self.target_uid, interaction.user.id, reason)
            if not out.ok:
                await send_private(interaction, out.private)
                return
            await after_event(game, interaction.channel, out.event)
            await interaction.followup.send("Straffkort utdelat.", ephemeral=True)


class PenaltyTargetView(discord.ui.View):
    def __init__(self, channel_id: int, seq: int, round_id: str, judge_uid: int, reason: str | None):
        super().__init__(timeout=180)
        self.channel_id, self.seq, self.round_id, self.judge_uid = channel_id, seq, round_id, judge_uid
        self.reason = reason
        self.take_back = False

        target = discord.ui.UserSelect(
            placeholder="Välj spelaren som ska få straffkortet",
            min_values=1,
            max_values=1,
        )
        target.callback = self.select_target
        self.add_item(target)

        if reason is None:
            self.take_back_button = discord.ui.Button(
                label="Ta tillbaka kortet: Nej",
                style=discord.ButtonStyle.secondary,
            )
            self.take_back_button.callback = self.toggle_take_back
            self.add_item(self.take_back_button)

    async def select_target(self, interaction: discord.Interaction):
        game = games.get(self.channel_id)
        if game is None or not game.started:
            await send_private(interaction, "Spelet är slut.")
            return
        reason = self.reason
        if reason is None:
            if (
                games.get(self.channel_id) is not game
                or game.round_id != self.round_id
                or game.finished
                or interaction.user.id != self.judge_uid
                or self.judge_uid not in game.players
                or game.seq != self.seq
            ):
                await send_private(interaction, "Spelet har ändrats. Öppna straffmenyn igen.")
                return
            target_uid = int(interaction.data["values"][0])
            if target_uid not in game.players:
                await send_private(interaction, "Den spelaren är inte med i spelet.")
                return
            if self.take_back and (
                game.last is None or game.last.kind != "play" or game.last.uid != target_uid
            ):
                await interaction.response.send_message(
                    "Kortet kan bara tas tillbaka från spelaren som lade det senast.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(
                PenaltyModal(
                    self.channel_id,
                    self.seq,
                    self.round_id,
                    self.judge_uid,
                    target_uid,
                    self.take_back,
                )
            )
            return
        await interaction.response.defer(ephemeral=True)
        async with game.lock:
            if games.get(self.channel_id) is not game or game.round_id != self.round_id:
                await send_private(interaction, "Den här straffmenyn tillhör en äldre runda.")
                return
            if game.finished:
                await send_private(interaction, "Spelet är slut.")
                return
            if interaction.user.id != self.judge_uid or self.judge_uid not in game.players:
                await send_private(interaction, "Du är inte längre med i spelet.")
                return
            if game.seq != self.seq:
                await send_private(interaction, "Spelet har ändrats. Öppna straffmenyn igen.")
                return
            target_uid = int(interaction.data["values"][0])
            if target_uid not in game.players:
                await send_private(interaction, "Den spelaren är inte med i spelet.")
                return
            out = game.penalty(target_uid, self.judge_uid, reason)
            if not out.ok:
                await send_private(interaction, out.private)
                return
            await after_event(game, interaction.channel, out.event)
            await interaction.followup.send("Straffkort utdelat.", ephemeral=True)

    async def toggle_take_back(self, interaction: discord.Interaction):
        game = games.get(self.channel_id)
        if game is None or game.round_id != self.round_id or game.seq != self.seq:
            await interaction.response.send_message("Spelet har ändrats. Öppna straffmenyn igen.", ephemeral=True)
            return
        self.take_back = not self.take_back
        self.take_back_button.label = f"Ta tillbaka kortet: {'Ja' if self.take_back else 'Nej'}"
        await interaction.response.edit_message(view=self)


class TableView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logging.exception("Fel i bordsvyn", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Något gick fel.", ephemeral=True)
            else:
                await interaction.response.send_message("Något gick fel.", ephemeral=True)
        except discord.HTTPException:
            pass

    def __init__(
        self,
        channel_id: int,
        seq: int,
        can_penalize: bool,
        can_undo_judgment: bool,
        round_id: str,
        active: bool,
    ):
        super().__init__(timeout=None)
        self.channel_id, self.seq, self.round_id = channel_id, seq, round_id
        self.not_your_turn.disabled = not can_penalize
        self.wrong_judgment.disabled = not can_undo_judgment
        self.pratade.disabled = not active
        self.straff.disabled = not active
        self.propose_card.disabled = not active

    @discord.ui.button(label="Visa min hand", emoji="🃏", style=discord.ButtonStyle.primary)
    async def show_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = games.get(self.channel_id)
        if game is None or interaction.user.id not in game.players:
            await interaction.response.send_message("Du är inte med i det här spelet.", ephemeral=True)
            return
        if game.round_id != self.round_id:
            await interaction.response.send_message("Det här bordsmeddelandet tillhör en äldre runda.", ephemeral=True)
            return
        await send_hand(interaction, game)

    async def _judge(self, interaction: discord.Interaction, reason: str, take_back: bool = True) -> None:
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.send_message("Spelet är slut.", ephemeral=True)
            return
        await interaction.response.defer()
        async with game.lock:
            if (
                games.get(self.channel_id) is not game
                or game.round_id != self.round_id
                or game.seq != self.seq
            ):
                await send_private(interaction, "Spelet har ändrats. Använd knappen på det senaste bordsmeddelandet.")
                return
            out = game.penalize_last(self.seq, interaction.user.id, reason, take_back=take_back)
            if not out.ok:
                await send_private(interaction, out.private)
                return
            await after_event(game, interaction.channel, out.event)

    @discord.ui.button(label="Inte din tur", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def not_your_turn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._judge(interaction, "Inte din tur")

    async def _open_penalty_target(self, interaction: discord.Interaction, reason: str | None):
        game = games.get(self.channel_id)
        if game is None or not game.started or game.finished:
            await interaction.response.send_message("Spelet är slut.", ephemeral=True)
            return
        if interaction.user.id not in game.players:
            await interaction.response.send_message("Du är inte med i det här spelet.", ephemeral=True)
            return
        if game.round_id != self.round_id or game.seq != self.seq:
            await interaction.response.send_message("Spelet har ändrats. Använd knappen på det senaste bordsmeddelandet.", ephemeral=True)
            return
        prompt = (
            "Välj vem som får straffkortet för att ha pratat."
            if reason == "Pratade"
            else "Välj spelare och om det senaste lagda kortet ska tas tillbaka."
        )
        await interaction.response.send_message(
            prompt,
            view=PenaltyTargetView(self.channel_id, self.seq, self.round_id, interaction.user.id, reason),
            ephemeral=True,
        )

    @discord.ui.button(label="Pratade", emoji="🗣️", style=discord.ButtonStyle.danger)
    async def pratade(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_penalty_target(interaction, "Pratade")

    @discord.ui.button(label="Straff", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def straff(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_penalty_target(interaction, None)

    @discord.ui.button(label="Föreslå nytt kort", emoji="🗳️", style=discord.ButtonStyle.primary)
    async def propose_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.send_message("Spelet är slut.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with game.lock:
            if (
                games.get(self.channel_id) is not game
                or game.round_id != self.round_id
                or game.seq != self.seq
                or game.finished
            ):
                await send_private(interaction, "Spelet har ändrats. Använd knappen på det senaste bordsmeddelandet.")
                return
            if interaction.user.id not in game.players:
                await send_private(interaction, "Du är inte med i det här spelet.")
                return
            if game.pending_vote is not None:
                await send_private(interaction, "En omröstning pågår redan.")
                return
            vote = NewCardVoteView(game.channel_id, game.round_id, game.seq, interaction.user.id)
            game.pending_vote = vote
            try:
                vote.message = await interaction.channel.send(content=vote.status_text(game), view=vote)
            except discord.HTTPException:
                game.pending_vote = None
                vote.stop()
                logging.exception("Kunde inte starta omröstningen")
                await send_private(interaction, "Kunde inte starta omröstningen. Försök igen.")
                return
            await interaction.followup.send(
                "Omröstningen startad. Ditt förslag räknas som en ja-röst.",
                ephemeral=True,
            )

    @discord.ui.button(label="Felbedömning", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def wrong_judgment(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.send_message("Spelet är slut.", ephemeral=True)
            return
        await interaction.response.defer()
        async with game.lock:
            if (
                games.get(self.channel_id) is not game
                or game.round_id != self.round_id
                or game.seq != self.seq
            ):
                await send_private(interaction, "Spelet har ändrats. Använd knappen på det senaste bordsmeddelandet.")
                return
            if game.finished and game.last is not None and game.last_judgment is None:
                out = game.penalize_last(self.seq, interaction.user.id, "Felbedömning av vinst")
            else:
                out = game.undo_last_judgment(interaction.user.id)
            if not out.ok:
                await send_private(interaction, out.private)
                return
            await after_event(game, interaction.channel, out.event)


class NewCardVoteView(discord.ui.View):
    def __init__(self, channel_id: int, round_id: str, seq: int, proposer_uid: int):
        super().__init__(timeout=20)
        self.channel_id, self.round_id, self.seq = channel_id, round_id, seq
        self.votes = {proposer_uid: True}
        self.message: discord.Message | None = None

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logging.exception("Fel i omröstningen", exc_info=error)
        try:
            await send_private(interaction, "Något gick fel i omröstningen.")
        except discord.HTTPException:
            logging.exception("Kunde inte visa omröstningsfelet för spelaren")

    def status_text(self, game: Game) -> str:
        yes = sum(self.votes.values())
        no = len(self.votes) - yes
        pending = len(game.players) - len(self.votes)
        return (
            f"Omröstning om att vända upp ett nytt kort. "
            f"Ja: {yes} · Nej: {no} · Inte röstat: {pending}. "
            "Omröstningen avslutas när alla röstat eller efter 20 sekunder."
        )

    async def _cast(self, interaction: discord.Interaction, approve: bool) -> None:
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.send_message("Spelet är slut.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with game.lock:
            if games.get(self.channel_id) is not game or game.pending_vote is not self:
                await send_private(interaction, "Den här omröstningen är inte längre aktiv.")
                return
            if game.round_id != self.round_id or game.seq != self.seq or game.finished:
                await cancel_pending_vote(game, "Omröstningen avbröts eftersom spelet ändrades.")
                await send_private(interaction, "Spelet ändrades; omröstningen avbröts.")
                return
            uid = interaction.user.id
            if uid not in game.players:
                await send_private(interaction, "Du är inte med i det här spelet.")
                return
            if uid in self.votes:
                await send_private(interaction, "Du har redan röstat.")
                return
            self.votes[uid] = approve
            if len(self.votes) == len(game.players):
                await self._finish_locked(game, interaction.channel)
            elif self.message is not None:
                try:
                    await self.message.edit(content=self.status_text(game), view=self)
                except discord.HTTPException:
                    logging.exception("Kunde inte uppdatera rösterna")
                    await interaction.followup.send(
                        "Din röst registrerades, men röstpanelen kunde inte uppdateras.",
                        ephemeral=True,
                    )
                    return
            await interaction.followup.send("Din röst är registrerad.", ephemeral=True)

    async def _finish_locked(self, game: Game, channel: discord.abc.Messageable) -> None:
        yes = sum(self.votes.values())
        no = len(self.votes) - yes
        approved = yes > no
        game.pending_vote = None
        self.stop()
        out: Outcome | None = None
        if approved:
            out = game.reveal_drawn_card(self.seq)
            if not out.ok:
                result = f"Omröstningen avbröts: {out.private}"
            else:
                result = f"Omröstningen godkändes ({yes} ja, {no} nej)."
        else:
            result = f"Förslaget avslogs ({yes} ja, {no} nej)."
        if self.message is not None:
            try:
                await self.message.edit(content=result, view=None)
            except discord.HTTPException:
                logging.exception("Kunde inte uppdatera det avslutade röstmeddelandet")
        if approved and out is not None and out.ok:
            await after_event(game, channel, out.event)

    async def on_timeout(self) -> None:
        game = games.get(self.channel_id)
        if game is None or self.message is None:
            return
        async with game.lock:
            if games.get(self.channel_id) is not game or game.pending_vote is not self:
                return
            if game.round_id != self.round_id or game.seq != self.seq or game.finished:
                await cancel_pending_vote(game, "Omröstningen avbröts eftersom spelet ändrades.")
                return
            await self._finish_locked(game, self.message.channel)

    @discord.ui.button(label="Ja", style=discord.ButtonStyle.success)
    async def vote_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._cast(interaction, True)

    @discord.ui.button(label="Nej", style=discord.ButtonStyle.danger)
    async def vote_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._cast(interaction, False)


class HandView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logging.exception("Fel i handvyn", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Något gick fel, tryck på Visa min hand igen.", ephemeral=True)
            else:
                await interaction.response.send_message("Något gick fel, tryck på Visa min hand igen.", ephemeral=True)
        except discord.HTTPException:
            pass

    def __init__(self, channel_id: int, hand: list, page: int, round_id: str, seq: int):
        super().__init__(timeout=900)
        self.channel_id, self.page, self.round_id, self.seq = channel_id, page, round_id, seq
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
        await interaction.response.defer()
        async with game.lock:
            if games.get(self.channel_id) is not game or game.round_id != self.round_id:
                await interaction.edit_original_response(
                    content="Den här handen tillhör en äldre runda. Tryck Visa min hand igen.",
                    attachments=[],
                    view=None,
                )
                return
            if game.seq != self.seq:
                await refresh_hand(interaction, game, self.page, "Spelet ändrades; handen har uppdaterats.")
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

    async def _turn_page(self, interaction: discord.Interaction, delta: int) -> None:
        game = games.get(self.channel_id)
        if game is None:
            await interaction.response.edit_message(content="Spelet är slut.", attachments=[], view=None)
            return
        await interaction.response.defer()
        async with game.lock:
            if games.get(self.channel_id) is not game or game.round_id != self.round_id:
                await interaction.edit_original_response(
                    content="Den här handen tillhör en äldre runda. Tryck Visa min hand igen.",
                    attachments=[],
                    view=None,
                )
                return
            await refresh_hand(interaction, game, self.page + delta, "")

    async def on_prev(self, interaction: discord.Interaction):
        await self._turn_page(interaction, -1)

    async def on_next(self, interaction: discord.Interaction):
        await self._turn_page(interaction, 1)


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


@bot.tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logging.exception("Kommandofel", exc_info=error)
    msg = "Något gick fel i boten. Försök igen."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass
mao = app_commands.Group(name="mao", description="Kortspelet Mao", guild_only=True)


@mao.command(name="ny", description="Skapa ett nytt spel i den här kanalen (du blir regelhållare)")
async def ny(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    async with game_creation_lock:
        old = find_game(interaction)
        if old and not old.finished:
            message = (
                "Det pågår redan ett spel här. Avsluta med `/mao slut`."
                if old.started
                else "Det finns redan en lobby här. Gå med med `/mao delta` eller avsluta den med `/mao slut`."
            )
            await send_private(interaction, message)
            return
        if old:
            async with old.lock:
                if games.get(interaction.channel_id) is not old:
                    await send_private(interaction, "Spelet ändrades. Försök igen.")
                    return
                if not old.finished:
                    message = (
                        "Det pågår redan ett spel här. Avsluta med `/mao slut`."
                        if old.started
                        else "Det finns redan en lobby här. Gå med med `/mao delta` eller avsluta den med `/mao slut`."
                    )
                    await send_private(interaction, message)
                    return
                game = Game(channel_id=interaction.channel_id, host_id=interaction.user.id)
                game.add_player(interaction.user.id, interaction.user.display_name)
                games[interaction.channel_id] = game
        else:
            game = Game(channel_id=interaction.channel_id, host_id=interaction.user.id)
            game.add_player(interaction.user.id, interaction.user.display_name)
            games[interaction.channel_id] = game
    await interaction.channel.send(
        f"🃏 **Mao!** {interaction.user.mention} är regelhållare.\n"
        "Skriv `/mao delta` för att vara med. När alla är här skriver regelhållaren `/mao starta`."
    )
    await interaction.edit_original_response(content="Ny lobby skapad.")


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
    await interaction.response.defer(ephemeral=True)
    async with game.lock:
        if games.get(interaction.channel_id) is not game:
            await send_private(interaction, "Spelet har ersatts. Starta från det aktuella spelet.")
            return
        if game.started and not game.finished:
            await send_private(interaction, "En runda pågår redan.")
            return
        await cancel_pending_vote(game, "Omröstningen avbröts eftersom en ny runda startade.")
        error = game.start(kort)
        if error:
            await send_private(interaction, error)
            return
        await interaction.channel.send(
            "Korten är utdelade!\n"
            f"Så sitter ni i cirkeln: {game.order_text(with_counts=False)}\n"
            "Högen är tom från början; en spelare lägger första kortet från sin hand. "
            "Ni avgör själva vems tur det är och vad som är rätt. Alla spelare kan döma. "
            "Tryck på **Visa min hand** nedan."
        )
        await post_table(game, interaction.channel)
        await refresh_all_hands(game)
        await interaction.edit_original_response(content="Rundan har startat.")


@mao.command(name="slut", description="Avsluta spelet (regelhållare)")
async def slut(interaction: discord.Interaction):
    game = find_game(interaction)
    if game is None or interaction.user.id not in game.keepers:
        await interaction.response.send_message("Bara regelhållaren kan avsluta spelet.", ephemeral=True)
        return
    await interaction.response.defer()
    async with game.lock:
        if games.get(interaction.channel_id) is not game:
            await send_private(interaction, "Spelet har redan avslutats eller ersatts.")
            return
        await cancel_pending_vote(game, "Omröstningen avbröts eftersom spelet avslutades.")
        if game.table_msg is not None:
            try:
                await game.table_msg.delete()
            except discord.HTTPException:
                logging.exception("Kunde inte ta bort bordsmeddelandet när spelet avslutades")
        del games[interaction.channel_id]
        await interaction.edit_original_response(content="Spelet är avslutat. Starta ett nytt med `/mao ny`.")


bot.tree.add_command(mao)

if __name__ == "__main__":
    bot.run(TOKEN)
