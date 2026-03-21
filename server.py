#!/usr/bin/env python3
"""
FastMCP server exposing the TC→TTS converter as MCP tools.

Tools:
  convert_roster          Convert a .rosz path → combined TTS string
  convert_roster_split    Convert a .rosz path → {filename: content} dict
  list_rosters            List .rosz files in a folder
"""

import sys
from pathlib import Path
from typing import Annotated

try:
    import fastmcp
except ImportError:
    print("fastmcp not installed. Run: py -m pip install fastmcp", file=sys.stderr)
    sys.exit(1)

from fastmcp import FastMCP

# Add the project directory to path so we can import convert
sys.path.insert(0, str(Path(__file__).parent))
from convert import parse_roster, build_combined_output, build_split_output

mcp = FastMCP(
    name="trench-crusade-converter",
    instructions=(
        "Convert Trench Crusade warband roster files (.rosz/.ros) into "
        "Tabletop Simulator card text. Use convert_roster for a single block, "
        "convert_roster_split for per-model/per-rule files."
    ),
)


@mcp.tool()
def convert_roster(
    path: Annotated[str, "Absolute path to a .rosz or .ros roster file"],
    verbose: Annotated[bool, "If True, output full uncompressed ability text"] = False,
) -> str:
    """
    Convert a Trench Crusade roster file to a single TTS-formatted text block.

    Returns the full warband — models, weapons, abilities, warband rules —
    formatted with TTS colour codes ready to paste into Tabletop Simulator.
    Abilities are compressed to concise summaries by default (verbose=False).
    """
    p = Path(path)
    if not p.exists():
        return f"Error: file not found: {path}"
    if p.suffix not in (".rosz", ".ros"):
        return f"Error: unsupported file type '{p.suffix}'. Use .rosz or .ros"
    try:
        roster = parse_roster(path)
        return build_combined_output(roster, verbose=verbose)
    except Exception as exc:
        return f"Error converting roster: {exc}"


@mcp.tool()
def convert_roster_split(
    path: Annotated[str, "Absolute path to a .rosz or .ros roster file"],
    verbose: Annotated[bool, "If True, output full uncompressed ability text"] = False,
) -> dict:
    """
    Convert a Trench Crusade roster file to per-model / per-rule TTS cards.

    Returns a dict mapping filename -> card text, e.g.:
      {
        "01_Matagot_Hag.txt":           "<card text>",
        "02_Butcher_Knight.txt":        "<card text>",
        "rules_01_Noble_Hierarchy.txt": "<card text>",
      }

    Each entry is a separate card for a TTS placeholder object.
    Abilities are compressed by default (verbose=False).
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    if p.suffix not in (".rosz", ".ros"):
        return {"error": f"unsupported file type '{p.suffix}'"}
    try:
        roster = parse_roster(path)
        return build_split_output(roster, verbose=verbose)
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_rosters(
    folder: Annotated[str, "Absolute path to a folder containing .rosz files"],
) -> list:
    """
    List all .rosz and .ros files in a folder.

    Returns a list of absolute file paths.
    """
    d = Path(folder)
    if not d.is_dir():
        return [f"Error: not a directory: {folder}"]
    files = sorted(str(f) for f in d.glob("*.rosz")) + \
            sorted(str(f) for f in d.glob("*.ros"))
    return files if files else [f"No roster files found in {folder}"]


@mcp.tool()
def get_roster_summary(
    path: Annotated[str, "Absolute path to a .rosz or .ros roster file"],
) -> dict:
    """
    Return a structured summary of a roster: name, faction, total costs,
    model names, and warband rule names — without full card text.
    """
    p = Path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    try:
        roster = parse_roster(path)
        return {
            "name":     roster["name"],
            "faction":  roster["faction"],
            "ducats":   roster["ducats"],
            "glory":    roster["glory"],
            "models":   [
                {
                    "name":     m["name"],
                    "ducats":   m["ducats"],
                    "keywords": m["keywords"],
                    "weapons":  [w["name"] for w in m["weapons"]],
                }
                for m in roster["models"]
            ],
            "warband_rules":    [wr["selection_name"] for wr in roster["warband_rules"]],
            "warband_variants": [wv["variant_name"] for wv in roster.get("warband_variants", [])],
        }
    except Exception as exc:
        return {"error": str(exc)}


if __name__ == "__main__":
    mcp.run()
