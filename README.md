# TC → TTS Converter

Convert Trench Crusade warband rosters into Tabletop Simulator card format. No install. No setup. Runs in your browser.

## Supported Formats

- **.rosz** — NewRecruit (newrecruit.eu)
- **.ros / .xml** — BattleScribe
- **.json** — Trench Companion

## How to Use

1. Build your warband at [newrecruit.eu](https://www.newrecruit.eu/) (or BattleScribe / Trench Companion) and export it
2. Drop the file onto the page
3. Copy the **Name Card** text into the TTS Name field (right-click model → Name)
4. Copy the **Description** text into the TTS Description field (right-click model → Description)
5. Use **Full / Short / Edit** toggles to trim long ability descriptions so they fit on cards

## Features

- TTS color-coded output with preset swatches or custom hex
- Drag to reorder units
- Inline name editing per unit
- Duplicate unit detection (identical loadouts get collapsed with a count badge)
- Spell descriptions parsed and displayed separately with their own toggles
- Copy per-section, per-unit, or all at once
- Download everything as a .txt file

## Running Locally

Open `index.html` in any modern browser. That's it. No build step, no dependencies, no server.

## License

MIT
