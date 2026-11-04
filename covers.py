#!/usr/bin/env python3
"""Recomp Covers - cover art fetcher for the whole recomp family.

Started as a PSX-only companion (the single-attempt, single-host fetch
in the converter apps is how covers went missing). It now covers every
system the converter tools handle - PSX, NES, GB, GBC, SNES, GBA - plus
ROM-folder mode for the systems whose tools are still coming (N64,
Genesis, Virtual Boy, Master System, Game Gear, Nintendo DS).

Two jobs:
  * Packages: point it at a folder of extracted recomp packages (any
    system, mixed is fine - each package's system and title are
    detected). Missing art is fetched and written where the launchers
    and the library look: artwork/<key>/front.png in every package,
    and assets/img/boxart.tga (the launcher's own art) for the
    cartridge systems. PSX keys are disc serials resolved through the
    Redump DAT, as before.
  * ROM folders: point it at a folder of ROMs for one chosen system;
    art lands in <folder>/artwork/<rom name>/front.png.

Fetching policy (the family policy, learned the hard way): the
libretro-thumbnails GitHub mirror first, the canonical host second,
retries with backoff on network errors, NO retry on a 404 (a missing
name is definitive - move on to the next name variant), and every
download validated as a real image before it is written.

CLI:
    python3 covers.py scan  <folder>                 packages + art state
    python3 covers.py fetch <folder> [--replace]     fill missing covers
    python3 covers.py scan-roms  <folder> <system>   ROM folder + state
    python3 covers.py fetch-roms <folder> <system> [--replace]
    python3 covers.py get <serial> <outdir>          one PSX cover
    python3 covers.py get --system snes "Name" <outdir>

Systems: psx nes gb gbc snes gba n64 genesis vb sms gg nds
The GUI (app.py) wraps this module.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "RecompCovers/2.0"
CACHE_DIR = Path(__file__).resolve().parent / "cache"

# --------------------------------------------------------------- systems
# gh = libretro-thumbnails repo name (GitHub mirror path segment);
# canon = the same set on thumbnails.libretro.com (spaced, %-escaped).
# try_with = extra sets to try after this one (GB and GBC share most of
# their library across the two sets, so each falls through to the
# other, like the GB converter does).

SYSTEMS = {
    "psx": dict(label="PlayStation", gh="Sony_-_PlayStation",
                canon="Sony%20-%20PlayStation", exts=(), try_with=()),
    "nes": dict(label="NES", gh="Nintendo_-_Nintendo_Entertainment_System",
                canon="Nintendo%20-%20Nintendo%20Entertainment%20System",
                exts=(".nes",), try_with=()),
    "gb": dict(label="Game Boy", gh="Nintendo_-_Game_Boy",
               canon="Nintendo%20-%20Game%20Boy",
               exts=(".gb",), try_with=("gbc",)),
    "gbc": dict(label="Game Boy Color", gh="Nintendo_-_Game_Boy_Color",
                canon="Nintendo%20-%20Game%20Boy%20Color",
                exts=(".gbc", ".gb"), try_with=("gb",)),
    "snes": dict(label="SNES",
                 gh="Nintendo_-_Super_Nintendo_Entertainment_System",
                 canon="Nintendo%20-%20Super%20Nintendo%20Entertainment"
                       "%20System",
                 exts=(".sfc", ".smc"), try_with=()),
    "gba": dict(label="Game Boy Advance",
                gh="Nintendo_-_Game_Boy_Advance",
                canon="Nintendo%20-%20Game%20Boy%20Advance",
                exts=(".gba",), try_with=()),
    "n64": dict(label="Nintendo 64", gh="Nintendo_-_Nintendo_64",
                canon="Nintendo%20-%20Nintendo%2064",
                exts=(".z64", ".n64", ".v64"), try_with=()),
    "genesis": dict(label="Genesis / Mega Drive",
                    gh="Sega_-_Mega_Drive_-_Genesis",
                    canon="Sega%20-%20Mega%20Drive%20-%20Genesis",
                    exts=(".md", ".gen", ".bin", ".smd"), try_with=()),
    "vb": dict(label="Virtual Boy", gh="Nintendo_-_Virtual_Boy",
               canon="Nintendo%20-%20Virtual%20Boy",
               exts=(".vb",), try_with=()),
    "sms": dict(label="Master System",
                gh="Sega_-_Master_System_-_Mark_III",
                canon="Sega%20-%20Master%20System%20-%20Mark%20III",
                exts=(".sms",), try_with=()),
    "gg": dict(label="Game Gear", gh="Sega_-_Game_Gear",
               canon="Sega%20-%20Game%20Gear",
               exts=(".gg",), try_with=()),
    "nds": dict(label="Nintendo DS", gh="Nintendo_-_Nintendo_DS",
                canon="Nintendo%20-%20Nintendo%20DS",
                exts=(".nds",), try_with=()),
}

GH_TEMPLATE = ("https://raw.githubusercontent.com/libretro-thumbnails/"
               "{gh}/master/Named_Boxarts/{name}.png")
CANON_TEMPLATE = ("https://thumbnails.libretro.com/"
                  "{canon}/Named_Boxarts/{name}.png")


def set_urls(system, name):
    """Both host URLs for one box-art name in one system's set."""
    info = SYSTEMS[system]
    enc = urllib.parse.quote(_lr_encode(name), safe="")
    return [GH_TEMPLATE.format(gh=info["gh"], name=enc),
            CANON_TEMPLATE.format(canon=info["canon"], name=enc)]


_ILLEGAL = dict.fromkeys(map(ord, '\\/:*?"<>|'), None)


def _lr_encode(name):
    """libretro file-name encoding: illegal path chars become _."""
    return name.translate(_ILLEGAL).replace("&", "_")


# ------------------------------------------------------------------ http

def _get_once(url, timeout=25):
    """(bytes, http_status) - status None means a network-level error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception:
        return None, None


def _get_retry(url, attempts=3, timeout=25):
    """Bytes or None. A 404 is definitive: never retried (the name is
    simply not in the set; the next variant may be)."""
    for i in range(attempts):
        data, status = _get_once(url, timeout=timeout)
        if data is not None:
            return data
        if status == 404:
            return None
        if i + 1 < attempts:
            time.sleep(2 * (i + 1))
    return None


# -------------------------------------------------------------- validation

def valid_image(data):
    """PNG or JPEG magic, a sane size, and (for PNG) real dimensions."""
    if not data or len(data) < 4000:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) < 24:
            return False
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return w >= 100 and h >= 100
    if data[:3] == b"\xff\xd8\xff":
        return True
    return False


def to_png(data):
    """PNG passes through; JPEG converts via Pillow or PySide6 when
    available. Returns None when conversion is impossible here."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        out = io.BytesIO()
        im.convert("RGB").save(out, "PNG")
        return out.getvalue()
    except Exception:
        pass
    try:
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QBuffer, QIODevice
        img = QImage.fromData(data)
        if img.isNull():
            return None
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        return bytes(buf.data())
    except Exception:
        return None


def png_to_tga(png):
    """The cartridge launchers stage box art as assets/img/boxart.tga.
    Pillow first (what the converter engines use), then a hand-rolled
    uncompressed TGA from QImage pixels. None when neither works."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB")
        out = io.BytesIO()
        im.save(out, "TGA")
        return out.getvalue()
    except Exception:
        pass
    try:
        import struct
        from PySide6.QtGui import QImage
        img = QImage.fromData(png)
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        raw = bytes(img.bits())
        stride = img.bytesPerLine()
        bgra = bytearray()
        for y in range(h):
            row = raw[y * stride: y * stride + w * 4]
            for x in range(0, len(row), 4):
                r, g, b, a = row[x:x + 4]
                bgra += bytes((b, g, r, a))
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                             w, h, 32, 0x28)
        return header + bytes(bgra)
    except Exception:
        return None


def tga_ok(path):
    """A staged boxart.tga counts as present when it is a plausible
    TGA (right type, sane dimensions, non-trivial size) - or PNG
    bytes under the .tga name, which the NES converter ships and the
    launcher's image loader happily reads."""
    try:
        data = path.read_bytes()[:18]
        if len(data) < 18 or path.stat().st_size < 4000:
            return False
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return True
        if data[2] not in (2, 10):
            return False
        w = int.from_bytes(data[12:14], "little")
        h = int.from_bytes(data[14:16], "little")
        return w >= 50 and h >= 50
    except OSError:
        return False


# ---------------------------------------------------------------- serials

SERIAL_DASH = re.compile(r"\b([A-Z]{4})-(\d{5})\b")
SERIAL_DOTTED = re.compile(r"\b([A-Z]{4})[-_](\d{3})\.(\d{2})\b")
SERIAL_PLAIN = re.compile(r"\b([A-Z]{4})(\d{5})\b")
ARTWORK_SERIAL = re.compile(r"^[A-Z]{4}-\d{5}$")

REDUMP_DAT_URL = ("https://raw.githubusercontent.com/libretro/"
                  "libretro-database/master/metadat/redump/"
                  "Sony%20-%20PlayStation.dat")


def normalize_serial(text):
    """Find a PSX serial in text, normalized to 'SCUS-94426' form."""
    if not text:
        return None
    m = SERIAL_DASH.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = SERIAL_DOTTED.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}{m.group(3)}"
    m = SERIAL_PLAIN.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def redump_names(serial, cache_dir=CACHE_DIR):
    """The game's Redump name for a serial, from the cached DAT.
    Downloads the DAT once. Returns [] when unknown / offline."""
    if not serial:
        return []
    dat = Path(cache_dir) / "redump-psx.dat"
    try:
        if not dat.exists() or dat.stat().st_size < 100000:
            data = _get_retry(REDUMP_DAT_URL, timeout=60)
            if not data:
                return []
            dat.parent.mkdir(parents=True, exist_ok=True)
            dat.write_bytes(data)
        text = dat.read_text(errors="replace")
    except OSError:
        return []
    for block in text.split("\ngame ("):
        if f'\tserial "{serial}"' in block:
            m = re.search(r'\tname "([^"]+)"', block)
            if m:
                return [m.group(1)]
    return []


def redump_variants(names):
    """Redump-name expansion: as-is, plus a leading 'ABBR - ' prefix
    stripped. Exact Redump name first."""
    out = []
    for n in names:
        if n not in out:
            out.append(n)
        if " - " in n:
            stripped = n.split(" - ", 1)[1]
            if stripped not in out:
                out.append(stripped)
    return out


# ----------------------------------------------------------- name variants

def name_variants(title, system=None):
    """Candidate Named_Boxarts file names for a cartridge title, best
    first. Assembled from the converter engines' own variant rules:
    the ROM's No-Intro stem as-is, dump flags ([!], [b]...) stripped,
    region groups normalized ((JUE) -> (World)), (V1.1) -> (Rev 1),
    the bare core title, and core + common region suffixes. GB/GBC
    titles also get the enhancement-suffix forms No-Intro uses
    ('(SGB Enhanced) (GB Compatible)') that hid Tetris DX's art."""
    title = (title or "").strip()
    if not title:
        return []
    out = []

    def add(n):
        n = (n or "").strip()
        if n and n not in out:
            out.append(n)

    add(title)
    no_brackets = re.sub(r"\s*\[[^\]]*\]", "", title).strip()
    add(no_brackets)
    norm = re.sub(r"\((JUE|JU|UE|U|J)\)", "(World)", no_brackets)
    norm = re.sub(r"\(V(\d+)\.(\d+)\)", r"(Rev \2)", norm)
    add(norm)
    core = title.split(" (")[0].strip()
    add(core)
    for base in (core, norm.split(" (")[0].strip()):
        for suffix in (" (USA)", " (USA, Europe)", " (World)"):
            add(base + suffix)
    if system in ("gb", "gbc"):
        for base in list(out):
            add(base + " (SGB Enhanced) (GB Compatible)")
            add(base + " (SGB Enhanced)")
            add(base + " (GB Compatible)")
    return out


# ------------------------------------------------------------------ fetch

def _cache_path(system, name):
    return CACHE_DIR / system / (_lr_encode(name) + ".png")


def _try_name(syskey, name):
    """One exact name in one set: cache, then mirror, then canonical.
    Returns (png, desc) or (None, None)."""
    cache = _cache_path(syskey, name)
    if cache.is_file():
        data = cache.read_bytes()
        if valid_image(data):
            return data, f"cache ({name})"
    for url in set_urls(syskey, name):
        data = _get_retry(url)
        if data and valid_image(data):
            png = to_png(data)
            if png:
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(png)
                except OSError:
                    pass
                host = ("GitHub mirror" if "githubusercontent" in url
                        else "libretro")
                return png, f"{host} [{syskey}] ({name})"
    return None, None


# ------------------------------------------- set listing (fuzzy fallback)
# Some sets (the Sega ones especially) name files with older No-Intro
# conventions - "Sonic The Hedgehog (Japan, USA) (En).png" - that no
# amount of variant generation will guess. As a last resort, list the
# set's real file names (a tree-only git clone: ~200 KB, a second or
# two), cache the list for a week, and match on core title + regions.

LISTING_TTL = 7 * 24 * 3600
REGION_WORDS = {
    "usa", "europe", "japan", "world", "australia", "brazil", "korea",
    "canada", "france", "germany", "spain", "italy", "netherlands",
    "sweden", "denmark", "finland", "norway", "asia", "taiwan",
    "china", "russia", "mexico", "portugal", "greece", "india",
    "israel", "uk", "scandinavia", "latin america", "new zealand",
}


def set_listing(system, log=print):
    """All Named_Boxarts stems in a system's set, cached a week."""
    dest = CACHE_DIR / "listings" / f"{system}.json"
    stale = []
    try:
        blob = json.loads(dest.read_text())
        stale = blob.get("names", [])
        if time.time() - blob.get("ts", 0) < LISTING_TTL and stale:
            return stale
    except Exception:
        pass
    if not shutil.which("git"):
        log(f"[{system}] git not found - cannot list the set for a "
            f"fuzzy match")
        return stale
    tmp = Path(tempfile.mkdtemp(prefix="covers-list-"))
    try:
        repo = ("https://github.com/libretro-thumbnails/"
                + SYSTEMS[system]["gh"])
        subprocess.run(["git", "clone", "--quiet", "--depth", "1",
                        "--filter=tree:0", "--no-checkout", repo,
                        str(tmp / "repo")],
                       check=True, capture_output=True, timeout=300)
        out = subprocess.run(
            ["git", "-C", str(tmp / "repo"), "ls-tree", "-r",
             "--name-only", "HEAD"],
            check=True, capture_output=True, timeout=300).stdout
        names = [line[len("Named_Boxarts/"):-len(".png")]
                 for line in out.decode(errors="replace").splitlines()
                 if line.startswith("Named_Boxarts/")
                 and line.endswith(".png")]
        if names:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps({"ts": time.time(),
                                        "names": names}))
            return names
    except Exception as e:
        log(f"[{system}] set listing failed ({e})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return stale


def _core(name):
    return _lr_encode(name).split(" (")[0].strip().lower()


def _regions(name):
    found = set()
    for group in re.findall(r"\(([^)]*)\)", name):
        for tok in re.split(r"[,+]", group):
            tok = tok.strip().lower()
            if tok in REGION_WORDS:
                found.add(tok)
    return found


def _penalty_tags(name):
    low = name.lower()
    return any(t in low for t in ("(proto)", "(beta)", "[hack",
                                  "(sample)", "(demo)", "(unl)"))


def fuzzy_candidates(system, title, log=print):
    """Best set-listing matches for a title that no exact variant hit:
    same core title (case-insensitive), the title's regions all
    present in the candidate, fewest extras and no proto/beta/hack
    penalty first."""
    names = set_listing(system, log)
    core, want = _core(title), _regions(title)
    scored, partial = [], []
    for n in names:
        if _core(n) != core:
            continue
        regs = _regions(n)
        score = 100 - 4 * len(regs - want) - len(n)
        if _penalty_tags(n) and not _penalty_tags(title):
            score -= 25
        if want and not want.issubset(regs):
            # Regional dump splits (Game Gear Sonic is Japan+USA or
            # Japan+Europe, never both) - keep as a fallback ranked
            # by how many of the wanted regions it does cover.
            partial.append((score + 12 * len(want & regs), n))
            continue
        scored.append((score, n))
    pool = scored or partial
    pool.sort(key=lambda t: -t[0])
    return [n for _, n in pool[:6]]


def fetch_named(system, title, log=print):
    """Fetch box art for a cartridge title. Returns (png_bytes, desc)
    or (None, None). Exact name variants first (system's set, then
    its fall-through sets, GB <-> GBC); if none hit, a fuzzy match
    against the set's real file listing."""
    systems = [system] + [s for s in SYSTEMS[system]["try_with"]
                          if s != system]
    for name in name_variants(title, system):
        for syskey in systems:
            data, desc = _try_name(syskey, name)
            if data:
                return data, desc
    for syskey in systems:
        for cand in fuzzy_candidates(syskey, title, log):
            data, desc = _try_name(syskey, cand)
            if data:
                return data, f"listing match ({cand})"
    return None, None


def fetch_serial(serial, extra_names=(), log=print):
    """PSX: fetch one cover by disc serial via its Redump name.
    Returns (png_bytes, desc) or (None, None)."""
    names = redump_variants(redump_names(serial) + list(extra_names))
    if not names:
        log(f"{serial}: no Redump name known - sources need a name")
        return None, None
    for name in names:
        data, desc = _try_name("psx", name)
        if data:
            return data, desc
    for cand in fuzzy_candidates("psx", names[0], log):
        data, desc = _try_name("psx", cand)
        if data:
            return data, f"listing match ({cand})"
    return None, None


# -------------------------------------------------------------- packages
# A package "payload dir" is the folder that holds artwork/ (GBA
# packages nest it one level down, in a linux/ or windows/ subfolder).
# System + title detection, in order: PSX serials, the README's first
# line (every converter writes "<Title> - native PC recompilation
# (<Tool>)" there), the GBA game.toml, launcher brand assets, and an
# existing artwork/<key> folder.

README_PATTERNS = [
    (re.compile(r"^(.*?) — native PC recompilation \(NESRecomp"), "nes"),
    (re.compile(r"^(.*?) — native PC recompilation \(GBRecomp"), "gb"),
    (re.compile(r"^(.*?) — native PC recompilation \(SNESRecomp"), "snes"),
    (re.compile(r"^(.*?) - native PC recompilation \(Game Boy Advance\)"),
     "gba"),
]
BRAND_SYSTEMS = [("brand_nes.tga", "nes"), ("brand_gb.tga", "gb"),
                 ("brand_gbc.tga", "gb"), ("brand_psx.tga", "psx"),
                 ("pad_gba.tga", "gba")]


def _first_line(path):
    try:
        with open(path, errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""


def _payload_dirs(root):
    root = Path(root)
    if not root.is_dir():
        return []
    candidates = [root]
    frontier = [root]
    for _depth in range(3):
        nxt = []
        for base in frontier:
            try:
                nxt += sorted(d for d in base.iterdir() if d.is_dir())
            except OSError:
                pass
        candidates += nxt
        frontier = nxt
    out = []
    for d in candidates:
        if ((d / "game.toml").is_file() or (d / "artwork").is_dir()
                or (d / "assets" / "img").is_dir()
                or "recompilation" in _first_line(d / "README.txt")
                or "recompilation" in _first_line(d / "README.md")):
            if d not in out:
                out.append(d)
    return out


def detect_package(d, forced_system=None):
    """Entries for one payload dir: one per PSX serial, else one."""
    d = Path(d)
    serials = []
    art = d / "artwork"
    if art.is_dir():
        for sub in sorted(art.iterdir()):
            if sub.is_dir() and ARTWORK_SERIAL.match(sub.name):
                serials.append(sub.name)
    for name in ("game.toml", "README.md", "README.txt"):
        s = normalize_serial(_safe_read(d / name))
        if s and s not in serials:
            serials.append(s)
    if serials and forced_system in (None, "psx"):
        return [dict(dir=d, art_root=d, system="psx", key=s, title=None,
                     serial=s) for s in serials]

    system, title = forced_system, None
    for name in ("README.txt", "README.md"):
        line = _first_line(d / name)
        for pat, syskey in README_PATTERNS:
            m = pat.match(line)
            if m:
                if system is None:
                    system = syskey
                title = title or m.group(1).strip()
                break
        if title:
            break
    toml = _safe_read(d / "game.toml")
    if toml:
        m = re.search(r'\[game\]\s*\nname\s*=\s*"([^"]+)"', toml)
        if m:
            title = title or m.group(1)
        if system is None and ("0x08000000" in toml
                               or 'screen = "frontlit"' in toml):
            system = "gba"
    if system is None:
        img = d / "assets" / "img"
        if img.is_dir():
            for brand, syskey in BRAND_SYSTEMS:
                if (img / brand).is_file():
                    system = syskey
                    break
    key = None
    if art.is_dir():
        subs = [s.name for s in sorted(art.iterdir()) if s.is_dir()]
        if subs:
            key = subs[0]
    key = key or title or d.name
    title = title or key
    return [dict(dir=d, art_root=d, system=system, key=key, title=title,
                 serial=None)]


def _safe_read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def find_packages(root, forced_system=None):
    entries = []
    for d in _payload_dirs(root):
        entries.extend(detect_package(d, forced_system))
    return entries


# ------------------------------------------------------------------ fill

def front_dest(entry):
    return entry["art_root"] / "artwork" / entry["key"] / "front.png"


def wants_tga(entry):
    return (entry["system"] != "psx"
            and (entry["art_root"] / "assets" / "img").is_dir())


def front_ok(entry):
    dest = front_dest(entry)
    try:
        if not dest.is_file() or dest.stat().st_size < 4000:
            return False
        with open(dest, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def entry_state(entry):
    """'have', 'partial' (front.png present, launcher TGA missing) or
    'NO ART'."""
    if not front_ok(entry):
        return "NO ART"
    if wants_tga(entry) and not tga_ok(
            entry["art_root"] / "assets" / "img" / "boxart.tga"):
        return "partial"
    return "have"


def _write_atomic(dest, data):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


def fill_entry(entry, log=print, replace=False):
    """Fetch/heal one entry's art. Returns a status string."""
    dest = front_dest(entry)
    png = None
    status = None
    if front_ok(entry) and not replace:
        status = "have"
        if wants_tga(entry):
            try:
                png = dest.read_bytes()
            except OSError:
                png = None
    else:
        if entry["system"] == "psx":
            data, desc = fetch_serial(entry["serial"],
                                      log=log)
        elif entry["system"]:
            data, desc = fetch_named(entry["system"],
                                     entry["key"] or entry["title"],
                                     log=log)
        else:
            return "unknown system - skipped"
        if data:
            _write_atomic(dest, data)
            png = data
            status = f"fetched from {desc}"
        else:
            return "missing"
    if wants_tga(entry):
        tga_path = entry["art_root"] / "assets" / "img" / "boxart.tga"
        if (replace or not tga_ok(tga_path)) and png:
            tga = png_to_tga(png)
            if tga:
                _write_atomic(tga_path, tga)
                status += " + boxart.tga"
            else:
                log(f"{entry['key']}: boxart.tga conversion unavailable "
                    f"here (needs Pillow or PySide6)")
    return status


# -------------------------------------------------------------- ROM mode

def scan_roms(root, system):
    """Entries for every ROM of one system under root (recursive).
    Art lands in <root>/artwork/<rom stem>/front.png."""
    root = Path(root)
    exts = SYSTEMS[system]["exts"]
    entries = []
    if not root.is_dir():
        return entries
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        if "artwork" in f.relative_to(root).parts:
            continue
        entries.append(dict(dir=f.parent, art_root=root, system=system,
                            key=f.stem, title=f.stem, serial=None,
                            rom=f))
    return entries


# -------------------------------------------------------------------- cli

def _print_entries(entries):
    for e in entries:
        label = SYSTEMS[e["system"]]["label"] if e["system"] else "?"
        print(f"{e['dir'].name} [{label}] {e['key']}: {entry_state(e)}")


def main(argv):
    args = [a for a in argv[1:] if a != "--replace"]
    replace = "--replace" in argv
    if len(args) < 2:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "scan":
        _print_entries(find_packages(args[1]))
        return 0
    if cmd == "fetch":
        for e in find_packages(args[1]):
            print(f"== {e['dir'].name} [{e['system']}] {e['key']}")
            print(f"   {fill_entry(e, replace=replace)}")
        return 0
    if cmd in ("scan-roms", "fetch-roms") and len(args) >= 3:
        system = args[2].lower()
        if system not in SYSTEMS or system == "psx":
            print(f"unknown ROM system: {system} "
                  f"(choose from {', '.join(k for k in SYSTEMS if k != 'psx')})")
            return 2
        entries = scan_roms(args[1], system)
        if cmd == "scan-roms":
            _print_entries(entries)
        else:
            for e in entries:
                print(f"== {e['key']}")
                print(f"   {fill_entry(e, replace=replace)}")
        return 0
    if cmd == "get":
        rest = args[1:]
        system = None
        if rest and rest[0] == "--system":
            system = rest[1].lower()
            rest = rest[2:]
        if len(rest) < 2:
            print(__doc__)
            return 2
        name, outdir = rest[0], Path(rest[1])
        if system is None and normalize_serial(name):
            serial = normalize_serial(name)
            data, desc = fetch_serial(serial)
            key = serial
        elif system:
            data, desc = fetch_named(system, name)
            key = name
        else:
            print("get needs a PSX serial, or --system <sys> and a name")
            return 2
        if data:
            dest = outdir / "artwork" / key / "front.png"
            _write_atomic(dest, data)
            print(f"{key}: fetched from {desc} -> {dest}")
            return 0
        print(f"{name}: no source had it")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
