"""Spellogik för Mao. Inget Discord-beroende här, så den går att testa separat."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

SUITS = "SHDC"  # spader, hjärter, ruter, klöver
SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

# Medvetet breda anledningar, så att man inte förstår exakt vilken regel som bröts.
REASONS = ["Inte din tur", "Pratade", "Fel kort", "Glömde något", "Sa fel sak", "För långsam", "Gjorde fel"]

# Hemliga effekter som regelhållaren kan koppla till en valör.
EFFECTS = {
    "skip": "nästa spelare hoppas över",
    "reverse": "spelriktningen byts",
    "draw2": "nästa spelare drar 2 kort och hoppas över",
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
class Game:
    channel_id: int
    host_id: int
    players: list[int] = field(default_factory=list)
    names: dict[int, str] = field(default_factory=dict)
    keepers: set[int] = field(default_factory=set)  # regelhållare (får dela ut straff)
    hands: dict[int, list[Card]] = field(default_factory=dict)
    draw_pile: list[Card] = field(default_factory=list)
    pile: list[Card] = field(default_factory=list)
    turn: int = 0
    direction: int = 1
    started: bool = False
    finished: bool = False
    winner: int | None = None
    effects: dict[str, str] = field(default_factory=dict)  # valör -> effekt
    table_msg: Any = None  # senaste bordsmeddelandet i Discord

    def __post_init__(self) -> None:
        self.keepers.add(self.host_id)

    # ---------- uppställning ----------
    def add_player(self, uid: int, name: str) -> bool:
        self.names[uid] = name
        if uid in self.players or self.started and not self.finished:
            return False
        self.players.append(uid)
        return True

    def start(self, cards_each: int = 7) -> str | None:
        """Delar ut och startar en runda. Returnerar felmeddelande eller None."""
        if len(self.players) < 2:
            return "Det behövs minst två spelare."
        decks = 1 if len(self.players) * cards_each <= 26 else 2
        if len(self.players) * cards_each > decks * 52 - 10:
            return "För många kort per spelare för så många spelare."
        deck = [Card(s, r, d) for d in range(decks) for s in SUITS for r in RANKS]
        random.shuffle(deck)
        self.hands = {p: [deck.pop() for _ in range(cards_each)] for p in self.players}
        self.pile = [deck.pop()]
        self.draw_pile = deck
        self.turn, self.direction = 0, 1
        self.started, self.finished, self.winner = True, False, None
        return None

    # ---------- hjälpare ----------
    @property
    def current(self) -> int:
        return self.players[self.turn]

    @property
    def top(self) -> Card:
        return self.pile[-1]

    def name(self, uid: int) -> str:
        return self.names.get(uid, str(uid))

    def sorted_hand(self, uid: int) -> list[Card]:
        return sorted(self.hands.get(uid, []), key=lambda c: (SUITS.index(c.suit), RANKS.index(c.rank), c.deck))

    def _advance(self, steps: int = 1) -> None:
        self.turn = (self.turn + self.direction * steps) % len(self.players)

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

    # ---------- handlingar ----------
    def penalty(self, uid: int, reason: str) -> Outcome:
        self._draw(uid, 1)
        return Outcome(False, private=f"Straffkort: {reason}", event=f"**{self.name(uid)}** får ett straffkort: *{reason}*")

    def play(self, uid: int, card_id: str) -> Outcome:
        if self.finished or not self.started or uid not in self.players:
            return Outcome(False, private="Du är inte med i ett pågående spel.")
        if uid != self.current:
            return self.penalty(uid, "Inte din tur")
        card = next((c for c in self.hands[uid] if c.id == card_id), None)
        if card is None:
            return Outcome(False, private="Du har inte det kortet längre.")
        if not card.matches(self.top):
            return self.penalty(uid, "Fel kort")

        self.hands[uid].remove(card)
        self.pile.append(card)
        event = f"**{self.name(uid)}** la {card.label}."
        if not self.hands[uid]:
            self.finished, self.winner = True, uid
            return Outcome(True, event=f"{event}\n🏆 **{self.name(uid)}** har inga kort kvar och vinner!")

        steps = 1
        effect = self.effects.get(card.rank)
        if effect == "reverse":
            self.direction *= -1
        elif effect == "skip":
            steps = 2
        elif effect == "draw2":
            self._advance(1)
            self._draw(self.current, 2)
        self._advance(steps)  # effekter annonseras inte: de ska man lista ut själv
        return Outcome(True, event=event)

    def draw(self, uid: int) -> Outcome:
        if self.finished or not self.started or uid not in self.players:
            return Outcome(False, private="Du är inte med i ett pågående spel.")
        if uid != self.current:
            return self.penalty(uid, "Inte din tur")
        drawn = self._draw(uid, 1)
        self._advance(1)
        if not drawn:
            return Outcome(True, private="Leken är tom.", event=f"**{self.name(uid)}** försökte dra men leken är slut.")
        return Outcome(True, private=f"Du drog {drawn[0].label}.", event=f"**{self.name(uid)}** drog ett kort.")
