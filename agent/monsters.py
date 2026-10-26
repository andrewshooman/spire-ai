"""What an enemy does NEXT turn (A0), from the monster AI in the Slay the Spire reference spreadsheet
(game version 2.2). The game only shows this turn's intent; a few choices need the turn after:
ending a turn in Wrath doubles the next turn's hits too (a Watcher ended Hexaghost's quiet first turn in
Wrath and took 72 from Divider), and potions/setup are better spent before a known big hit.

next_attack() is the expected attack total (damage x hits, before Wrath / Vulnerable) of the monster's
next move: exact for fixed patterns, the monster's average attack otherwise."""
from __future__ import annotations

# average attack total (damage x hits) of each monster's attack moves at A0, from the reference
TYPICAL = {
    "Acid Slime (L)": 13.5, "Acid Slime (M)": 8.5, "Acid Slime (S)": 3.0, "Awakened One": 26.4, "Bear": 13.5,
    "Blue Slaver": 9.5, "Book of Stabbing": 13.5, "Bronze Automaton": 29.5, "Bronze Orb": 8.0, "Byrd": 6.7,
    "Centurion": 15.0, "Chosen": 12.7, "Corrupt Heart": 32.0, "Cultist": 6.0, "Dagger": 17.0, "Darkling": 16.0,
    "Deca": 20.0, "Donu": 20.0, "Exploder": 9.0, "Fat Gremlin": 4.0, "Fungi Beast": 6.0, "Giant Head": 21.5,
    "Gremlin Leader": 18.0, "Gremlin Nob": 10.0, "Gremlin Wizard": 25.0, "Hexaghost": 9.3, "Lagavulin": 18.0,
    "Looter": 11.0, "Mad Gremlin": 4.0, "Mugger": 13.0, "Mystic": 8.0, "Nemesis": 31.5, "Orb Walker": 12.5,
    "Pointy": 10.0, "Red Slaver": 10.5, "Reptomancer": 28.0, "Repulsor": 11.0, "Romeo": 12.5, "Sentry": 9.0,
    "Shelled Parasite": 15.0, "Shield Gremlin": 6.0, "Slime Boss": 35.0, "Snake Plant": 21.0,
    "Sneaky Gremlin": 9.0, "Snecko": 11.5, "Spheric Guardian": 13.3, "Spike Slime (L)": 16.0,
    "Spike Slime (M)": 8.0, "Spike Slime (S)": 5.0, "Spiker": 7.0, "Spire Growth": 19.0, "Spire Shield": 23.0,
    "Spire Spear": 20.0, "Taskmaster": 7.0, "The Champ": 14.0, "The Collector": 18.0, "The Guardian": 20.3,
    "The Maw": 15.0, "Time Eater": 23.5, "Torch Head": 7.0, "Transient": 30.0, "Writhing Mass": 22.7,
    # rows the spreadsheet export splits oddly
    "Jaw Worm": 9.0, "Louse": 6.0, "Red Louse": 6.0, "Green Louse": 6.0, "Gremlin Leader ": 18.0,
}


def _intent(m) -> str:
    v = getattr(m, "intent", None)
    return str(getattr(v, "value", v) or "")


def next_attack(m, turn: int, player_hp: int, cur_total: float) -> float:
    """Expected attack total of `m`'s next move. `cur_total` = this turn's attack total (0 if not attacking),
    `turn` = the combat turn now (1 = first)."""
    name, intent = m.name, _intent(m)
    attacking = cur_total > 0
    strength = 0
    for p in getattr(m, "powers", []) or []:
        if getattr(p, "id", "") == "Strength" or getattr(p, "name", "") == "Strength":
            strength = getattr(p, "amount", 0) or 0
    if name == "Hexaghost":
        # Activate (turn 1) -> Divider: (your HP / 12 + 1) x 6; then Sear 6 / Tackle 5x2 / Inflame / Inferno 2x6
        if turn <= 1 and not attacking:
            return (player_hp // 12 + 1) * 6.0
        return 9.3 + 2 * strength
    if name == "Slime Boss":
        # Goop Spray -> Preparing -> Slam 35, always in that order
        return 35.0 if not attacking and intent in ("UNKNOWN", "NONE", "") else 0.0
    if name == "The Guardian":
        # Charge Up (block 9) -> Fierce Bash 32 -> Vent Steam (Weak/Vuln) -> Whirlwind 5x4;
        # defensive: Close Up -> Roll Attack 9 -> Twin Slam 8x2
        if intent == "DEFEND":
            return 32.0
        if intent in ("DEBUFF", "STRONG_DEBUFF"):
            return 20.0
        if intent == "BUFF":
            return 9.0
        if attacking and cur_total <= 10:
            return 16.0    # Roll Attack -> Twin Slam
        return 9.0 if cur_total >= 30 else 0.0   # Fierce Bash -> Vent Steam; Whirlwind -> Charge Up
    if name == "Bronze Automaton":
        # Spawn Orbs, then Flail 7x2 -> Boost -> Flail -> Boost -> HYPER BEAM 45 -> Stunned
        if turn <= 1:
            return 14.0 + 2 * strength
        if not attacking:
            return 0.0 if intent == "STUN" else 30.0 + strength   # after a Boost: Flail or Hyper Beam
        return 0.0                                               # after Flail: Boost; after Hyper Beam: Stunned
    if name == "Gremlin Nob" and turn <= 1:
        return 14.0 + strength      # Bellow -> Rush
    if name == "Lagavulin":
        return 0.0 if intent == "SLEEP" else 18.0 + strength
    if name == "Transient":
        return cur_total + 10.0     # 30, 40, 50...
    if attacking:
        return cur_total            # the next hit will look much like this one
    base = TYPICAL.get(name)
    if base is None:
        return 0.0 if intent in ("SLEEP", "STUN") else 6.0
    return base + strength
