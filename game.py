"""Spellogik för Mao. Inget Discord-beroende här, så den går att testa separat.

Boten dömer ALDRIG själv: den lägger vilket kort som helst och håller inte koll på
vems tur det är. Den håller reda på korten och låter regelhållarna (domarna) dela ut
straffkort, med möjlighet att ta tillbaka det senaste draget.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

SUITS = "SHDC"  # spader, hjärter, ruter, klöver
SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

# Hemliga effekter som regelhållaren kan koppla till en valör.
EFFECTS = {
    "draw1": "nästa spelare i cirkeln drar 1 kort",
    "draw2": "nästa spelare i cirkeln drar 2 kort",
}


@dataclass(frozen=True)
class Card:
    suit: str
    rank: str
    deck: int = 0  # vilken kortlek (om man spelar med flera)

    @property
    def id(self) -> str:
        return f"{self.suit}{self.rank}-{self.deck}"

    @property
    def label(self) -> str:
        return f"{SYMBOLS[self.suit]} {self.rank}"

    def matches(self, other: "Card") -> bool:
        return self.suit == other.suit or self.rank == other.rank


@dataclass
class Outcome:
    ok: bool  # True om draget/handlingen genomfördes
    private: str = ""  # visas bara för spelaren
    event: str = ""  # visas för alla i kanalen


@dataclass
class LastAction:
    """Det senaste draget, så att det kan ångras om någon straffas för 'Inte din tur'."""
    kind: str  # "play" | "draw" | "pass"
    uid: int
    card: Card | None = None
    victim: int | None = None
    victim_cards: list[Card] = field(default_factory=list)


@dataclass
class LastJudgment:
    """Ögonblicksbild som gör att en dömning kan felbedömas och återställas."""
    judge_uid: int
    reason: str
    hands: dict[int, list[Card]]
    draw_pile: list[Card]
    pile: list[Card]
    finished: bool
    winner: int | None
    last: LastAction | None
    seq: int


@dataclass
class Game:
    channel_id: int
    host_id: int
    players: list[int] = field(default_factory=list)  # i den ordning man "sitter i cirkeln"
    names: dict[int, str] = field(default_factory=dict)
    keepers: set[int] = field(default_factory=set)  # regelhållare (får dela ut straff)
    hands: dict[int, list[Card]] = field(default_factory=dict)
    draw_pile: list[Card] = field(default_factory=list)
    pile: list[Card] = field(default_factory=list)
    started: bool = False
    finished: bool = False
    winner: int | None = None
    effects: dict[str, str] = field(default_factory=dict)  # valör -> effekt
    last: LastAction | None = None
    seq: int = 0  # räknas upp vid varje drag; knappar på gamla bordsmeddelanden blir ogiltiga
    last_judgment: LastJudgment | None = None
    table_msg: Any = None  # senaste bordsmeddelandet i Discord
    hand_ix: dict[int, tuple[Any, int]] = field(default_factory=dict)  # spelare -> (interaction, sida) för live-uppdatering

    def __post_init__(self) -> None:
        self.keepers.add(self.host_id)

    # ---------- uppställning ----------
    def add_player(self, uid: int, name: str) -> bool:
        self.names[uid] = name
        if uid in self.players or (self.started and not self.finished):
            return False
        self.players.append(uid)
        return True

    def start(self, cards_each: int = 5) -> str | None:
        """Blandar cirkelns ordning, delar ut och startar en runda. Returnerar felmeddelande eller None."""
        if len(self.players) < 2:
            return "Det behövs minst två spelare."
        decks = 1 if len(self.players) * cards_each <= 26 else 2
        if len(self.players) * cards_each > decks * 52 - 10:
            return "För många kort per spelare för så många spelare."
        random.shuffle(self.players)
        deck = [Card(s, r, d) for d in range(decks) for s in SUITS for r in RANKS]
        random.shuffle(deck)
        self.hands = {p: [deck.pop() for _ in range(cards_each)] for p in self.players}
        self.pile = []
        self.draw_pile = deck
        self.started, self.finished, self.winner = True, False, None
        self.last = None
        self.last_judgment = None
        self.seq += 1
        return None

    # ---------- hjälpare ----------
    @property
    def top(self) -> Card | None:
        return self.pile[-1] if self.pile else None

    def name(self, uid: int) -> str:
        return self.names.get(uid, str(uid))

    def order_text(self, with_counts: bool = True) -> str:
        parts = [f"{self.name(p)} ({len(self.hands[p])})" if with_counts else self.name(p) for p in self.players]
        return " → ".join(parts) + " ↺"

    def sorted_hand(self, uid: int) -> list[Card]:
        return sorted(self.hands.get(uid, []), key=lambda c: (SUITS.index(c.suit), RANKS.index(c.rank), c.deck))

    def _next_after(self, uid: int) -> int:
        return self.players[(self.players.index(uid) + 1) % len(self.players)]

    def _draw(self, uid: int, n: int = 1) -> list[Card]:
        drawn: list[Card] = []
        for _ in range(n):
            if not self.draw_pile and len(self.pile) > 1:
                rest, self.pile = self.pile[:-1], [self.pile[-1]]
                random.shuffle(rest)
                self.draw_pile = rest
            if not self.draw_pile:
                break
            card = self.draw_pile.pop()
            self.hands[uid].append(card)
            drawn.append(card)
        return drawn

    def _active(self, uid: int) -> bool:
        return self.started and not self.finished and uid in self.players

    def _judgment_snapshot(self, judge_uid: int, reason: str) -> LastJudgment:
        return LastJudgment(
            judge_uid=judge_uid,
            reason=reason,
            hands={uid: list(hand) for uid, hand in self.hands.items()},
            draw_pile=list(self.draw_pile),
            pile=list(self.pile),
            finished=self.finished,
            winner=self.winner,
            last=self.last,
            seq=self.seq,
        )

    def undo_last_judgment(self, uid: int) -> Outcome:
        if uid not in self.players:
            return Outcome(False, private="Bara spelare i spelet kan göra en felbedömning.")
        judgment = self.last_judgment
        if judgment is None:
            return Outcome(False, private="Det finns ingen dömning att felbedöma.")
        self.hands = {player: list(hand) for player, hand in judgment.hands.items()}
        self.draw_pile = list(judgment.draw_pile)
        self.pile = list(judgment.pile)
        self.finished = judgment.finished
        self.winner = judgment.winner
        self.last = judgment.last
        self.seq = judgment.seq + 1
        judge_name = self.name(judgment.judge_uid)
        reason = judgment.reason
        drawn = self._draw(judgment.judge_uid, 1)
        self.last_judgment = None
        penalty = " får ett straffkort för felbedömningen." if drawn else ", men leken är tom och inget straffkort kunde dras."
        return Outcome(
            True,
            event=f"Felbedömning: **{judge_name}**s dömning (*{reason}*) har ångrats.{penalty}",
        )

    # ---------- handlingar ----------
    def play(self, uid: int, card_id: str) -> Outcome:
        if not self._active(uid):
            return Outcome(False, private="Du är inte med i ett pågående spel.")
        card = next((c for c in self.hands[uid] if c.id == card_id), None)
        if card is None:
            return Outcome(False, private="Du har inte det kortet längre.")
        self.hands[uid].remove(card)
        self.pile.append(card)
        action = LastAction("play", uid, card)
        effect = self.effects.get(card.rank)
        if effect in EFFECTS:  # effekter annonseras inte: de ska man lista ut själv
            action.victim = self._next_after(uid)
            action.victim_cards = self._draw(action.victim, int(effect[-1]))
        self.last, self.seq = action, self.seq + 1
        self.last_judgment = None

        event = f"**{self.name(uid)}** la {card.label}."
        if not self.hands[uid]:
            self.finished, self.winner = True, uid
            event += f"\n🏆 **{self.name(uid)}** har inga kort kvar och vinner!"
        return Outcome(True, event=event)

    def draw(self, uid: int) -> Outcome:
        if not self._active(uid):
            return Outcome(False, private="Du är inte med i ett pågående spel.")
        drawn = self._draw(uid, 1)
        self.last, self.seq = LastAction("draw", uid), self.seq + 1
        self.last_judgment = None
        if not drawn:
            return Outcome(True, private="Leken är tom.", event=f"**{self.name(uid)}** försökte dra men leken är slut.")
        return Outcome(True, private=f"Du drog {drawn[0].label}.", event=f"**{self.name(uid)}** drog ett kort.")

    def pass_turn(self, uid: int) -> Outcome:
        if not self._active(uid):
            return Outcome(False, private="Du är inte med i ett pågående spel.")
        self.last, self.seq = LastAction("pass", uid), self.seq + 1
        self.last_judgment = None
        return Outcome(True, private="Du passade.", event=f"**{self.name(uid)}** passade.")

    def penalize_last(self, seq: int, by_uid: int, reason: str = "Inte din tur", target: int | None = None) -> Outcome:
        """Tar tillbaka det senaste draget (om det var ett kort) och ger spelaren ett straffkort."""
        if not self.started:
            return Outcome(False, private="Inget pågående spel.")
        if by_uid not in self.players:
            return Outcome(False, private="Bara spelare i spelet kan döma.")
        if self.last is None or seq != self.seq:
            return Outcome(False, private="Det har hänt något sedan dess, så det går inte att ta tillbaka längre.")
        act = self.last
        if target is not None and act.uid != target:
            return Outcome(False, private="Den spelaren gjorde inte det senaste draget.")
        if act.uid == by_uid:
            return Outcome(False, private="Du kan inte döma dig själv.")
        snapshot = self._judgment_snapshot(by_uid, reason)
        if act.kind == "play" and act.card is not None:
            self.pile.pop()  # kortet går tillbaka till spelaren
            self.hands[act.uid].append(act.card)
            if act.victim is not None:  # ångra en eventuell effekt
                for c in act.victim_cards:
                    self.hands[act.victim].remove(c)
                self.draw_pile.extend(act.victim_cards)
            self.finished, self.winner = False, None
        self.last, self.seq = None, self.seq + 1
        self._draw(act.uid, 1)
        self.last_judgment = snapshot
        extra = " Kortet tas tillbaka." if act.kind == "play" else ""
        return Outcome(True, event=f"**{self.name(act.uid)}** får ett straffkort: *{reason}*.{extra}")
