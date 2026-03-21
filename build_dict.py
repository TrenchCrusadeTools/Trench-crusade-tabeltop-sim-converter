#!/usr/bin/env python3
"""
build_dict.py  —  Extract abilities from all Trench Crusade BattleScribe
                  catalogue files and emit faction_abilities.py.

Usage:
    py build_dict.py                   # uses data/ folder next to this file
    py build_dict.py --data path/      # custom data folder
    py build_dict.py --download        # (re-)download catalogues from GitHub
    py build_dict.py --verbose         # show all extracted abilities

Output:
    faction_abilities.py  — importable Python module with per-faction dicts
                            and a merged FACTION_ABILITIES lookup.
"""

import os
import re
import sys
import argparse
import textwrap
from pathlib import Path
from xml.etree import ElementTree as ET

# ── Namespace handling ────────────────────────────────────────────────────────

NS_CAT = "http://www.battlescribe.net/schema/catalogueSchema"
NS_GST = "http://www.battlescribe.net/schema/gameSystemSchema"

# Build tag helpers that work regardless of which namespace a file uses
def _tag(ns, name):
    return f"{{{ns}}}{name}"


def _find_in(root, name):
    """Find child element by local name, trying both catalogue and gst namespaces."""
    for ns in (NS_CAT, NS_GST):
        el = root.find(_tag(ns, name))
        if el is not None:
            return el
    return None


def _findall_in(root, name):
    result = []
    for ns in (NS_CAT, NS_GST):
        result.extend(root.findall(f".//{_tag(ns, name)}"))
    return result


# ── Compression transforms (same as convert.py) ───────────────────────────────

_COMPRESS_SUBS = [
    (r"^If you do so,\s*", ""),
    (r"^you can say that\s*", ""),
    (r"^you can\s*", ""),
    (r"^you must\s*", ""),
    (r"^In addition,?\s*", ""),
    (r"^Note that\s*", ""),
    (r"^Once per Turn\s*", "1x/turn: "),
    (r"\bthe model taking the (\w+ )?ACTION\b", "the model"),
    (r"\bthe model with this Keyword\b", "this model"),
    (r"\bA model with this (Goetic )?Ability\b", "This model"),
    (r"\bA model with this Keyword\b", "This model"),
    (r"\bfor a model with this\b", ""),
    (r"\bfor this model\b", ""),
    (r"\bfor the model\b", ""),
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
    (r"\bINFECTION MARKER(S)?\b", "INF MARKER"),
    (r"\bBLOOD MARKER(S)?\b", "BLOOD"),
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
    (r'\b(\d+)"\b', r'\1"'),
    (r"\xa0", " "),
    (r"\s{2,}", " "),
    (r"\s+\.", "."),
    (r"\s+,", ","),
]

_COMPRESS_PATTERNS = [(re.compile(p, re.IGNORECASE), r) for p, r in _COMPRESS_SUBS]

MAX_LINE = 200  # chars before truncation


def clean(text):
    if not text:
        return ""
    text = text.replace("\xa0", " ").replace("\u2033", '"').replace("\u2032", "'")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compress(text):
    if not text:
        return ""
    result = clean(text)
    for pattern, repl in _COMPRESS_PATTERNS:
        result = pattern.sub(repl, result)
    result = result.strip()
    if len(result) > MAX_LINE:
        cut = result.rfind(".", 0, MAX_LINE)
        if cut > MAX_LINE // 2:
            result = result[: cut + 1]
        else:
            result = result[:MAX_LINE].rsplit(" ", 1)[0] + "..."
    return result


# ── Catalogue parsing ─────────────────────────────────────────────────────────

def parse_cat_abilities(filepath):
    """
    Extract all Ability profiles from a .cat or .gst file.
    Returns dict: ability_name -> compressed_text
    Also returns raw_dict: ability_name -> raw_text
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    abilities = {}   # name -> compressed
    raw      = {}    # name -> full text

    # Find all profile elements (catalogue namespace)
    profiles = root.findall(f".//{_tag(NS_CAT, 'profile')}")
    # Also try gst namespace (for .gst files)
    profiles += root.findall(f".//{_tag(NS_GST, 'profile')}")

    for p in profiles:
        if p.get("typeName") != "Ability":
            continue
        name = clean(p.get("name", ""))
        if not name:
            continue

        # Find Description characteristic
        desc = ""
        for ns in (NS_CAT, NS_GST):
            desc_el = p.find(f".//{_tag(ns, 'characteristic')}[@name='Description']")
            if desc_el is not None and desc_el.text:
                desc = desc_el.text
                break

        if name not in abilities:   # first occurrence wins
            raw[name]       = clean(desc)
            abilities[name] = compress(desc)

    return abilities, raw


def parse_gst_keywords(filepath):
    """
    Extract weapon keyword rules from the .gst sharedRules.
    Returns dict: keyword_name -> compressed_text
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    keywords = {}
    for ns in (NS_CAT, NS_GST):
        rules_el = root.find(_tag(ns, "sharedRules"))
        if rules_el is not None:
            for rule in rules_el:
                name = clean(rule.get("name", ""))
                desc_el = rule.find(_tag(ns, "description"))
                desc = clean(desc_el.text or "") if desc_el is not None else ""
                if name and desc:
                    keywords[name] = compress(desc)
    return keywords


def extract_warband_variants(filepath):
    """
    Return a dict of warband-variant blocks:
      { "Variant Name": { ability_name: compressed_text, ... } }
    These are 'Warband Variant' upgrade entries that represent
    faction sub-rules (strains, warband types, etc.)
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    variants = {}

    # Look for selectionEntry with name="Warband Variant" at any depth
    for ns in (NS_CAT, NS_GST):
        for sel in root.findall(f".//{_tag(ns, 'selectionEntry')}"):
            if sel.get("name", "") != "Warband Variant":
                continue

            # Each selectionEntryGroup or nested selectionEntry under here
            # represents one warband variant
            for child_ns in (NS_CAT, NS_GST):
                # selectionEntryGroups directly under the Warband Variant entry
                for grp in sel.findall(f".//{_tag(child_ns, 'selectionEntryGroup')}"):
                    grp_name = clean(grp.get("name", ""))
                    if not grp_name:
                        continue
                    block = {}
                    for p in grp.findall(f".//{_tag(child_ns, 'profile')}"):
                        if p.get("typeName") != "Ability":
                            continue
                        ab_name = clean(p.get("name", ""))
                        if not ab_name:
                            continue
                        desc = ""
                        desc_el = p.find(
                            f".//{_tag(child_ns, 'characteristic')}[@name='Description']"
                        )
                        if desc_el is not None and desc_el.text:
                            desc = desc_el.text
                        if ab_name not in block:
                            block[ab_name] = compress(desc)
                    if block:
                        variants.setdefault(grp_name, {}).update(block)

            # Also collect abilities directly on the Warband Variant entry
            # (not inside sub-groups) — these are top-level variant rules
            direct = {}
            for child_ns in (NS_CAT, NS_GST):
                for p in sel.findall(f".//{_tag(child_ns, 'profile')}"):
                    if p.get("typeName") != "Ability":
                        continue
                    ab_name = clean(p.get("name", ""))
                    if not ab_name:
                        continue
                    desc = ""
                    desc_el = p.find(
                        f".//{_tag(child_ns, 'characteristic')}[@name='Description']"
                    )
                    if desc_el is not None and desc_el.text:
                        desc = desc_el.text
                    if ab_name not in direct:
                        direct[ab_name] = compress(desc)
            if direct:
                variants.setdefault("_warband_rules", {}).update(direct)

    return variants


# ── GitHub download ───────────────────────────────────────────────────────────

CAT_URLS = {
    "New Antioch": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/New%20Antioch.cat",
    "Trench Pilgrims": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Trench%20Pilgrims.cat",
    "Iron Sultanate": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Iron%20Sultanate.cat",
    "Heretic Legion": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Heretic%20Legion.cat",
    "Black Grail": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Black%20Grail.cat",
    "Court of the Seven-Headed Serpent": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Court%20of%20the%20Seven-Headed%20Serpent.cat",
    "Mercenaries": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Mercenaries.cat",
    "Campaign Rules": "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Campaign%20Rules.cat",
}

GST_URL = "https://raw.githubusercontent.com/Fawkstrot11/TrenchCrusade/main/Trench%20Crusade.gst"


def download_data(data_dir):
    import urllib.request
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    print("Downloading catalogue files from GitHub …")
    for name, url in CAT_URLS.items():
        dest = data_dir / f"{name}.cat"
        print(f"  {name} … ", end="", flush=True)
        urllib.request.urlretrieve(url, dest)
        print("ok")

    dest = data_dir / "Trench Crusade.gst"
    print("  Trench Crusade.gst … ", end="", flush=True)
    urllib.request.urlretrieve(GST_URL, dest)
    print("ok")


# ── Output generation ─────────────────────────────────────────────────────────

_PY_HEADER = '''\
# faction_abilities.py  —  AUTO-GENERATED by build_dict.py
# Do not edit by hand. Run:  py build_dict.py
#
# Provides per-faction ability compression dictionaries extracted from
# the BattleScribe catalogue files for Trench Crusade.
#
# FACTION_ABILITIES: { faction_name -> { ability_name -> compressed_text } }
# WARBAND_VARIANTS:  { faction_name -> { variant_name -> { ability_name -> text } } }
# WEAPON_KEYWORDS:   { keyword_name -> compressed_text }  (from .gst)

'''


def _dict_to_py(d, indent=4):
    """Render a dict as a Python literal with nice alignment."""
    if not d:
        return "{}"
    lines = ["{"]
    pad = " " * indent
    max_k = max(len(repr(k)) for k in d) if d else 0
    for k, v in sorted(d.items()):
        rk = repr(k)
        rv = repr(v)
        lines.append(f"{pad}{rk:{max_k}}: {rv},")
    lines.append("}")
    return "\n".join(lines)


def generate_module(faction_dicts, variant_dicts, keyword_dict, out_path):
    lines = [_PY_HEADER]

    # ── Per-faction abilities ────────────────────────────────────────────────
    lines.append("FACTION_ABILITIES = {")
    for faction, abilities in sorted(faction_dicts.items()):
        lines.append(f"    # {'─' * 60}")
        lines.append(f"    {repr(faction)}: {{")
        if abilities:
            max_k = max(len(repr(k)) for k in abilities)
            for name, text in sorted(abilities.items()):
                lines.append(f"        {repr(name):{max_k}}: {repr(text)},")
        lines.append("    },")
        lines.append("")
    lines.append("}")
    lines.append("")

    # ── Warband variant blocks ───────────────────────────────────────────────
    lines.append("WARBAND_VARIANTS = {")
    for faction, variants in sorted(variant_dicts.items()):
        lines.append(f"    # {'─' * 60}")
        lines.append(f"    {repr(faction)}: {{")
        for variant_name, abilities in sorted(variants.items()):
            lines.append(f"        {repr(variant_name)}: {{")
            if abilities:
                max_k = max(len(repr(k)) for k in abilities)
                for name, text in sorted(abilities.items()):
                    lines.append(f"            {repr(name):{max_k}}: {repr(text)},")
            lines.append("        },")
        lines.append("    },")
        lines.append("")
    lines.append("}")
    lines.append("")

    # ── Weapon keywords ──────────────────────────────────────────────────────
    lines.append("WEAPON_KEYWORDS = {")
    if keyword_dict:
        max_k = max(len(repr(k)) for k in keyword_dict)
        for name, text in sorted(keyword_dict.items()):
            lines.append(f"    {repr(name):{max_k}}: {repr(text)},")
    lines.append("}")
    lines.append("")

    # ── Merged flat lookup (all factions combined) ───────────────────────────
    lines.append("# Flat merge: last writer wins for duplicates across factions")
    lines.append("ALL_ABILITIES = {")
    merged = {}
    for d in faction_dicts.values():
        merged.update(d)
    if merged:
        max_k = max(len(repr(k)) for k in merged)
        for name, text in sorted(merged.items()):
            lines.append(f"    {repr(name):{max_k}}: {repr(text)},")
    lines.append("}")
    lines.append("")

    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build faction ability dictionaries from .cat files")
    parser.add_argument("--data", default=None, help="Folder containing .cat and .gst files (default: data/ next to this script)")
    parser.add_argument("--out", default=None, help="Output .py file (default: faction_abilities.py next to this script)")
    parser.add_argument("--download", action="store_true", help="Download .cat/.gst files from GitHub before parsing")
    parser.add_argument("--verbose", action="store_true", help="Print all extracted abilities")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    data_dir   = Path(args.data) if args.data else script_dir / "data"
    out_path   = Path(args.out)  if args.out  else script_dir / "faction_abilities.py"

    if args.download:
        download_data(data_dir)

    # ── Parse .gst for weapon keywords ──────────────────────────────────────
    gst_files = list(data_dir.glob("*.gst"))
    keyword_dict = {}
    for gst in gst_files:
        keyword_dict.update(parse_gst_keywords(gst))
        print(f"GST keywords: {len(keyword_dict)} from {gst.name}")

    # ── Parse .cat files ─────────────────────────────────────────────────────
    cat_files = sorted(data_dir.glob("*.cat"))
    if not cat_files:
        print(f"No .cat files found in {data_dir}", file=sys.stderr)
        sys.exit(1)

    faction_dicts  = {}   # faction -> {name: compressed}
    variant_dicts  = {}   # faction -> variant_blocks

    for cat_path in cat_files:
        faction_name = cat_path.stem  # e.g. "New Antioch"
        abilities, raw = parse_cat_abilities(cat_path)
        variants       = extract_warband_variants(cat_path)

        faction_dicts[faction_name] = abilities
        if variants:
            variant_dicts[faction_name] = variants

        print(f"{faction_name:45} {len(abilities):3} abilities  {len(variants):2} variant blocks")

        if args.verbose:
            for name, text in sorted(abilities.items()):
                print(f"    {name}")
                print(f"      {text[:120]}")

    generate_module(faction_dicts, variant_dicts, keyword_dict, out_path)

    total = sum(len(d) for d in faction_dicts.values())
    print(f"\nTotal: {total} abilities across {len(faction_dicts)} factions -> {out_path.name}")


if __name__ == "__main__":
    main()
