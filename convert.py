#!/usr/bin/env python3
"""
Trench Crusade roster -> TTS converter.

Usage:
  convert.py <file.rosz>                  Convert single file, print to stdout
  convert.py <file.rosz> --per-model      Write one .txt per model/rule into output/
  convert.py <folder/>                    Batch-convert all .rosz files in folder
  convert.py <folder/> --per-model        Per-model output for every .rosz in folder
  convert.py <file.rosz> --watch          Watch for changes and auto-convert
  convert.py <file.rosz> --verbose        Full ability text (no compression)
"""

import zipfile
import gzip
import os
import sys
import re
import argparse
import time
from pathlib import Path
from xml.etree import ElementTree as ET

# Fix Windows stdout encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

NS = "http://www.battlescribe.net/schema/rosterSchema"

# ── Faction ability dictionaries (auto-generated from .cat files) ─────────────
# Import faction_abilities.py if present (run build_dict.py to regenerate).
# Falls back to empty dicts so the converter works without data files.
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from faction_abilities import FACTION_ABILITIES, ALL_ABILITIES, WARBAND_VARIANTS, WEAPON_KEYWORDS
    _HAS_FACTION_DATA = True
except ImportError:
    FACTION_ABILITIES  = {}
    ALL_ABILITIES      = {}
    WARBAND_VARIANTS   = {}
    WEAPON_KEYWORDS    = {}
    _HAS_FACTION_DATA  = False

# ── TTS colour codes ──────────────────────────────────────────────────────────

HEADER   = "[fde047]"
STAT_CLR = "[93c5fd]"
WEAP_CLR = "[86efac]"
ABIL_CLR = "[f9a8d4]"
RULE_CLR = "[fcd34d]"
RESET    = "[-]"
SUP_O    = "[sup]"
SUP_C    = "[/sup]"

MAX_ABILITY_LINE = 180   # chars before truncation in compressed mode


def h(text):
    return f"{HEADER}{text}{RESET}"


def small(text):
    return f"{SUP_O}{text}{SUP_C}"


# ── Ability compression engine ────────────────────────────────────────────────

# Known abilities: name -> short description.
# Full multi-faction ability lookup. Maps ability name -> concise TTS summary.
# Lookup is case-insensitive (see compress_ability).
KNOWN_ABILITIES = {
    # ── Universal keywords / common abilities ──────────────────────────────
    "Fear":                         "Attackers: -1 DICE melee vs this model. Immune to FEAR.",
    "Tough":                        "First OOA result treated as Down instead.",
    "Strong":                       "+1 INJ MOD on melee attacks.",
    "Impervious":                   "Cannot gain status markers (BLOOD, INFECTION, etc.).",
    "Infiltrator":                  "Deploys after opponents anywhere >6\" from enemies.",
    "Scout":                        "Free move before the first activation of the game.",
    "Levitate":                     "Ignores terrain height for movement.",
    "Abiotic Life":                 "Immune to BLOOD/INFECTION MARKERS.",
    "Leader":                       "This model leads the warband.",
    "Negate Gas":                   "Immune to GAS keyword effects.",
    "Negate Shrapnel":              "Immune to SHRAPNEL keyword effects.",
    "Negate Fire":                  "Immune to FIRE keyword effects.",
    "Negate Fear":                  "Immune to FEAR effects.",
    "Ignore Off-Hand":              "No penalty for dual-wielding.",
    "Skirmisher":                   "Can move through models; ignores engagement zone.",
    "Agile":                        "+1 DICE risky roll for Climb/Jump/Diving Charge/Dash.",

    # ── New Antioch ────────────────────────────────────────────────────────
    "Hold Your Fire!":              "Lieutenant ACTION: Pick 1 enemy in LoS — opponent must Activate it next.",
    "Absolute Faith":               "Sniper Priest: Opponent's BLOOD MARKERS don't penalise ranged attacks vs this model.",
    "Aim":                          "Sniper Priest ACTION: Risky +2 DICE; Success = +2 DICE to ranged attacks rest of Activation.",
    "God is With Us!":              "Trench Cleric ACTION: Risky roll; Success = 1 BLESSING MARKER on self or friendly within 6\".",
    "Onward Christian Soldiers!":   "Friendly New Antioch within 8\" of Trench Cleric gain NEGATE FEAR.",
    "Assault Drill":                "Shock Trooper: Ignore HEAVY on 1 Melee Weapon.",
    "Shock Charge":                 "Shock Trooper Charge Bonus: Roll extra D6, use single highest.",
    "Battlefield Demolition":       "Combat Engineer: Ignore HEAVY on 1 Satchel Charge.",
    "Set Mine":                     "Combat Engineer ACTION: +2 DICE roll; Success = terrain gains MINED.",
    "Defuse Mine":                  "Moving into MINED terrain: risky roll; Success removes MINED.",
    "Fortify":                      "ACTION: Risky roll; Success = gains COVER until you move.",
    "Expert Medic":                 "+2 DICE to risky roll for Treat ACTION.",
    "Finish the Fallen":            "+1 INJ DICE on melee attacks vs Down non-BLACK GRAIL/DEMONIC targets.",
    "Loudspeakers":                 "War Prophet ACTION: Risky +2 DICE; Success = all friendly within 8\" move 3\" toward nearest visible enemy.",
    "Laying on of Hands":           "War Prophet ACTION: Success removes 1 BLOOD from friendly within 6\"; Critical removes 3.",
    "Memento Mori":                 "War Prophet: First OOA = No Effect instead. Cannot have TOUGH.",

    # ── Trench Pilgrims ────────────────────────────────────────────────────
    "Bodyguard":                    "Within 1\" of a friendly model: intercept attacks (not BLAST).",
    "Resurrection":                 "Pilgrim killed in campaign: resurrects as Martyr Penitent (45D) with -1 INJ DICE.",
    "Blessed Stigmata":             "Each BLOOD removed by REGENERATE 1 places 1 BLESSING MARKER.",
    "Feeble Flailing":              "Can melee attack without weapon at -1 DICE.",
    "Mad Dash":                     "+1 DICE risky roll for Dash ACTION.",
    "Broken on the Wheel":          "START: Choose 1 Pilgrim/Prisoner permanently on the Wheel — injuries go to them until death.",
    "Symphony of Slaughter":        "2 Melee Weapons; attack once or twice (first Wheel, second Mace as Off-Hand).",
    "Arise and be Healed!":         "Papal States Trench Cleric ACTION: Risky roll; Success = model stands and remove up to D3 BLOOD/INFECTION within 3\".",

    # ── Iron Sultanate ────────────────────────────────────────────────────
    "Janissary Veteran":            "Yüzbaşı upgrade: gains STRONG and Counter-Charge.",
    "Counter-Charge":               "If first ACTION is Charge: +1 DICE melee attacks rest of Activation.",
    "Mubarizun":                    "+1 INJ DICE vs targets with TOUGH.",
    "Mastery of the Elements":      "Deploy: Give all weapons FIRE or GAS or SHRAPNEL (all must match).",
    "Elemental Change":             "ACTION: Risky roll; Success changes Mastery element.",
    "Temporal Assassin":            "Charge: 2 targets — charge first, Fight, redeploy within 1\" of second, Fight again.",
    "Time Slip":                    "If opponent fails attack: redeploy within 6\", >1\" from enemies.",
    "Light Skirmishers":            "Any number of Azebs gain SKIRMISHER for +5 each.",
    "Forward Positions":            "Sapper DEPLOY: Up to 6\" from zone in contact with terrain ≥½\" tall.",
    "Artificial Life":              "-1 INJ DICE on all injury rolls (Golems/constructs).",
    "Teeth and Claws":              "Can make melee attacks without a weapon.",
    "Pin":                          "Enemy Down models on ≤40mm base cannot stand if within 1\".",
    "Trample":                      "ACTION: Melee Attack vs Down enemy only; no weapon, IGNORE ARMOUR.",
    "Ammunition Sacrament":         "Mendelist ACTION: Risky roll; Success = 1 of 4 Sacrament bonuses to friendly within 1\" until next Activation.",
    "Faithful Followers":           "When activating a model within 1\", can also activate Mendelist (if not yet activated).",
    "Martial Prowess":              "Mamluk: Greatsword loses HEAVY; Jezzail gains ASSAULT and Shield Combo.",
    "Sworn Brethren":               "Mamluk: Can form FIRETEAM with 1 other ELITE in addition to normal FIREATEAMs.",
    "Automaton Destrier":           "Mamluk Faris: Deploys within 1\" of battlefield edge >8\" from enemies, after Infiltrators.",
    "Eye of God":                   "Observer: Re-roll failed rolls; any 1s = Failure, taken Down, Activation ends.",
    "Lightning Speed":              "Observer: Polearm gains CLEAVE 2.",
    "Temporal Fugue":               "-1 DICE to ranged/melee targeting this model.",
    "Voice of God":                 "Observer ACTION: Risky roll; Success = pick 1 unActivated model anywhere; their Activation begins.",

    # ── Heretic Legions ───────────────────────────────────────────────────
    "Puppet Master":                "Heretic Priest ACTION: Risky roll; Success = move 1 model within 12\" D6\" in any direction.",
    "Stealth Generator":            "Death Commando: -1 DICE to ranged attacks targeting it.",
    "Hide":                         "ACTION: Risky +1 DICE; Success = cannot be chosen target until it moves or charges.",
    "Unholy Hymns":                 "-1 DICE to enemy Success Rolls within 8\" while 1+ Choristers active.",
    "Heretic Legionnaires":         "Up to 1 Trooper per Trooper upgrade: +10D; change Ranged OR Melee to +1.",
    "Assault Beast":                "War Wolf: 2 Melee Weapons (Chainsaw/Claws); attack once or twice (Claws Off-Hand).",
    "Loping Dash":                  "+1 DICE risky roll for Dash ACTION.",
    "Chattel":                      "Can sell in Quartermaster Step for 25D + half Battlekit cost.",
    "Dark Blessing":                "When this model goes OOA: place 1 BLESSING MARKER on nearest friendly ELITE HERETIC.",

    # ── Court of the Seven-Headed Serpent ──────────────────────────────────
    "Blood Magic":                  "Spell (Cost 1): Before injury rolls, +1 INJ DICE to all rolls for this attack.",
    "Blessing of the Serpent Moon": "Spell (2/4/6 Cost): Before injury roll, -1 INJ MOD per 2 BLOOD spent.",
    "Left-Hand Path":               "Spell (Cost 2): Move into terrain contact; redeploy within 6\" of terrain >1\" from enemies.",
    "Oracle Beast Cloak":           "Spell (Cost 3): 1x/turn after Injury Roll, change result to No Effect.",
    "Shadow-walker":                "Spell (Cost 2): Before Retreat, enemy cannot melee attack on retreat.",
    "Shadow Walker":                "-2 DICE to ranged attacks targeting this model at Long Range (instead of -1 DICE).",
    "Charge of Hatred":             "Charge counts as Move 12\". No D6 Charge Bonus roll; ignore that rule entirely.",
    "Lesser Mark of Cain":          "This model has the -1 INJURY DICE Keyword.",
    "Aura of Wrath":                "+1 DICE melee and Dash risky rolls for friendly within 8\" (incl. self).",
    "Aura of Lust":                 "Enemy within 4\" wearing non-IMPERVIOUS Armour: negate any Armour Keywords/rules.",
    "Aura of Pride":                "When Activation ends: place 1 BLOOD MARKER on each enemy within 8\".",
    "Aura of Sloth":                "Enemy within 8\" treat Minor Hit as Down.",
    "Aura of Gluttony":             "-1 DICE to enemy within 8\" (not BLACK GRAIL/ARTIFICIAL).",
    "Aura of Envy":                 "Enemy within 12\" cannot Charge a friendly within 1\" of its own warband.",
    "Aura of Greed":                "Enemy within 12\" taking Charge must charge this model if in LoS and reachable.",
    "Annihilator":                  "Desecrated Saint: 1 Fight ACTION per Melee Weapon per Activation.",
    "Law of Hell":                  "Kill ELITE enemy = removed from game; doesn't count for Morale.",
    "Torturer":                     "Can melee attack a friendly non-DEMONIC within 1\". Cannot attack again same Activation.",
    "Forbidden Pleasures":          "Per model with this ability: place 3 BLOOD MARKERS on 1 non-DEMONIC friendly before deployment.",
    "Barbed Embrace":               "Enemy within 1\" cannot Retreat.",
    "Iron-Clawed Hands":            "Melee attack with CLEAVE 2 and CRITICAL without weapon; Off-Hand applies.",
    "Goetic Gaze":                  "ACTION: Success roll; Success = 1 BLOOD on enemy within 24\" LoS (2 on Critical).",
    "Goetic Portal":                "ACTION: Risky +1 DICE; Success = redeploy within 6\"; can bring ≤32mm enemy.",
    "Charm of Acedia":              "Spell (Cost 1) ELITE: Next ACTION's first Success Roll auto-succeeds.",
    "Daemonium Meridianum":         "ELITE: Open/Dangerous terrain within 6\" treated as DIFFICULT.",
    "Morphean Mind":                "ELITE: Opponent can spend max 1 BLOOD for -1 DICE on Success Rolls.",
    "Belly of the Beast":           "After melee attack, place 1 BLOOD on attacker if target gained ≥1 BLOOD.",
    "Eater of the Flesh":           "After melee attack, remove 1 BLOOD per BLOOD placed on target (not BLACK GRAIL/DEMONIC).",
    "Uncaring Gluttony":            "Spell (Cost 2) ELITE: Pick 1 unActivated enemy; 1 Equipment rendered unusable.",
    "Black Heart":                  "Spell (Cost 1) ELITE: Before a roll, +1 DICE.",
    "Body of Gold":                 "ELITE: Model gains GOLEM; loses/cannot gain TOUGH.",
    "Greedy Hearts":                "ELITE: After deployment, place 1 BLESSING per enemy costing ≥150D.",
    "Battlefield Vivisection":      "Glorious Deed: Take 3+ enemies OOA while within 1\" of each.",
    "Prize Specimens":              "Melee OOA vs DEMONIC/BLACK GRAIL: place 1 BLESSING MARKER.",
    "Iron Fists":                   "Melee attack with CLEAVE 2 without weapon; Off-Hand applies.",
    "Exquisite Pain":               "Spell (1-2 Cost): Pick model in LoS; place BLOOD equal to cost spent next to it.",
    "Devour the Guilty":            "ACTION: Pick friendly or enemy ≤40mm within 1\"; Risky roll to devour them.",
    "Pit Locust Sting":             "Melee attack with CLEAVE 2 and SHRAPNEL without weapon.",
    "Burning Inferno":              "Spell (Cost 2): BLAST, FIRE, SCATTER ranged attack 36\".",
    "Slavemaster":                  "Ranged 18\" — see weapon profile for targeting.",

    # ── Black Grail (all variants) ─────────────────────────────────────────
    "Undead Fortitude":             "-1 INJ DICE on injury rolls (not vs FIRE).",
    "Infernal Iron Armour":         "[Armour] -2 INJ MOD, IMPERVIOUS.",
    "Noble Hierarchy":              "Enemy warbands roll Morale at -1 DICE. Court Warbands and Black Grail ignore this.",
    "Frenzied Followers":           "+1 DICE risky roll for Dash actions by friendly within 8\" of this model.",
    "Cadre of Flesh":               "Non-BLAST hits on this model: redirect injury to a Ravenous within 4\". BLAST: take roll first.",
    "Pestilent":                    "Can melee attack with INFECTION MARKERS + CRITICAL without weapon. Hits add 1 extra INF MARKER.",
    "Overwhelming Horde":           "Can melee attack without weapon. +1 DICE per other friendly within 3\" (not self).",
    "Ravenous Infection":           "ACTION: Risky roll. Success/Crit: place 1 INF MARKER on any model within 1\". Activation ends either way.",
    "Cradle of Filth":              "Melee attacks gain CRITICAL.",
    "Unending Horde":               "Does not count toward Maximum Field Strength.",
    "Unending Starvation":          "[Equipment] +1\" Move. Can target self with Ravenous Infection.",
    "Dormant Hunger":               "When would go OOA: go Dormant instead. Remove all INF MARKERs; replace with 60mm marker. Returns if INF placed on marker.",
    "Plague-Ridden Flesh":          "-2 INJ DICE on injury rolls (not vs FIRE).",
    "Knight Companion of the Feast":"Nearby friendly models: +1 DICE risky roll for Ravenous Infection if within 3\".",
    "Knight of Twin Cleavers":      "Gains IGNORE OFF-HAND. Melee attacks gain SHRAPNEL.",
    "Butcher King":                 "Campaign: if not OOA and took 1+ enemy OOA with melee, gain 1 Glory.",
    "Beelzebub's Touch":            "Lord of Tumours: Melee attacks that place markers add 1 extra INF MARKER.",
    "Crushing Blows":               "Melee attack without weapon with CLEAVE 2.",
    "Parasite Host":                "Corpse Guard: Melee attack that places markers also removes 1 BLOOD/INFECTION from self.",
    "Disease Carrier":              "Enemy Activated within 1\": gains 1 INF MARKER before their actions.",
    "Frightening Speed":            "+1 DICE Dash risky roll. Don't halve Movement when standing up.",
    "Infected Proboscis":           "Melee attack with INFECTION MARKERS without weapon. Hit removes 1 BLOOD from target.",
    "Maddening Buzzing":            "Enemy Success Rolls within 8\" become Risky.",
    "Six-armed Monstrosity":        "1 Shoot per Ranged Weapon + 1 Fight per Melee Weapon per Activation; no Off-Hand.",
    "Corpulent":                    "-2 INJ DICE on all injury rolls targeting this model.",
    "Unstoppable":                  "≤32mm models cannot melee attack it on retreat. Can Move/Charge if only ≤32mm nearby.",
    "Grail Devotee":                "+1 INJ MOD per Devotee to melee attacks. Up to 2 per model.",
    "Gnashing and Tearing":         "Fight ACTION: make 2 melee atk without weapon (+1 INJ DICE, CLEAVE 2; CLEAVE 3 on Charge).",
    "Gluttonous Horde":             "Melee atk CRITICAL without weapon. +1 DICE per other friendly within 3\" (not self).",

    # ── Great Hunger Strains ───────────────────────────────────────────────
    "Devouring Jaws":               "[Strain] Devour ACTION: melee atk CRITICAL without weapon; if not OOA, place 1 BLOOD on self.",
    "Grasping Maw":                 "[Strain] Grasp ACTION: risky; Success = pull enemy in LoS within 12\" up to 3\" toward self.",
    "Hellfly Host":                 "[Strain] Replace Move with 6\"/Flying; lose Undead Fortitude.",
    "Lockjaw Bite":                 "[Strain] Enemy retreating within 1\": place 1 INF MARKER before retreat attacks.",
    "Papillal Hide":                "[Strain] No LoS required for Charge ACTION.",
    "Rotten Cutters":               "[Strain] Melee attacks gain CLEAVE 2.",
    "More Worm Than Man":           "Opponent cannot spend this model's INF MARKERS (except Bloodbath rolls).",
    "Rapturous Feast":              "Friendly within 4\" of spellcaster: melee atk gain INFECTION MARKERS if charged this Activation.",

    # ── Great Hunger Warband Special Rules ───────────────────────────────────
    "Arcana Putrescere":            "Hag: up to 3 Powers. Lord of Tumours: 1. Gregori Gula: 1.",
    "Butcher Knights":              "Plague Knights get Ravenous Infection free; use GH Plague Knight Ranks instead of standard ones.",
    "Cradle Thralls":               "0-3 Ravenous-class Infiltrators; cost 2 blood; don't count toward Max Field Strength.",
    "Desiccated Husks":             "0-2 Corpse Guard-class; melee gain CRITICAL; replace Bodyguard with More Worm Than Man.",
    "Excruciating Hunger":          "Cannot have: Beelzebub's Axe, BG Shields, Bolt-action Rifles, Blunderbusses, Compound Eyes, Gas Grenades, Infested Rifles, Machine Guns, Musical Instruments, Muskets, Pistols, Troop Flags, Viscera Cannons.",
    "Spawn of Gluttony":            "Must include 1 Matagot Hag or Lord of Tumours (not both). Cannot include Corpse Guard, Heralds, or Amalgam.",
    "The Great Maw":                "If Warband roster value ≥1000D: may recruit 0-1 Great Maw (Lord of Tumours profile, no LEADER).",

    # ── Great Hunger Turn Modes ────────────────────────────────────────────
    "Agonised Churning":            "Each Turn (if within 8\" of Hag): remove 2 INF from friendly → place on enemy within 1\".",
    "Ruinous Masticating":          "Each Turn: +1 INF on friendly within 8\" of Hag; opponent can't spend their INF for -DICE if ≥2.",
    "Spasmodic Wretching":          "Each Turn: +1 INF on friendly within 8\" of Hag; -1 DICE ranged atk vs those with ≥2 INF.",
    "Vile Craving":                 "Each Turn (if within 8\" of Hag): remove 2 INF → move up to half Move toward nearest enemy.",

    # ── Great Hunger Equipment ────────────────────────────────────────────
    "Foetid Palaquin":              "[Armour, -1 INJ MOD] Bile Clot ACTION: remove INF (friend/foe within 18\") → -1 INJ MOD/2 INF (max -3).",
    "Cup of Filth":                 "[Equipment] Pre-game: pick 1 ELITE or up to 4 Ravenous; they gain +1 DICE Dash risky rolls.",

    # ── Court Warbands / Goetic Powers ────────────────────────────────────
    "Concentration Camp":           "Yoke Fiend within 1\" of enemy: that enemy cannot Retreat.",
    "Perverse Desires":             "Court Ability: once per game, one non-DEMONIC model joins Warband as Deserter.",
    "Hateful":                      "Must Charge nearest visible non-DEMONIC/BLACK GRAIL enemy within 12\" if >1\" from all enemies.",

    # ── Heretic Legion Warband Variants ───────────────────────────────────
    "Semi-corporeal":               "-1 INJ DICE for ranged atk injury rolls vs models in this Warband.",
    "Barbed Wire Banshee":          "[Trench Ghosts] Can include Banshee instead of Chorister; replaces Unholy Hymns with +1 INJ MOD melee.",
    "Enemies of All":               "Cannot include Mercenaries.",
    "Lost Souls":                   "No ARTIFICIAL models; cannot have Hellbound Soul Contracts or Infernal Brands.",
    "Slow and Creeping":            "Dash = 3\"/Infantry. -1 DICE attacking enemies that haven't moved this Turn.",
    "Undead Horror":                "All Warband models gain FEAR, NEGATE DIFFICULT TERRAIN, NEGATE GAS.",

    # ── Tank Palanquin (Heretic Priest upgrade) ───────────────────────────
    "Death From On High":           "Tank Palanquin: +3\" to height for Elevated Position bonus checks.",
    "Bulky":                        "Tank Palanquin: 50mm base, no Shield, Charge Bonus D3\" not D6\".",
    "Standfast":                    "Down result on injury table treated as Minor Wound.",

    # ── Trench Dogs (Mercenaries) ─────────────────────────────────────────
    "Four Paws":                    "+1 DICE Climb/Jump/Fall/Dash risky rolls.",
    "Pack Loyalty":                 "Shares its owner's Faction Keyword.",
    "Dog Food":                     "[Equipment] Allows recruiting a Trench Dog from the Mercenaries section.",

    # ── Black Grail profile names used as warband rules ───────────────────
    "Special Rule: Morale":         "Enemy warbands roll Morale at -1 DICE. Court Warbands and Black Grail ignore this.",

    # ── Mercenaries / misc ────────────────────────────────────────────────
    "Inspiring Relic":              "Bearer doesn't end Activation after failed Risky Success Roll.",
    "Divine Judgement":             "Witchburner ACTION: Risky roll; Success = 1 BLOOD on enemy ≤24\" (2 if BLACK GRAIL/DEMONIC/HERETIC).",
    "Dignified Conduct":            "Cannot take Dash ACTION.",
    "Self Sacrifice":               "After hit: place up to 3 BLOOD on attacker; place 1 INFECTION per BLOOD on target.",
    "Harrowing Assault":            "Target taken Down/OOA: move model up to 3\". If within 1\" enemy, take another Fight ACTION.",
    "Enhanced Vision":              "+1 DICE ranged attack success rolls.",
    "Combat Biologist":             "Gather Knowledge deed: take 3+ enemies OOA within 1\" each.",
}


# Regex-based compression transforms (applied in order).
# Each is (pattern, replacement) for re.sub.
_COMPRESS_SUBS = [
    # Strip filler openings
    (r"^If you do so,\s*", ""),
    (r"^you can say that\s*", ""),
    (r"^you can\s*", ""),
    (r"^you must\s*", ""),
    (r"^In addition,?\s*", ""),
    (r"^Note that\s*", ""),
    (r"^Once per Turn\s*", "1x/turn: "),

    # Model references
    (r"\bthe model taking the (\w+ )?ACTION\b", "the model"),
    (r"\bthe model with this Keyword\b", "this model"),
    (r"\bA model with this (Goetic )?Ability\b", "This model"),
    (r"\bA model with this Keyword\b", "This model"),
    (r"\bfor a model with this\b", ""),
    (r"\bfor this model\b", ""),
    (r"\bfor the model\b", ""),

    # Dice language
    (r"Add \+(\d+ DICE) to\b", r"+\1 to"),
    (r"Add -(\d+ DICE) to\b", r"-\1 to"),
    (r"Add \+(\d+) INJURY MODIFIER\b", r"+\1 INJ MOD"),
    (r"Add -(\d+) INJURY MODIFIER\b", r"-\1 INJ MOD"),
    (r"Add \+(\d+) INJURY DICE\b", r"+\1 INJ DICE"),
    (r"Add -(\d+) INJURY DICE\b", r"-\1 INJ DICE"),
    (r"\+(\d+) DICE to (\w+ )?Success Rolls\b", r"+\1 DICE \2rolls"),
    (r"-(\d+) DICE to (\w+ )?Success Rolls\b", r"-\1 DICE \2rolls"),
    (r"Injury Rolls\b", "injury rolls"),
    (r"Success Roll\b", "success roll"),
    (r"Risky Success Roll\b", "risky roll"),

    # Markers
    (r"\bINFECTION MARKER(S)?\b", "INF MARKER"),
    (r"\bBLOOD MARKER(S)?\b", "BLOOD"),

    # Common phrases
    (r"unless the attack has the ([A-Z]+) Keyword", r"(not vs \1)"),
    (r"if the (?:attack|roll) has the ([A-Z]+) Keyword", r"(vs \1 only)"),
    (r"Activation ends immediately\.?\b", "Activation ends."),
    (r"If the roll is a Failure,?\s*", "Fail: "),
    (r"If the roll is a (?:Success or a )?Critical Success,?\s*", "Crit: "),
    (r"If the roll is a Success(?! or),?\s*", "Success: "),
    (r"\btake a (?:Risky )?Success Roll\b", "risky roll"),
    (r"\bOut of Action\b", "OOA"),
    (r"\bLine of Sight\b", "LoS"),
    (r"\bMelee Attack(s)?\b", "melee atk"),
    (r"\bRanged Attack(s)?\b", "ranged atk"),
    (r"\bMelee Characteristic\b", "Melee"),
    (r"\bMovement Characteristic\b", "Move"),
    (r"\bInjury Table\b", "injury table"),
    (r"\bActionable\b", "action"),
    (r"\bACTION\b", "action"),
    (r"\b(\d+)\"\b", r'\1"'),

    # Cleanup artifacts
    (r"\s{2,}", " "),
    (r"\s+\.", "."),
    (r"\s+,", ","),
]

_COMPRESS_PATTERNS = [(re.compile(p, re.IGNORECASE), r) for p, r in _COMPRESS_SUBS]


def compress_ability(name: str, text: str, faction: str = "", verbose: bool = False) -> str:
    """
    Return a compressed (or full) ability description.

    Lookup priority:
      1. Hand-crafted KNOWN_ABILITIES (always most concise)
      2. Faction-specific dict from faction_abilities.py
      3. Cross-faction ALL_ABILITIES from faction_abilities.py
      4. Regex-based compression applied to whatever text came from the roster
    """
    if verbose:
        return text

    name_lower = name.lower()

    # 1. Hand-crafted summaries
    for key, short in KNOWN_ABILITIES.items():
        if key.lower() == name_lower:
            return short

    # 2. Faction-specific dict (exact faction match)
    if faction and faction in FACTION_ABILITIES:
        for key, short in FACTION_ABILITIES[faction].items():
            if key.lower() == name_lower:
                return short

    # 3. Cross-faction merged dict
    for key, short in ALL_ABILITIES.items():
        if key.lower() == name_lower:
            return short

    if not text:
        return ""

    # 4. Regex-based compression on the text from the roster
    result = text
    for pattern, repl in _COMPRESS_PATTERNS:
        result = pattern.sub(repl, result)

    result = result.strip()

    if len(result) > MAX_ABILITY_LINE:
        cut = result.rfind(".", 0, MAX_ABILITY_LINE)
        if cut > MAX_ABILITY_LINE // 2:
            result = result[: cut + 1]
        else:
            result = result[: MAX_ABILITY_LINE].rsplit(" ", 1)[0] + "..."

    return result


# ── XML helpers ───────────────────────────────────────────────────────────────

def tag(name):
    return f"{{{NS}}}{name}"


def find(el, name):
    return el.find(tag(name))


def findall(el, name):
    return el.findall(tag(name))


def clean(text):
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("\u2033", '"').replace("\u2032", "'")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_costs(el):
    costs = {}
    costs_el = find(el, "costs")
    if costs_el is not None:
        for c in findall(costs_el, "cost"):
            name = c.get("name", "")
            try:
                val = float(c.get("value", "0"))
            except ValueError:
                val = 0.0
            if val:
                costs[name] = val
    return costs


def get_categories(el):
    cats = []
    cats_el = find(el, "categories")
    if cats_el is not None:
        for c in findall(cats_el, "category"):
            name = c.get("name", "")
            if name and name != "Configuration":
                cats.append(name)
    return cats


# ── Profile extraction ────────────────────────────────────────────────────────

def extract_profiles(el):
    profiles = []
    profs_el = find(el, "profiles")
    if profs_el is None:
        return profiles
    for p in findall(profs_el, "profile"):
        chars = {}
        chars_el = find(p, "characteristics")
        if chars_el is not None:
            for c in findall(chars_el, "characteristic"):
                chars[c.get("name", "")] = clean(c.text or "")
        profiles.append({
            "name":  p.get("name", ""),
            "type":  p.get("typeName", ""),
            "chars": chars,
        })
    return profiles


# ── Model data extraction ─────────────────────────────────────────────────────

def extract_model_data(sel):
    """
    Build a model dict from a <selection type="model"> element.
    Recursively traverses the full selection tree for all abilities/weapons.
    """
    name      = sel.get("name", "Unknown")
    costs     = get_costs(sel)
    keywords  = get_categories(sel)

    stats     = {}
    abilities = []    # [{"name": str, "text": str}]
    weapons   = []    # [{"name": str, "type": str, "range": str, "keywords": str, "rules": str}]

    seen: set = set()

    def process(profiles_list):
        for p in profiles_list:
            pname = p["name"]
            ptype = p["type"]
            chars = p["chars"]

            if pname in seen:
                continue
            seen.add(pname)

            if ptype == "Unit":
                stats.update({
                    "Movement": chars.get("Movement", ""),
                    "Ranged":   chars.get("Ranged", ""),
                    "Melee":    chars.get("Melee", ""),
                    "Armour":   chars.get("Armour", ""),
                    "Base":     chars.get("Base", ""),
                })

            elif ptype == "Ability":
                desc = chars.get("Description", "")
                if desc:
                    abilities.append({"name": pname, "text": desc})

            elif ptype == "Weapon":
                weapons.append({
                    "name":     pname,
                    "type":     chars.get("Type", ""),
                    "range":    chars.get("Range", ""),
                    "keywords": chars.get("Keywords", ""),
                    "rules":    chars.get("Rules", ""),
                })

            elif ptype == "Battlekit":
                btype       = chars.get("Type", "")
                rules       = chars.get("Rules", "")
                kw_str      = chars.get("Keywords", "")
                rng         = chars.get("Range", "")
                # Treat as weapon if it has a non-trivial Range
                if rng and rng not in ("-", ""):
                    weapons.append({
                        "name":     pname,
                        "type":     btype,
                        "range":    rng,
                        "keywords": kw_str,
                        "rules":    rules,
                    })
                else:
                    # Armour/equipment → ability-like
                    parts = []
                    if btype and btype not in ("-", ""):
                        parts.append(f"[{btype}]")
                    if kw_str and kw_str not in ("-", ""):
                        parts.append(kw_str)
                    if rules and rules not in ("-", ""):
                        parts.append(rules)
                    abilities.append({
                        "name": pname,
                        "text": "  ".join(parts),
                    })

    def walk(s):
        process(extract_profiles(s))
        subs = find(s, "selections")
        if subs is not None:
            for child in findall(subs, "selection"):
                walk(child)

    walk(sel)

    return {
        "name":      name,
        "ducats":    int(costs.get("Ducats", 0)),
        "glory":     int(costs.get("Glory Points", 0)),
        "keywords":  keywords,
        "stats":     stats,
        "abilities": abilities,
        "weapons":   weapons,
    }


# ── Warband-rule extraction ───────────────────────────────────────────────────

def extract_warband_rules(sel):
    """Extract warband-level rules from an upgrade selection."""
    rules = []
    seen:  set = set()

    def collect(s):
        for p in extract_profiles(s):
            pname = p["name"]
            if pname in seen:
                continue
            seen.add(pname)
            ptype = p["type"]
            chars = p["chars"]

            if ptype == "Ability":
                desc = chars.get("Description", "")
                if desc:
                    rules.append({"name": pname, "text": desc})

            elif ptype == "Battlekit":
                rule_text = chars.get("Rules", "")
                kw_str    = chars.get("Keywords", "")
                btype     = chars.get("Type", "")
                parts = []
                if btype and btype not in ("-", ""):
                    parts.append(f"[{btype}]")
                if kw_str and kw_str not in ("-", ""):
                    parts.append(kw_str)
                if rule_text and rule_text not in ("-", ""):
                    parts.append(rule_text)
                if parts:
                    rules.append({"name": pname, "text": "  ".join(parts)})

        subs = find(s, "selections")
        if subs is not None:
            for child in findall(subs, "selection"):
                collect(child)

    collect(sel)
    return rules


# ── TTS formatting ────────────────────────────────────────────────────────────

def _weapon_inline(w):
    """Return a short inline string for a weapon: Name Range KW."""
    parts = [w["name"]]
    rng = w["range"]
    if rng and rng not in ("-", ""):
        parts.append(rng)
    kw = w["keywords"]
    if kw and kw not in ("-", ""):
        parts.append(kw)
    rules = w["rules"]
    if rules and rules not in ("-", ""):
        # Only add rules text if it's very short
        if len(rules) <= 60:
            parts.append(rules)
    return " ".join(parts)


def format_model_card(model: dict, faction: str = "", verbose: bool = False) -> str:
    """
    Format a model dict into a TTS name-card string.

    Line 1: [gold]Name  Cost / Mov / Rng / Melee / Armour / Base[-]
    Line 2: [blue] Weapon1 | Weapon2 | ...
    Line 3+: [pink]AbilityName[-] [sup]compressed text[/sup]
    """
    lines = []

    # ── Name + cost + stat line ──────────────────────────────────────────────
    cost_parts = []
    if model["ducats"]:
        cost_parts.append(f"{model['ducats']}D")
    if model["glory"]:
        cost_parts.append(f"{model['glory']}GP")
    cost_str = f"  ({', '.join(cost_parts)})" if cost_parts else ""

    stats = model["stats"]
    stat_line_parts = []
    for key in ("Movement", "Ranged", "Melee", "Armour", "Base"):
        v = stats.get(key, "")
        stat_line_parts.append(v if v else "-")
    stat_str = " / ".join(stat_line_parts) if any(stat_line_parts) else ""

    name_line = f"{model['name']}{cost_str}"
    if stat_str:
        name_line += f"  |  {stat_str}"
    lines.append(h(name_line))

    # ── Keywords ─────────────────────────────────────────────────────────────
    if model["keywords"]:
        lines.append(small("[" + ", ".join(model["keywords"]) + "]"))

    # ── Weapons (inline on one line) ─────────────────────────────────────────
    if model["weapons"]:
        weapon_parts = [f"{WEAP_CLR}{_weapon_inline(w)}{RESET}"
                        for w in model["weapons"]]
        lines.append("  " + "  |  ".join(weapon_parts))

    # ── Abilities ────────────────────────────────────────────────────────────
    if model["abilities"]:
        for ab in model["abilities"]:
            compressed = compress_ability(ab["name"], ab["text"], faction=faction, verbose=verbose)
            line = f"  {ABIL_CLR}{ab['name']}{RESET}"
            if compressed:
                line += f"  {small(compressed)}"
            lines.append(line)

    return "\n".join(lines)


def format_warband_rule_card(sel_name: str, rules: list, faction: str = "", verbose: bool = False) -> str:
    """Format warband-level rules into a standalone TTS card."""
    lines = [h(sel_name)]
    for rule in rules:
        compressed = compress_ability(rule["name"], rule["text"], faction=faction, verbose=verbose)
        line = f"  {RULE_CLR}{rule['name']}{RESET}"
        if compressed:
            line += f"\n  {small(compressed)}"
        lines.append(line)
    return "\n".join(lines)


def format_variant_card(variant_name: str, abilities: dict, faction: str = "", verbose: bool = False) -> str:
    """
    Format a warband variant (strain/warband-type) block as a standalone TTS card.
    abilities: { ability_name: compressed_text }
    """
    lines = [h(f"[{variant_name}]")]
    for ab_name, text in abilities.items():
        if verbose:
            compressed = text
        else:
            # Try compress again with the full lookup stack, using dict text as fallback
            compressed = compress_ability(ab_name, text, faction=faction, verbose=verbose)
        line = f"  {RULE_CLR}{ab_name}{RESET}"
        if compressed:
            line += f"\n  {small(compressed)}"
        lines.append(line)
    return "\n".join(lines)


# ── Roster parsing ────────────────────────────────────────────────────────────

def open_roster(path):
    p = Path(path)
    if p.suffix == ".rosz":
        with zipfile.ZipFile(path) as z:
            inner = z.namelist()[0]
            with z.open(inner) as f:
                return ET.parse(f).getroot()
    elif p.suffix in (".gz", ".gzip"):
        with gzip.open(path) as f:
            return ET.parse(f).getroot()
    else:
        return ET.parse(path).getroot()


# Top-level upgrade selections that are pure meta-config, not card-worthy
_SKIP_NAMES = {"Campaign Rules"}


def parse_roster(path):
    root = open_roster(path)

    roster_name = root.get("name", "Unknown Warband")

    total_costs = {}
    costs_el = find(root, "costs")
    if costs_el is not None:
        for c in findall(costs_el, "cost"):
            try:
                total_costs[c.get("name", "")] = float(c.get("value", "0"))
            except ValueError:
                pass

    forces = find(root, "forces")
    force  = find(forces, "force") if forces is not None else None
    faction = force.get("catalogueName", "") if force is not None else ""

    sels = find(force, "selections") if force is not None else None
    if sels is None:
        return {
            "name": roster_name, "faction": faction,
            "ducats": int(total_costs.get("Ducats", 0)),
            "glory":  int(total_costs.get("Glory Points", 0)),
            "models": [], "warband_rules": [],
        }

    models           = []
    warband_rules    = []
    warband_variants = []   # strain / warband-type variant blocks

    for sel in findall(sels, "selection"):
        stype = sel.get("type", "")
        sname = sel.get("name", "")

        if stype == "model":
            models.append(extract_model_data(sel))
        elif stype == "upgrade" and sname not in _SKIP_NAMES:
            if sname == "Warband Variant":
                # Collect the chosen variant sub-selections as strain blocks
                sub_sels = find(sel, "selections")
                if sub_sels is not None:
                    for vsub in findall(sub_sels, "selection"):
                        vname  = vsub.get("name", "")
                        vrules = extract_warband_rules(vsub)
                        if vrules:
                            warband_variants.append({
                                "variant_name": vname,
                                "rules":        vrules,
                            })
                # Also capture any abilities directly on the Warband Variant entry
                direct = extract_warband_rules(sel)
                # Filter out ones already captured in sub-selections
                direct_names = {r["name"] for v in warband_variants for r in v["rules"]}
                top_rules = [r for r in direct if r["name"] not in direct_names]
                if top_rules:
                    warband_variants.append({
                        "variant_name": "Warband Rules",
                        "rules":        top_rules,
                    })
            else:
                rules = extract_warband_rules(sel)
                if rules:
                    warband_rules.append({
                        "selection_name": sname,
                        "rules":          rules,
                    })

    return {
        "name":              roster_name,
        "faction":           faction,
        "ducats":            int(total_costs.get("Ducats", 0)),
        "glory":             int(total_costs.get("Glory Points", 0)),
        "models":            models,
        "warband_rules":     warband_rules,
        "warband_variants":  warband_variants,
    }


# ── Combined / split output ───────────────────────────────────────────────────

def build_combined_output(roster: dict, verbose: bool = False) -> str:
    blocks   = []
    faction  = roster.get("faction", "")

    totals = f"Total: {roster['ducats']} Ducats  {roster['glory']} Glory"
    header = (
        f"{HEADER}{'='*54}{RESET}\n"
        f"{h(roster['name'] + '  (' + faction + ')')}\n"
        f"{small(totals)}\n"
        f"{HEADER}{'='*54}{RESET}"
    )
    blocks.append(header)

    # ── Warband variants (strains / warband types) ───────────────────────────
    if roster.get("warband_variants"):
        blocks.append(h("-- WARBAND VARIANT --"))
        for wv in roster["warband_variants"]:
            blocks.append(
                format_warband_rule_card(
                    wv["variant_name"], wv["rules"],
                    faction=faction, verbose=verbose
                )
            )
            blocks.append("")

    # ── Regular warband-level rules ─────────────────────────────────────────
    if roster["warband_rules"]:
        blocks.append(h("-- WARBAND RULES --"))
        for wr in roster["warband_rules"]:
            blocks.append(
                format_warband_rule_card(
                    wr["selection_name"], wr["rules"],
                    faction=faction, verbose=verbose
                )
            )
            blocks.append("")

    if roster["models"]:
        blocks.append(h("-- MODELS --"))
        for model in roster["models"]:
            blocks.append(format_model_card(model, faction=faction, verbose=verbose))
            blocks.append("")

    return "\n".join(blocks)


def build_split_output(roster: dict, verbose: bool = False) -> dict:
    """Return {filename: card_text} for per-model/per-rule output."""
    files   = {}
    faction = roster.get("faction", "")

    def safe(s):
        return re.sub(r"[^\w\s-]", "", s).strip().replace(" ", "_")[:40]

    idx = 1
    for model in roster["models"]:
        fname = f"{idx:02d}_{safe(model['name'])}.txt"
        files[fname] = format_model_card(model, faction=faction, verbose=verbose)
        idx += 1

    # Warband variant / strain cards
    vidx = 1
    for wv in roster.get("warband_variants", []):
        card_text = format_warband_rule_card(
            wv["variant_name"], wv["rules"], faction=faction, verbose=verbose
        )
        fname = f"variant_{vidx:02d}_{safe(wv['variant_name'])}.txt"
        files[fname] = card_text
        vidx += 1

    ridx = 1
    for wr in roster["warband_rules"]:
        card_text = format_warband_rule_card(
            wr["selection_name"], wr["rules"], faction=faction, verbose=verbose
        )
        fname = f"rules_{ridx:02d}_{safe(wr['selection_name'])}.txt"
        files[fname] = card_text
        ridx += 1

    return files


# ── CLI ───────────────────────────────────────────────────────────────────────

def convert_file(path, per_model=False, output_dir=None, verbose=False):
    roster = parse_roster(path)

    if per_model:
        out_dir = Path(output_dir) if output_dir else Path(path).parent / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        split = build_split_output(roster, verbose)
        for fname, content in split.items():
            out_path = out_dir / fname
            out_path.write_text(content, encoding="utf-8")
            print(f"  Wrote {out_path}")
        print(f"Wrote {len(split)} files to {out_dir}")
    else:
        print(build_combined_output(roster, verbose))


def convert_folder(folder, per_model=False, verbose=False):
    folder_path = Path(folder)
    rosz_files = sorted(
        list(folder_path.glob("*.rosz")) + list(folder_path.glob("*.ros"))
    )
    if not rosz_files:
        print(f"No .rosz / .ros files found in {folder}", file=sys.stderr)
        return
    for f in rosz_files:
        print(f"\n{'='*60}\nProcessing: {f.name}\n{'='*60}")
        out_dir = (str(folder_path / "output" / f.stem) if per_model else None)
        convert_file(str(f), per_model=per_model, output_dir=out_dir, verbose=verbose)


def watch_path(path, per_model=False, verbose=False):
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class Handler(FileSystemEventHandler):
            def __init__(self, target_path):
                self.target  = Path(target_path)
                self.is_file = self.target.is_file()

            def _handle(self, event_path):
                ep = Path(event_path)
                if self.is_file:
                    if ep == self.target:
                        print(f"\n[watch] {ep.name} changed — converting...")
                        convert_file(str(self.target), per_model, verbose=verbose)
                else:
                    if ep.suffix in (".rosz", ".ros"):
                        print(f"\n[watch] {ep.name} changed — converting...")
                        out = str(self.target / "output" / ep.stem) if per_model else None
                        convert_file(str(ep), per_model, output_dir=out, verbose=verbose)

            def on_modified(self, event): self._handle(event.src_path)
            def on_created(self, event):  self._handle(event.src_path)

        target    = Path(path)
        watch_dir = target.parent if target.is_file() else target
        handler   = Handler(path)
        observer  = Observer()
        observer.schedule(handler, str(watch_dir), recursive=False)
        observer.start()
        print(f"[watch] Watching {path}  (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    except ImportError:
        print("[watch] watchdog not available — polling every 2s")
        target = Path(path)
        if target.is_file():
            mtimes = {str(target): target.stat().st_mtime}
        else:
            mtimes = {
                str(f): f.stat().st_mtime
                for f in list(target.glob("*.rosz")) + list(target.glob("*.ros"))
            }
        print(f"[watch] Polling {path}  (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(2)
                check_files = (
                    [target] if target.is_file()
                    else list(target.glob("*.rosz")) + list(target.glob("*.ros"))
                )
                for f in check_files:
                    mtime = f.stat().st_mtime
                    if mtimes.get(str(f)) != mtime:
                        mtimes[str(f)] = mtime
                        print(f"\n[watch] {f.name} changed — converting...")
                        out = str(target / "output" / f.stem) if per_model and target.is_dir() else None
                        convert_file(str(f), per_model, output_dir=out, verbose=verbose)
        except KeyboardInterrupt:
            print("\n[watch] Stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Convert Trench Crusade roster files to TTS card format."
    )
    parser.add_argument("path",
                        help="Roster file (.rosz/.ros) or folder containing rosters")
    parser.add_argument("--per-model", action="store_true",
                        help="Write one .txt file per model / warband rule")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory for --per-model (default: ./output)")
    parser.add_argument("--watch", action="store_true",
                        help="Watch for file changes and auto-convert")
    parser.add_argument("--verbose", action="store_true",
                        help="Output full uncompressed ability text")
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"Error: {args.path} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.watch:
        watch_path(args.path, per_model=args.per_model, verbose=args.verbose)
    elif target.is_dir():
        convert_folder(args.path, per_model=args.per_model, verbose=args.verbose)
    else:
        convert_file(args.path, per_model=args.per_model,
                     output_dir=args.output_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
