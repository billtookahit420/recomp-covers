# Recomp Covers

A small companion utility for the recomp converter family: it fills in
missing cover art from **multiple sources, with retries** — the
single-attempt, single-host fetch is how covers went missing in the
first place (the host was having a bad night and nothing tried again
or anywhere else). It started out PSX-only and now covers **every
system the family handles**:

PlayStation · NES · Game Boy · Game Boy Color · SNES · Game Boy
Advance · Nintendo 64 · Genesis / Mega Drive · Virtual Boy ·
Master System · Game Gear · Nintendo DS

## Packages mode (auto-detect)

Point it at a folder of **extracted** recomp packages — PSX, NES, GB,
SNES and GBA packages can all sit in the same folder. For each package
it detects the system and title (PSX by disc serial → Redump name;
the cartridge systems from the package's own README/config), then
fetches any missing art:

1. libretro-thumbnails `Named_Boxarts`, via its GitHub mirror
2. libretro-thumbnails `Named_Boxarts`, canonical host

…trying the name variants each converter uses (region groups, Rev
tags, GB/GBC enhancement suffixes), retrying network errors but never
a 404, and validating every download as a real image. Art lands at
`artwork/<title-or-serial>/front.png` inside the package, and for the
cartridge systems the launcher's own `assets/img/boxart.tga` is
written/healed from the same cover. Games that already have art are
skipped (a checkbox refetches them).

## ROM folder mode

Pick a system in the dropdown and point it at a folder of ROMs for
that system. Art lands in `<folder>/artwork/<rom name>/front.png` —
handy for the systems whose converter tools are still coming (N64,
Genesis, Virtual Boy, Master System, Game Gear, DS), and for keeping
a library's art complete ahead of the front end.

## Running it

- **Linux:** `./recomp-covers.sh` (first run creates a local venv and
  installs PySide6 + Pillow, then never again)
- **Windows:** `recomp-covers.bat` (same idea)
- **Command line** (no GUI needed):
  - `python3 covers.py scan <folder>` — packages + art state
  - `python3 covers.py fetch <folder>` — fill missing package covers
  - `python3 covers.py scan-roms <folder> n64` — ROM folder + state
  - `python3 covers.py fetch-roms <folder> n64` — fill ROM covers
  - `python3 covers.py get SCUS-94426 <outdir>` — one PSX cover
  - `python3 covers.py get --system snes "Super Mario World (USA)" <outdir>`

## Credits

Cover images come from the
[libretro-thumbnails](https://github.com/libretro-thumbnails) project;
the PSX serial→name data comes from the Redump DAT in
[libretro-database](https://github.com/libretro/libretro-database).
Redump names belong to the Redump project. The converter frameworks
this family builds on are by Matthew Stanley (mstan) /
RetroPortingToolkit. This tool just fetches art for games you already
own and converted.
