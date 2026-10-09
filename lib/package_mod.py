"""Pack an R5Flowstate mod folder into a Thunderstore upload zip.

    py build.py <ModFolder> --team <ThunderstoreTeam>

Checks the same rules the launcher enforces at install, so a package that
builds here installs there.
"""
import argparse
import json
import re
import struct
import sys
import zipfile
from pathlib import Path

ALLOWED_EXT = {
    "", ".nut", ".gnut", ".rson", ".txt", ".vdf", ".cfg", ".json", ".md", ".csv", ".res", ".menu",
    ".png", ".jpg", ".jpeg", ".webp", ".dds",
    ".rpak", ".starpak", ".mbnk", ".mstr", ".mprj", ".raw_hdr",
    ".bsp", ".bsp_lump", ".ent", ".kv", ".vpk", ".nm", ".ain",
    ".ttf", ".otf",
}
MOD_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._]{3,31}$")
TS_NAME = re.compile(r"^[A-Za-z0-9_]+$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
MAP_CHARS = re.compile(r"^[a-z0-9_]+$")
SEPARATOR = "__"
FIXED_FILES = {
    "mod.vdf", "manifest.json", "icon.png", "readme.md", "changelog.md",
    "license", "license.md", "license.txt",
    "scripts/vscripts/scripts.rson", "paks/win64/preload.rson", "paks/win64_server/preload.rson",
    "playlists_r5_patch.txt",
}
MAX_DEPTH = 8
MAX_COMPRESSION_RATIO = 100  # launcher ModInstaller.MaxCompressionRatio
MAX_OVERRIDES = 64
DATATABLE_NAME = re.compile(r"^[a-z0-9_]{1,64}(/[a-z0-9_]{1,64})?$")
LOC_TOKEN = re.compile(r"^[A-Za-z0-9_]{1,96}$")
PROTECTED_LOC = ("SDK_", "BRIDGE_", "EULA")
PROTECTED_LOC_TOKENS = ("YES", "NO", "OK", "CANCEL")
SCRIPT_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
FEATURE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_FEATURES = 64
PROTECTED_WRAP = ("modsystem", "sdk", "bridge", "agent", "launcher", "scriptconvar", "eula")
WRAP_VMS = {"SERVER", "CLIENT", "UI"}
MAX_WRAPS = 64
MAX_PLAYLIST_ID = 31


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def vdf_value(text, key):
    m = re.search(r'"' + re.escape(key) + r'"\s+"([^"]*)"', text)
    return m.group(1) if m else None


def vdf_block_keys(text, block):
    m = re.search(r'"' + re.escape(block) + r'"\s*\{([^{}]*)\}', text, re.IGNORECASE)
    return re.findall(r'"([^"]+)"\s+"[^"]*"', m.group(1)) if m else []


def leads_with(comp, token):
    return comp.startswith(token) and (len(comp) == len(token) or comp[len(token)] in "_.")


def embeds_map(comp, m):
    if leads_with(comp, m):
        return True
    return any(comp[i - 1] == "_" and comp[i:i + len(m)] == m and i + len(m) < len(comp)
               and comp[i + len(m)] in "_." for i in range(1, len(comp)))


def owns(namespace, maps, tables, rel, install=None):
    """Same rule as the engine: fixed files, "<namespace>__*" names, a folder named
    exactly <namespace> where the game has none, declared maps and stock tables."""
    rel = rel.lower()
    if rel in FIXED_FILES or any(rel == "datatable/%s.csv" % t for t in tables):
        return True
    parts = rel.split("/")
    for i, comp in enumerate(parts):
        if comp.startswith(namespace + SEPARATOR) and len(comp) > len(namespace) + len(SEPARATOR):
            return True
        if i < len(parts) - 1 and comp == namespace:
            prefix = Path(*parts[:i + 1])
            if install is None or not ((install / prefix).is_dir() or (install / "platform" / prefix).is_dir()):
                return True
        if any(embeds_map(comp, m) for m in maps):
            return True
    return False


def check_localization(root, vdf, namespace, loc_overrides):
    """Keys a mod may define: its own <NAMESPACE>__* tokens and the declared overrides."""
    allowed = {t.lower() for t in loc_overrides}
    for rel in vdf_block_keys(vdf, "LocalizationFiles"):
        path = root / rel
        if not path.is_file():
            fail("LocalizationFiles lists a missing file: " + rel)
        text = path.read_text(encoding="utf-8-sig")
        for key in re.findall(r'^\s*"([^"]+)"\s+"', text, re.MULTILINE):
            name = key[1:] if key.startswith("#") else key
            low = name.lower()
            if low == "language":
                continue
            if low.startswith(namespace + SEPARATOR) or low in allowed:
                continue
            fail("%s defines '%s'; mods may only define %s%s* keys and declared LocalizationOverrides"
                 % (rel, key, namespace.upper(), SEPARATOR))


def vdf_nested_block(text, block):
    m = re.search(r'"' + re.escape(block) + r'"\s*\{', text, re.IGNORECASE)
    if not m:
        return ""
    depth, i = 1, m.end()
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i - 1]


def check_script_wraps(root, vdf, namespace):
    """Same rules as the engine: "<namespace>__*" wrapper defined in the mod's scripts,
    unprotected target, known VMs, one wrap per target per VM."""
    body = vdf_nested_block(vdf, "ScriptWraps")
    wraps = re.findall(r'"([^"]+)"\s*\{([^{}]*)\}', body)
    if len(wraps) > MAX_WRAPS:
        fail("more than %d script wraps" % MAX_WRAPS)
    sources = "\n".join(p.read_text(encoding="utf-8-sig", errors="replace")
                        for p in (root / "scripts" / "vscripts").rglob("*") if p.suffix.lower() in (".nut", ".gnut"))
    seen = set()
    for target, inner in wraps:
        fn = vdf_value(inner, "Function") or ""
        vms = (vdf_value(inner, "VM") or "SERVER").replace(",", " ").replace("|", " ").split()
        if not SCRIPT_IDENT.match(target) or SEPARATOR in target or target.lower().startswith(PROTECTED_WRAP):
            fail("script wrap target '%s' is not a wrappable base function" % target)
        if not (SCRIPT_IDENT.match(fn) and fn.lower().startswith(namespace + SEPARATOR) and len(fn) > len(namespace) + 2):
            fail("script wrap '%s' function '%s' must be named %s%s*" % (target, fn, namespace, SEPARATOR))
        if not vms or any(v.upper() not in WRAP_VMS for v in vms):
            fail("script wrap '%s' VM must be SERVER, CLIENT and/or UI" % target)
        for v in vms:
            if (target, v.upper()) in seen:
                fail("script wrap '%s' is declared twice for %s" % (target, v.upper()))
            seen.add((target, v.upper()))
        if not re.search(r"function[ \t]+" + re.escape(fn) + r"[ \t]*\(", sources):
            fail("script wrap '%s' names '%s', which none of the mod's scripts define" % (target, fn))


def check_requires_features(vdf):
    """Shape only: feature names are engine-defined, so membership is judged at
    load, not at pack time. A mod that names a feature this build lacks is
    refused with [MOD-POLICY], not here."""
    body = vdf_nested_block(vdf, "RequiresFeatures")
    feats = re.findall(r'"([^"]+)"\s+"([^"]*)"', body)
    if len(feats) > MAX_FEATURES:
        fail("more than %d required features" % MAX_FEATURES)
    for name, value in feats:
        if not FEATURE_NAME.match(name):
            fail("required feature '%s' is not a plain feature name" % name)
        if value not in ("0", "1"):
            fail("required feature '%s' must be \"0\" or \"1\"" % name)


def check_playlist_ids(root):
    """The engine keeps playlist names in 32-byte buffers and skips longer ids."""
    path = root / "playlists_r5_patch.txt"
    if not path.is_file():
        return
    text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8", errors="replace"))
    tokens = re.findall(r'"[^"]*"|[{}]|[^\s{}"]+', text)
    depth = 0
    for i, tok in enumerate(tokens):
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
        elif depth == 2 and i + 1 < len(tokens) and tokens[i + 1] == "{":
            name = tok.strip('"')
            if len(name) > MAX_PLAYLIST_ID:
                fail("playlist id '%s' is %d chars; the engine skips ids over %d"
                     % (name, len(name), MAX_PLAYLIST_ID))


def png_size(path):
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        fail("icon.png is not a PNG")
    return struct.unpack(">II", head[16:24])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--team", required=True, help="Thunderstore team the package is uploaded under")
    ap.add_argument("--out", default="dist")
    args = ap.parse_args()

    root = Path(args.folder).resolve()
    for required in ("manifest.json", "icon.png", "README.md", "mod.vdf"):
        if not (root / required).is_file():
            fail("missing " + required)

    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    name, version = man.get("name", ""), man.get("version_number", "")
    if not TS_NAME.match(name):
        fail("manifest name must be letters, digits and '_' only: " + name)
    if not SEMVER.match(version):
        fail("manifest version_number must be Major.Minor.Patch: " + version)
    if len(man.get("description", "")) > 250:
        fail("manifest description exceeds 250 characters")
    if not isinstance(man.get("dependencies"), list) or "website_url" not in man:
        fail("manifest needs dependencies (list) and website_url")

    if png_size(root / "icon.png") != (256, 256):
        fail("icon.png must be 256x256")

    vdf = (root / "mod.vdf").read_text(encoding="utf-8")
    mod_id = vdf_value(vdf, "id") or ""
    expected = args.team + "." + name
    if mod_id != expected:
        fail("mod.vdf id is '%s'; the launcher requires '%s'" % (mod_id, expected))
    if not MOD_ID.match(mod_id):
        fail("mod id '%s' must be 4-32 letters, digits, '.' or '_'" % mod_id)
    if vdf_value(vdf, "version") != version:
        fail("mod.vdf version must match manifest version_number")

    namespace = mod_id.replace(".", "_").lower()
    if SEPARATOR in namespace:
        fail("mod id '%s' reads as '%s' in file names; pick another id" % (mod_id, SEPARATOR))

    map_prefix = "mp_" + namespace + SEPARATOR
    maps = [m.lower() for m in vdf_block_keys(vdf, "Maps")]
    for m in maps:
        if not (m.startswith(map_prefix) and len(map_prefix) < len(m) <= 63 and MAP_CHARS.match(m)):
            fail("map '%s' must be named %s<name> (letters, digits, '_', at most 63)" % (m, map_prefix))

    weapons = [w.lower() for w in vdf_block_keys(vdf, "Weapons")]
    for w in weapons:
        if not (w.startswith("mp_weapon_") and len("mp_weapon_") < len(w) <= 63 and MAP_CHARS.match(w)):
            fail("weapon '%s' must be named mp_weapon_<name> (letters, digits, '_', at most 63)" % w)
    weapon_files = {"scripts/weapons/%s.txt" % w for w in weapons}

    tables = [t.lower() for t in vdf_block_keys(vdf, "DatatableOverrides")]
    for t in tables:
        if not DATATABLE_NAME.match(t) or len(tables) > MAX_OVERRIDES:
            fail("invalid or excess datatable override '%s'" % t)

    loc_overrides = [t.lstrip("#") for t in vdf_block_keys(vdf, "LocalizationOverrides")]
    for t in loc_overrides:
        protected = t.upper() in PROTECTED_LOC_TOKENS or any(t.upper().startswith(p) for p in PROTECTED_LOC)
        if not LOC_TOKEN.match(t) or protected or len(loc_overrides) > MAX_OVERRIDES:
            fail("mods may not override localization token '%s'" % t)
    check_localization(root, vdf, namespace, loc_overrides)
    check_script_wraps(root, vdf, namespace)
    check_requires_features(vdf)
    check_playlist_ids(root)

    files = sorted(p for p in root.rglob("*") if p.is_file())
    for p in files:
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            fail("links are not allowed: " + rel)
        if rel.count("/") > MAX_DEPTH:
            fail("folders nest deeper than %d: %s" % (MAX_DEPTH, rel))
        if rel.lower() not in weapon_files and not owns(namespace, maps, tables, rel):
            fail("'%s' is outside the namespace '%s', the declared maps and datatable overrides" % (rel, namespace))
        if p.suffix.lower() not in ALLOWED_EXT:
            fail("file type not allowed in a mod: " + rel)
        head = p.read_bytes()[:4]
        if head[:2] == b"MZ" or head == b"\x7fELF":
            fail("executable image: " + rel)

    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).parent / out
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / ("%s-%s-%s.zip" % (args.team, name, version))
    # The launcher refuses an entry that deflates past MAX_COMPRESSION_RATIO (its
    # zip-bomb guard), so an entry that would is stored instead.
    stored = set()
    while True:
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in files:
                rel = p.relative_to(root).as_posix()
                z.write(p, rel, zipfile.ZIP_STORED if rel in stored else zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(zpath) as z:
            over = {i.filename for i in z.infolist()
                    if i.compress_size > 0 and i.file_size > i.compress_size * MAX_COMPRESSION_RATIO}
        if not over:
            break
        if over <= stored:
            fail("entries still exceed the compression ratio cap: " + ", ".join(sorted(over)))
        stored |= over
    for rel in sorted(stored):
        print("stored (compresses past %dx): %s" % (MAX_COMPRESSION_RATIO, rel))
    print("OK  %s  (%d files, id %s)" % (zpath, len(files), mod_id))


if __name__ == "__main__":
    main()
