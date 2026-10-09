#!/usr/bin/env python3
"""Melee modding: a Blender mesh on a stock heirloom rig -> an installable R5Flowstate mod.

  python melee_modding.py hosts                         list the rigs you can build on
  python melee_modding.py new <key> --host <host> --mod <Team>.<Name> [--name "Display Name"]
  python melee_modding.py build <key> [--install]

`new` makes heirlooms/<key>/ with spec.json and the host skeletons to model on. `build`
compiles the SMDs, converts and aligns them to the host, encodes textures, authors the
materials, packs client + server paks, writes the weapon files, gates everything and packages
the mod (Thunderstore zip); --install also copies it into <game>/mods and enables it.
Tool locations: config.json (see config.example.json), or MELEE_CONFIG = path to one, or
MELEE_* variables per tool. MELEE_WORKSPACE moves heirlooms/ and work/ out of the kit folder.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "lib"))
from align_rmdl_to_host import align  # noqa: E402
import rmdl_v17_dump as rdump  # noqa: E402
from string_to_guid import string_to_guid  # noqa: E402

_STAGE = {"client": Path(), "dedi": Path()}


def cs() -> Path:
    return _STAGE["client"]


def ds() -> Path:
    return _STAGE["dedi"]


def set_stage(client: Path, dedi: Path) -> None:
    _STAGE["client"], _STAGE["dedi"] = client, dedi


# your heirlooms and the build scratch live outside the kit when MELEE_WORKSPACE is set
WORKSPACE = Path(os.environ.get("MELEE_WORKSPACE") or ROOT)
HEIRLOOMS = WORKSPACE / "heirlooms"
WORK = WORKSPACE / "work"
ORACLE = WORK / "oracle"
SEED = json.loads((ROOT / "hosts" / "seed.json").read_text(encoding="utf-8"))
VERIFY = ROOT / "lib" / "verify_rmdl_v17.ps1"

CONFIG_KEYS = {"game_dir": "MELEE_GAME_DIR", "rmdlconv": "MELEE_RMDLCONV", "rsx": "MELEE_RSX",
               "repak": "MELEE_REPAK", "texconv": "MELEE_TEXCONV"}


def load_config() -> dict[str, Path]:
    f = Path(os.environ.get("MELEE_CONFIG") or ROOT / "config.json")
    cfg = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    out = {}
    for key, env in CONFIG_KEYS.items():
        v = os.environ.get(env) or cfg.get(key)
        if not v:
            raise SystemExit(f"config: set \"{key}\" in config.json (copy config.example.json) or {env}")
        p = Path(v)
        if not p.exists():
            raise SystemExit(f"config: {key} = {p} does not exist")
        out[key] = p
    if not (out["game_dir"] / "paks" / "Win64" / "common.rpak").is_file():
        raise SystemExit(f"config: game_dir {out['game_dir']} has no paks/Win64/common.rpak (point it at the game folder)")
    return out


CFG = load_config()
GAME = CFG["game_dir"]
RMDLCONV, RSX, REPAK, TEXCONV = CFG["rmdlconv"], CFG["rsx"], CFG["repak"], CFG["texconv"]
GAME_PAKS = [GAME / "paks" / "Win64" / n for n in ("common.rpak", "common_mp.rpak", "common_early.rpak")]
GAME_DEDI_PAK = GAME / "paks" / "Win64_server" / "common.rpak"
# the game's own weapon files; the kit replaces their model paths
GAME_WEAPONS = GAME / "platform" / "scripts" / "weapons"
HOSTS_CACHE = WORK / "hosts.json"
PARITY_KEYS = GAME / "platform" / "scripts" / "vscripts" / "sh_mod_heirloom_parity_keys.gnut"

tempfile.tempdir = str(WORK / "tmp")
os.makedirs(tempfile.tempdir, exist_ok=True)

STUDIOHDR_STATIC_PROP = 0x10
STUDIOHDR_VIRTUAL_MODEL = 0x80000
BONE_VERTEX_MASK = 0x3FC00

# texture slot type -> (texconv format, accepted fourcc/dxgi for passthrough)
SLOT_FMT = {
    "albedoTexture": ("BC7_UNORM", {b"DX10:98"}),
    "normalTexture": ("BC5_UNORM", {b"BC5U", b"DX10:83"}),
    "glossTexture": ("BC4_UNORM", {b"BC4U", b"DX10:80"}),
    "specTexture": ("BC7_UNORM", {b"DX10:98"}),
    "emissiveTexture": ("BC7_UNORM", {b"DX10:98"}),
    "aoTexture": ("BC4_UNORM", {b"BC4U", b"DX10:80"}),
    "cavityTexture": ("BC4_UNORM", {b"BC4U", b"DX10:80"}),
    "opacityMultiplyTexture": ("BC4_UNORM", {b"BC4U", b"DX10:80"}),
}
SLOT_FALLBACK_FMT = ("BC7_UNORM", {b"DX10:98"})


class _Hosts(dict):
    """Host table, generated on first use from the game's own paks (work/hosts.json)."""

    def _load(self):
        if not dict.__len__(self):
            dict.update(self, load_hosts())

    def __getitem__(self, k):
        self._load()
        return dict.__getitem__(self, k)

    def __contains__(self, k):
        return k in SEED

    def __iter__(self):
        return iter(sorted(SEED))

    def items(self):
        self._load()
        return dict.items(self)


HOSTS = _Hosts()


def log(*a):
    print(*a, flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        raise SystemExit(f"FAILED ({r.returncode}): {' '.join(str(c) for c in cmd)}")
    return r


def guid_hex(path: str) -> str:
    return "0x%016X" % string_to_guid(path)


def staged(p: Path, root: Path | None = None) -> Path:
    root = root or cs()
    real = Path(os.path.realpath(p))
    if Path(os.path.realpath(root)) not in real.parents:
        raise SystemExit(f"refusing write outside {root.name}: {p} -> {real}")
    return p


# ---------------------------------------------------------------- specs
def load_spec(key: str) -> dict:
    f = Path(key) if key.endswith(".json") else HEIRLOOMS / key / "spec.json"
    if not f.is_file():
        raise SystemExit(f"no spec at {f} (run: melee_modding.py new {key} --host <host> --mod <Team>.<Name>)")
    s = json.loads(f.read_text(encoding="utf-8"))
    if s["host"] not in HOSTS:
        raise SystemExit(f"{f}: unknown host '{s['host']}' (hosts: {', '.join(sorted(HOSTS))})")
    if not re.fullmatch(r"[a-z0-9_]{2,32}", s["key"]):
        raise SystemExit(f"{f}: key must be [a-z0-9_]{{2,32}}")
    if not s.get("mod"):
        raise SystemExit(f"{f}: needs a \"mod\" block")
    s.setdefault("source_root", str(f.parent))
    s["mod"].setdefault("dir", str(f.parent / "mod"))
    return s


def source_path(spec: dict, role: str) -> Path:
    rel = spec.get(role + "_src") or spec[role]
    if isinstance(rel, list):
        rel = rel[0]
    p = Path(rel)
    return p if p.is_absolute() else Path(spec["source_root"]) / rel


def source_smds(spec: dict, role: str) -> list[Path]:
    rel = spec.get(role + "_src") or []
    rels = rel if isinstance(rel, list) else [rel]
    paths = [Path(r) if Path(r).is_absolute() else Path(spec["source_root"]) / r for r in rels]
    return paths if paths and all(p.suffix.lower() == ".smd" for p in paths) else []


def compile_smd_source(spec: dict, role: str, host_model: Path, out_dir: Path) -> Path:
    """SMD sources compile here (smd2mdl) into the MDL v49 rmdlconv reads. Each SMD material
    maps to the spec material whose name ends in it ("smd_material" overrides)."""
    import smd2mdl
    smds = source_smds(spec, role)
    rename = {}
    for m in spec["materials"]:
        rename[m.get("smd_material", m["name"].rsplit("/", 1)[-1])] = m["name"] + "_sknp"
    used = set()
    for p in smds:
        used |= {mat for mat, _ in smd2mdl.parse_smd(p)["tris"]}
    unmapped = sorted(used - set(rename))
    if unmapped:
        raise SystemExit(f"{spec['key']} {role}: SMD materials {unmapped} match no spec material "
                         f"(name the spec material .../<smd material> or set \"smd_material\")")
    out = out_dir / (Path(spec[role]).stem + ".mdl")
    info = smd2mdl.compile_model(out, smds, host_model, rename, spec.get("skins"),
                                 spec.get("surfaceprop", "metal"))
    log(f"  smd2mdl {out.name}: {info['bones']} bones, {info['verts']} verts, {info['tris']} tris, "
        f"{info['attachments']} host attachments, {info['skins']} skin families")
    return out


# ---------------------------------------------------------------- oracle
def model_material_guids(path: Path) -> list[int]:
    data, h = rdump.parse(str(path))
    ti = rdump.FIX(h["textureindex"])
    return [struct.unpack_from("<Q", data, ti + 8 * i)[0] for i in range(h["numtextures"])]


def rsx_export(guids: list[int], out: Path, paks: list[Path], types: str | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    gl = out / "guids.txt"
    gl.write_text("\n".join("%x" % g for g in guids) + "\n", encoding="ascii")
    cmd = [RSX, "-nogui", "-nocachedb", "-export", "--exportguids", gl, "--texturenames", "guid"]
    if types:
        cmd += ["--exporttypes", types]
    cmd += ["--exportdir", out]
    r = subprocess.run([str(c) for c in cmd + list(paks)], capture_output=True, text=True, cwd=out)
    if r.returncode == 0:
        return
    # some assets crash RSX when common_mp/common_early load alongside; one pak at a time works
    log(f"  rsx exit {r.returncode:#x} with {len(paks)} paks; retrying per pak")
    for pak in paks:
        r = subprocess.run([str(c) for c in cmd + [pak]], capture_output=True, text=True, cwd=out)
        if r.returncode != 0:
            log(f"  rsx exit {r.returncode:#x} on {Path(pak).name}")


def find_export(out: Path, stem: str, ext: str) -> Path:
    hits = [p for p in out.rglob("*" + ext) if p.stem.lower() == stem.lower()]
    if not hits:
        raise SystemExit(f"oracle export missing {stem}{ext} under {out}")
    return hits[0]


def load_hosts() -> dict:
    """Model paths, rigs and bones of every seed host, read from the game paks once."""
    if HOSTS_CACHE.is_file():
        cached = json.loads(HOSTS_CACHE.read_text(encoding="utf-8"))
        if set(cached) == set(SEED):
            return cached
    log("hosts: reading the stock heirloom models from the game paks (once)")
    out_dir = WORK / "hosts_scan"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    guids = [int(s[r], 16) for s in SEED.values() for r in ("view_guid", "world_guid")]
    rsx_export(guids, out_dir, GAME_PAKS, "mdl_")
    found = {}
    for f in out_dir.rglob("*.rmdl"):
        data, hd = rdump.parse(str(f))
        rel = rdump.cstr(data, rdump.FIX(hd["sznameindex"]))
        found[guid_hex(rel)] = (rel, f)
    hosts = {}
    for key, s in SEED.items():
        h = {"melee": s["melee"], "primary": "mp_weapon_" + s["melee"][len("melee_"):] + "_primary",
             "core_bone": s["core_bone"], "good_for": s["good_for"]}
        for role in ("view", "world"):
            g = "0x%016X" % int(s[role + "_guid"], 16)
            if g not in found:
                raise SystemExit(f"hosts: {key} {role} model {g} is not in the game paks (is game_dir an S21 install?)")
            rel, f = found[g]
            h[role], h[role + "_guid"] = rel, g
            h[role + "_rigs"] = [guid_hex(r) for r in rson_rigs(f.with_suffix(".rson"))]
            data, hd = rdump.parse(str(f))
            names = [b["name"] for b in rdump.bones(data, hd)]
            state = 0xB0 + rdump.FIX(hd["boneStateOffset"])
            h[role + "_mesh_bones"] = [names[i] for i in data[state:state + hd["boneStateCount"]]]
            h[role + "_bones"] = len(names)
        if h["core_bone"] not in h["view_mesh_bones"]:
            raise SystemExit(f"hosts: {key} core bone {h['core_bone']} is not a mesh bone of {h['view']}")
        if not h["view_rigs"]:
            raise SystemExit(f"hosts: {key} view model {h['view']} names no animation rig")
        hosts[key] = h
    HOSTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HOSTS_CACHE.write_text(json.dumps(hosts, indent=1), encoding="utf-8")
    shutil.rmtree(out_dir, ignore_errors=True)
    log(f"hosts: {len(hosts)} hosts -> {HOSTS_CACHE}")
    return hosts


def rson_rigs(rson: Path) -> list[str]:
    if not rson.is_file():
        return []
    text = rson.read_text(encoding="utf-8")
    block = text.split("rigs:", 1)[1].split("]", 1)[0] if "rigs:" in text else ""
    return [r.strip().replace("\\", "/") for r in block.replace("[", " ").split() if r.strip().endswith(".rrig")]


def ensure_oracle(host_key: str) -> dict:
    """Stock host models (client + dedi) and every host material + colpass."""
    h = HOSTS[host_key]
    out = ORACLE / host_key
    view_stem, world_stem = Path(h["view"]).stem, Path(h["world"]).stem
    marker = out / "oracle.json"
    if marker.is_file():
        o = json.loads(marker.read_text(encoding="utf-8"))
        return {k: (Path(v) if isinstance(v, str) else [Path(x) for x in v]) for k, v in o.items()}
    log(f"oracle: exporting host '{host_key}'")
    rsx_export([int(h["view_guid"], 16), int(h["world_guid"], 16)], out / "client", GAME_PAKS, "mdl_")
    view = find_export(out / "client", view_stem, ".rmdl")
    world = find_export(out / "client", world_stem, ".rmdl")
    mats = model_material_guids(view)
    rsx_export(mats, out / "mats", GAME_PAKS, "matl")
    mat_jsons = []
    colpass = []
    for g in mats:
        hit = None
        for j in (out / "mats").rglob("*.json"):
            if guid_hex(j.relative_to(out / "mats").as_posix()[:-5] + ".rpak") == "0x%016X" % g:
                hit = j
                break
        if hit is None:
            raise SystemExit(f"oracle: host material {g:#018x} not exported")
        mat_jsons.append(hit)
        cp = json.loads(hit.read_text(encoding="utf-8")).get("$colpassMaterial", "0x0")
        if int(cp, 16):
            colpass.append(int(cp, 16))
    if colpass:
        rsx_export(colpass, out / "colpass", GAME_PAKS, "matl")
    rsx_export([int(h["view_guid"], 16), int(h["world_guid"], 16)], out / "dedi", [GAME_DEDI_PAK], "mdl_")
    o = {
        "view": str(view), "world": str(world),
        "mats": [str(p) for p in mat_jsons],
        "dedi_view": str(find_export(out / "dedi", view_stem, ".rmdl")),
        "dedi_world": str(find_export(out / "dedi", world_stem, ".rmdl")),
    }
    marker.write_text(json.dumps(o, indent=1), encoding="utf-8")
    return ensure_oracle(host_key)


def host_colpass_json(mat_json: Path) -> Path:
    cp = json.loads(mat_json.read_text(encoding="utf-8")).get("$colpassMaterial", "0x0")
    root = mat_json.parents[0]
    while root.name not in ("mats",):
        root = root.parent
    for j in (root.parent / "colpass").rglob("*.json"):
        rel = j.relative_to(root.parent / "colpass").as_posix()[:-5]
        if guid_hex(rel + ".rpak") == cp.upper().replace("0X", "0x"):
            return j
    raise SystemExit(f"host colpass {cp} for {mat_json.name} not exported")


# ---------------------------------------------------------------- models
def convert(src: Path, outdir: Path) -> Path:
    if not src.is_file():
        raise SystemExit(f"missing source model: {src}")
    outdir.mkdir(parents=True, exist_ok=True)
    stage = outdir / "src"
    stage.mkdir(exist_ok=True)
    ver = "49" if src.suffix.lower() == ".mdl" else "8"
    exts = (".mdl", ".vvd", ".dx90.vtx", ".vtx", ".phy") if ver == "49" else (".rmdl", ".vg", ".phy")
    for ext in exts:
        s = src.parent / (src.stem + ext)
        if s.is_file():
            shutil.copy2(s, stage / s.name)
    run([RMDLCONV, "-convertmodel", stage / src.name, "-sourceversion", ver, "-targetversion", "17",
         "-outputdir", outdir, "-nopause"])
    dst = outdir / (src.stem + ".rmdl")
    if not dst.is_file() or not dst.with_suffix(".vg").is_file():
        raise SystemExit(f"rmdlconv produced no rmdl/vg for {src}")
    if ver == "49":
        # studiomdl output reads as a static prop; heirloom models animate off a rig
        raw = bytearray(dst.read_bytes())
        flags = struct.unpack_from("<i", raw, 0)[0]
        struct.pack_into("<i", raw, 0, (flags | STUDIOHDR_VIRTUAL_MODEL) & ~STUDIOHDR_STATIC_PROP)
        dst.write_bytes(raw)
        dst.with_suffix(".vg_static").unlink(missing_ok=True)
    return dst


def bone_table(path: Path) -> dict:
    data, h = rdump.parse(str(path))
    db = rdump.FIX(h["boneDataOffset"])
    out = {}
    for b in rdump.bones(data, h):
        o = db + b["i"] * 128
        out[b["name"]] = (b["parent"], b["flags"], struct.unpack_from("<7f", data, o + 64))
    return out


def rig_files(host_key: str, role: str) -> list[Path]:
    """The host's stock rigs for one role, exported once next to the oracle."""
    guids = HOSTS[host_key][role + "_rigs"]
    out = ORACLE / host_key / ("rigs_" + role)
    files = sorted(out.rglob("*.rrig")) if out.is_dir() else []
    if guids and len(files) < len(guids):
        rsx_export([int(g, 16) for g in guids], out, GAME_PAKS, "arig")
        files = sorted(out.rglob("*.rrig"))
    return files


def rig_ik_bones(rigs: list[Path]) -> set[str] | None:
    """Bone names the rigs' IK chains use, or None when a chain table does not decode
    cleanly (the caller then demands the full host skeleton)."""
    names: set[str] = set()
    for rig in rigs:
        data, h = rdump.parse(str(rig))
        bones = [b["name"] for b in rdump.bones(data, h)]
        base = rdump.FIX(h["ikchainindex"])
        for i in range(h["numikchains"]):
            c = base + i * 12  # sznameindex i32, numlinks u16, linkindex u16, float
            nlinks, linkindex = struct.unpack_from("<HH", data, c + 4)
            if not 0 < nlinks <= 8:
                return None
            for k in range(nlinks):
                b = struct.unpack_from("<i", data, c + linkindex + k * 16)[0]
                if not 0 <= b < len(bones):
                    return None
                names.add(bones[b])
    return names


def gate_model(path: Path, host: Path | None, required: set[str] | None = None) -> None:
    """required: bones that must exist (the rigs' IK chain bones); None = every host bone."""
    r = subprocess.run(["powershell", "-NoProfile", "-File", str(VERIFY), str(path), "-Quiet"],
                       capture_output=True, text=True)
    if r.returncode != 0 or "FAIL" in (r.stdout or ""):
        sys.stdout.write(r.stdout or "")
        raise SystemExit(f"verify_rmdl_v17 failed: {path}")
    data, h = rdump.parse(str(path))
    if not (h["flags"] & STUDIOHDR_VIRTUAL_MODEL) or (h["flags"] & STUDIOHDR_STATIC_PROP):
        raise SystemExit(f"{path.name}: studiohdr flags {h['flags']:#x} (need 0x80000 set, 0x10 clear)")
    for s in rdump.seqs(data, h):
        if s["activity"] != 0xFFFF or not (s["flags"] & 0x80000):
            raise SystemExit(f"{path.name}: placeholder seq contract broken {s}")
    if host is None:
        log(f"  gate ok {path.name}")
        return
    a, b = bone_table(path), bone_table(host)
    missing = [n for n in b if n not in a]
    fatal = [n for n in missing if required is None or n in required]
    if fatal:
        # the rig's IK chains resolve bones by name; a missing one AVs the client
        raise SystemExit(f"{path.name}: {len(fatal)} required host bones missing: {fatal[:10]}")
    if missing:
        log(f"  {path.name}: {len(missing)} host bones absent (rig motion on them is skipped): {missing[:6]}")
    shared = [n for n in b if n in a]
    if list(a)[:len(shared)] != shared:
        raise SystemExit(f"{path.name}: bone order differs from host after align")
    bad = [n for n in shared if a[n][0] != b[n][0] or (a[n][1] & ~BONE_VERTEX_MASK) != (b[n][1] & ~BONE_VERTEX_MASK)
           or max(abs(x - y) for x, y in zip(a[n][2][:3], b[n][2][:3])) > 0.05]
    if bad:
        raise SystemExit(f"{path.name}: bones differ from host: {bad[:8]}")
    log(f"  gate ok {path.name} ({len(shared)}/{len(b)} host bones)")


ATTACH_STRIDE = 56  # u16 name, u16 bone, i32 flags, 3x4 float local


def attachments_from_host(model: Path, host: Path, offsets: dict, overrides: dict | None = None) -> None:
    """Studiomdl QC eulers do not round-trip to the stock attachment frames: take every
    attachment matrix (and bone) from the host by name, then apply bone-local translation
    offsets and full 3x4 overrides (e.g. an ADS frame on our own iron sights). Run after
    align, so bone indices are the host's."""
    raw = bytearray(model.read_bytes())
    d, h = rdump.parse(str(model))
    hd, hh = rdump.parse(str(host))
    if [b["name"] for b in rdump.bones(d, h)][:hh["boneCount"]] != [b["name"] for b in rdump.bones(hd, hh)]:
        raise SystemExit(f"{model.name}: bone order is not the host's; align before taking host attachments")

    def table(data, hdr):
        base = rdump.FIX(hdr["localattachmentindex"])
        return {rdump.cstr(data, base + i * ATTACH_STRIDE + rdump.FIX(struct.unpack_from("<H", data, base + i * ATTACH_STRIDE)[0])):
                base + i * ATTACH_STRIDE for i in range(hdr["numlocalattachments"])}
    ours, theirs = table(d, h), table(hd, hh)
    missing = [n for n in theirs if n not in ours]
    if missing:
        raise SystemExit(f"{model.name}: host attachments missing from the model: {missing[:8]}")
    for name, o in theirs.items():
        raw[ours[name] + 2:ours[name] + ATTACH_STRIDE] = hd[o + 2:o + ATTACH_STRIDE]
    for name, off in offsets.items():
        o = ours[name]
        for k, idx in enumerate((3, 7, 11)):
            v = struct.unpack_from("<f", raw, o + 8 + idx * 4)[0]
            struct.pack_into("<f", raw, o + 8 + idx * 4, v + float(off[k]))
    for name, m in (overrides or {}).items():
        if name not in ours or len(m) != 12:
            raise SystemExit(f"{model.name}: attachment override '{name}' needs an existing attachment and 12 floats")
        struct.pack_into("<12f", raw, ours[name] + 8, *(float(x) for x in m))
    model.write_bytes(raw)
    log(f"  {model.name}: {len(theirs)} attachments from host" + (f", offsets {list(offsets)}" if offsets else "")
        + (f", overrides {list(overrides)}" if overrides else ""))


def stage_model(conv: Path, pack_rel: str, rigs: list[str], root: Path | None = None,
                exts=(".rmdl", ".vg", ".vg_static", ".phy")) -> None:
    root = root or cs()
    dst = staged(root / pack_rel, root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    for ext in exts:
        s = conv.with_suffix(ext)
        if s.is_file():
            shutil.copy2(s, dst.with_suffix(ext))
    body = "rigs:\n[\n" + "".join(f"\t{r[2:].lower()}\n" for r in rigs) + "]\nseqs:\n[\n]\n"
    dst.with_suffix(".rson").write_text(body, encoding="utf-8")


V54_FLAGS = 0x9C
V54_NUMSKINFAMILIES = 0xE4


def stage_dedi_compiled(src: Path, pack_rel: str, workdir: Path, host_rmdl: Path) -> None:
    """Our studiomdl model as an S3 v54 rmdl for the dedi (rmdlconv single-argument mode).
    The dedi checks SetSkin against its own model ("Invalid skin index %i (total %i)"), so a
    model with skin families must run there too -- the host stand-in has one skin."""
    if src.suffix.lower() != ".mdl":
        raise SystemExit(f"dedi 'compiled' needs a studiomdl .mdl source: {src}")
    workdir.mkdir(parents=True, exist_ok=True)
    for ext in (".mdl", ".vvd", ".dx90.vtx", ".vtx", ".phy"):
        f = src.parent / (src.stem + ext)
        if f.is_file():
            shutil.copy2(f, workdir / f.name)
    r = subprocess.run([str(RMDLCONV), str(workdir / src.name)], input="\n\n", capture_output=True, text=True)
    out = None
    for cand in (workdir / (src.stem + ".rmdl"), workdir / src.name):
        if cand.is_file() and struct.unpack_from("<i", cand.read_bytes(), 4)[0] == 54:
            out = cand
            break
    if out is None:
        sys.stdout.write((r.stdout or "")[-1500:])
        raise SystemExit(f"rmdlconv produced no v54 model for the dedi from {src}")
    raw = bytearray(out.read_bytes())
    # the dedi takes the host's own v54 flags: S3 does not use the client's 0x80000 bit
    struct.pack_into("<i", raw, V54_FLAGS, struct.unpack_from("<i", host_rmdl.read_bytes(), V54_FLAGS)[0])
    name = pack_rel.encode("ascii")
    if len(name) >= 64:
        raise SystemExit(f"dedi model path too long for the v54 name field: {pack_rel}")
    raw[0x10:0x50] = name + b"\0" * (64 - len(name))
    dst = staged(ds() / pack_rel, ds())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(raw)
    phy = out.with_suffix(".phy")
    if phy.is_file():
        shutil.copy2(phy, dst.with_suffix(".phy"))
    log(f"  dedi {pack_rel}: compiled v54, {struct.unpack_from('<i', raw, V54_NUMSKINFAMILIES)[0]} skin families")


def v54_skin_families(path: Path) -> int:
    return struct.unpack_from("<i", path.read_bytes(), V54_NUMSKINFAMILIES)[0]


def stage_dedi_from_host(host_rmdl: Path, pack_rel: str) -> None:
    """The dedi never renders: the host's own S3 model (bones, attachments, hitboxes) under our path."""
    dst = staged(ds() / pack_rel, ds())
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = bytearray(host_rmdl.read_bytes())
    name = pack_rel.encode("ascii")
    if len(name) >= 64:
        raise SystemExit(f"dedi model path too long for the v54 name field: {pack_rel}")
    raw[0x10:0x50] = name + b"\0" * (64 - len(name))  # studiohdr_t::name[64]
    dst.write_bytes(raw)
    phy = host_rmdl.with_suffix(".phy")
    if phy.is_file():
        shutil.copy2(phy, dst.with_suffix(".phy"))


# ---------------------------------------------------------------- textures / materials
def dds_info(p: Path) -> tuple[int, int, int, bytes]:
    d = p.read_bytes()[:160]
    if d[:4] != b"DDS ":
        raise SystemExit(f"not a DDS: {p}")
    hgt, wid = struct.unpack_from("<II", d, 12)
    mips = struct.unpack_from("<I", d, 28)[0]
    fcc = d[84:88]
    if fcc == b"DX10":
        fcc = b"DX10:%d" % struct.unpack_from("<I", d, 128)[0]
    return wid, hgt, mips, fcc


def stage_texture(src: Path, dest: Path, slot_type: str) -> None:
    fmt, accept = SLOT_FMT.get(slot_type, SLOT_FALLBACK_FMT)
    staged(dest).parent.mkdir(parents=True, exist_ok=True)
    how = None
    if src.suffix.lower() == ".dds":
        w, h, mips, fcc = dds_info(src)
        if fcc in accept and mips >= max(w, h).bit_length():
            shutil.copy2(src, dest)
            how = "copy"
    if how is None:
        with tempfile.TemporaryDirectory() as td:
            run([TEXCONV, "-f", fmt, "-m", "0", "-y", "-ft", "dds", "-o", td, src])
            shutil.copy2(Path(td) / (src.stem + ".dds"), dest)
        how = f"texconv {fmt}"
    w2, h2, m2, f2 = dds_info(dest)
    if f2 not in accept or m2 < max(w2, h2).bit_length():
        raise SystemExit(f"texture gate: {dest.name} {f2} mips={m2} want {fmt} full mips")
    if w2 & (w2 - 1) or h2 & (h2 - 1):
        raise SystemExit(f"texture gate: {src.name} is {w2}x{h2}; S21 textures must be power-of-two")
    dest.with_suffix(".json").write_text(json.dumps({"resourceFlags": "0x0", "usageFlags": "0x0"}, indent=1),
                                         encoding="utf-8")
    log(f"  tex {dest.relative_to(cs()).as_posix()} {w2}x{h2} {f2.decode()} mips={m2} ({how})")


def solid_texture(rgba: str) -> Path:
    """A 64x64 solid PNG for template slots the source has no map for ("solid:r,g,b,a")."""
    vals = tuple(int(v) for v in rgba.split(","))
    if len(vals) != 4 or not all(0 <= v <= 255 for v in vals):
        raise SystemExit(f"bad solid texture '{rgba}', want solid:r,g,b,a")
    out = Path(tempfile.gettempdir()) / ("solid_%02x%02x%02x%02x.png" % vals)
    if not out.is_file():
        w = h = 64
        raw = b"".join(b"\0" + bytes(vals) * w for _ in range(h))
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        out.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                        + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return out


def material_textures(spec: dict, m: dict, types: dict) -> dict[str, Path]:
    """Template slot index -> source texture file. "slots" maps S3 <src_tex>_<n>.dds onto the
    template's slots in order; "textures" (slot index or textureType -> file or solid:r,g,b,a)
    adds or overrides."""
    out = {}
    if "slots" in m:
        for idx, src_slot in zip(sorted(types, key=int), m["slots"]):
            if src_slot is None:
                continue
            out[idx] = Path(spec["source_root"]) / "texture" / f"{m['src_tex']}_{src_slot}.dds"
    by_type = {v: k for k, v in types.items()}
    for key, path in m.get("textures", {}).items():
        idx = key if key.isdigit() else by_type.get(key)
        if idx is None or idx not in types:
            raise SystemExit(f"{m['name']}: template material has no slot '{key}' (slots: {types})")
        if path.startswith("solid:"):
            out[idx] = solid_texture(path[len("solid:"):])
            continue
        p = Path(path)
        out[idx] = p if p.is_absolute() else Path(spec["source_root"]) / path
    for idx, p in out.items():
        if not p.is_file():
            raise SystemExit(f"{m['name']}: missing texture for slot {idx} ({types.get(idx)}): {p}")
    missing = [types[i] for i in types if i not in out]
    if missing:
        raise SystemExit(f"{m['name']}: no texture for template slots {missing} -- every slot needs one")
    return out


def ensure_template(guid: str) -> tuple[Path, Path]:
    """Any stock S21 matl (+ its colpass) as a material template."""
    out = ORACLE / "templates" / guid[2:].upper()
    jsons = list((out / "mats").rglob("*.json")) if (out / "mats").is_dir() else []
    if not jsons:
        rsx_export([int(guid, 16)], out / "mats", GAME_PAKS, "matl")
        jsons = list((out / "mats").rglob("*.json"))
        if not jsons:
            raise SystemExit(f"template material {guid} not found in the stock paks")
        cp = json.loads(jsons[0].read_text(encoding="utf-8")).get("$colpassMaterial", "0x0")
        if int(cp, 16):
            rsx_export([int(cp, 16)], out / "colpass", GAME_PAKS, "matl")
    has_cp = int(json.loads(jsons[0].read_text(encoding="utf-8")).get("$colpassMaterial", "0x0"), 16)
    return jsons[0], host_colpass_json(jsons[0]) if has_cp else None


SHARED_TEX: dict[tuple[str, str], str] = {}


def stage_material(spec: dict, m: dict, oracle: dict) -> tuple[list[str], list[tuple[str, str]]]:
    if "template" in m:
        host_json, host_cp = ensure_template(m["template"])
    else:
        host_json = oracle["mats"][m.get("host_material", 0)]
        host_cp = host_colpass_json(host_json)
    tmpl = json.loads(host_json.read_text(encoding="utf-8"))
    tmpl_cp = json.loads(host_cp.read_text(encoding="utf-8")) if host_cp else None
    types = tmpl["$textureTypes"]
    tex_paths, tex_map = [], {}
    pn = m.get("pack_name", m["name"])
    for idx, src in sorted(material_textures(spec, m, types).items(), key=lambda kv: int(kv[0])):
        # one asset per (source file, slot type): skin variants share their big maps
        key = (os.path.normcase(os.path.realpath(src)), types[idx])
        rel = SHARED_TEX.get(key)
        if rel is None:
            rel = f"texture/{pn}_{idx}"
            stage_texture(src, cs() / (rel + ".dds"), types[idx])
            SHARED_TEX[key] = rel
        tex_map[idx] = rel + ".rpak"
        tex_paths.append(rel + ".rpak")

    main_rel = f"material/{pn}_sknp"
    cp_rel = f"material/{pn}_colpass_sknp"
    main_guid = guid_hex(main_rel + ".rpak")
    cp_guid = guid_hex(cp_rel + ".rpak") if tmpl_cp else "0x0"

    # Our own S3 values where they exist; the host supplies what S21 changed in the
    # format (shader set, glue/depth flags, the 96-byte uber tail S3 lacks).
    host_uber = host_json.with_suffix(".uber").read_bytes()
    if len(host_uber) != 608:
        raise SystemExit(f"host uber {host_json.with_suffix('.uber')} is {len(host_uber)} B, want 608")
    uber, surface = host_uber, tmpl.get("surfaceProp")
    src_mat = Path(spec["source_root"]) / "material" / (m["name"] + "_sknp.json")
    if src_mat.is_file() and src_mat.with_suffix(".uber").is_file():
        src_uber = src_mat.with_suffix(".uber").read_bytes()
        if len(src_uber) != 512:
            raise SystemExit(f"source uber {src_mat.with_suffix('.uber')} is {len(src_uber)} B, want 512")
        uber = src_uber + host_uber[512:]
        surface = json.loads(src_mat.read_text(encoding="utf-8")).get("surfaceProp", surface)
    surface = m.get("surfaceProp", surface)

    # Explicit look overrides (named uber fields, float indices of the shared V15 layout).
    UBER_FIELDS = {"albedoTint": 89, "emissiveTint": 93, "perfSpecColor": 97, "perfGloss": 100}
    if m.get("uber"):
        raw = bytearray(uber)
        for key, val in m["uber"].items():
            vals = val if isinstance(val, list) else [val]
            for k, v in enumerate(vals):
                struct.pack_into("<f", raw, (UBER_FIELDS[key] + k) * 4, float(v))
        uber = bytes(raw)
    # shader-specific constants by float index (e.g. the screen-space galaxy drift at 4/5)
    if m.get("uber_floats"):
        raw = bytearray(uber)
        for idx, v in m["uber_floats"].items():
            struct.pack_into("<f", raw, int(idx) * 4, float(v))
        uber = bytes(raw)

    docs = [(tmpl, main_rel, cp_guid)] + ([(tmpl_cp, cp_rel, "0x0")] if tmpl_cp else [])
    for doc, rel, colpass in docs:
        doc = dict(doc)
        doc["name"] = pn + ("_colpass" if rel == cp_rel else "")
        doc["surfaceProp"] = surface
        doc["$textures"] = dict(tex_map)
        doc["$colpassMaterial"] = colpass
        out = staged(cs() / (rel + ".json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        out.with_suffix(".uber").write_bytes(uber)
    log(f"  matl {main_rel} {main_guid} (host {host_json.stem}, {len(tex_map)} slots)")
    mats = [(cp_rel + ".rpak", cp_guid)] if tmpl_cp else []
    return tex_paths, mats + [(main_rel + ".rpak", main_guid)]


def repoint_model_materials(model: Path, remap: dict[str, str]) -> None:
    """Rewrite the material GUIDs a model embeds (studiohdr textureindex) -- how a
    model converted with its original material names ships under a mod namespace."""
    if not remap:
        return
    raw = bytearray(model.read_bytes())
    data, h = rdump.parse(str(model))
    ti = rdump.FIX(h["textureindex"])
    for i in range(h["numtextures"]):
        old = "0x%016X" % struct.unpack_from("<Q", raw, ti + 8 * i)[0]
        if old in remap:
            struct.pack_into("<Q", raw, ti + 8 * i, int(remap[old], 16))
    model.write_bytes(raw)


def check_model_materials(model: Path, mat_guids: set[str], what: str) -> None:
    for g in model_material_guids(model):
        if "0x%016X" % g not in mat_guids:
            raise SystemExit(f"{what}: model references material {g:#018x}, which no spec material produces "
                             f"(spec 'name' must equal the QC $renamematerial target)")


# ---------------------------------------------------------------- scripts
def host_weapon_kv(name: str) -> str:
    f = GAME_WEAPONS / f"{name}.txt"
    if not f.is_file():
        raise SystemExit(f"host weapon file {f} is missing from the game install")
    return f.read_text(encoding="utf-8", errors="replace")


def set_kv(text: str, key: str, value: str | None) -> str:
    top, sep, rest = text.partition('"Mods"')
    pat = re.compile(r'^(\s*)"%s"\s+"[^"]*"[ \t]*\r?\n' % re.escape(key), re.M)
    if value is None:
        top = pat.sub("", top, count=1)
    elif pat.search(top):
        top = pat.sub(lambda mt: f'{mt.group(1)}"{key}" "{value}"\n', top, count=1)
    else:
        top = top.replace("{", '{\n\t"%s" "%s"' % (key, value), 1)
    return top + sep + rest


def is_gun(spec: dict) -> bool:
    return HOSTS[spec["host"]].get("kind") == "gun"


def class_names(spec: dict) -> dict[str, str]:
    if spec.get("mod"):
        ns = mod_namespace(spec)
        if is_gun(spec):
            return {"primary": f"{ns}__{spec['key']}"}
        return {"primary": f"{ns}__{spec['key']}", "melee": f"{ns}__{spec['key']}_melee"}
    if is_gun(spec):
        raise SystemExit(f"{spec['key']}: gun hosts ship as mods only (add a \"mod\" block)")
    return {"primary": f"mp_weapon_{spec['key']}_primary", "melee": f"melee_{spec['key']}"}


def write_scripts(spec: dict, weapons_dir: Path) -> list[str]:
    h = HOSTS[spec["host"]]
    w = spec.get("weapon", {})
    name = w.get("printname", spec["key"])
    if spec.get("mod"):
        name = "#" + loc_token(spec)
    host_fx = w.get("host_fx", is_gun(spec))
    names = class_names(spec)
    out = []
    for kind, host_wpn in (("primary", h["primary"]), ("melee", h.get("melee"))):
        if kind not in names:
            continue
        txt = host_weapon_kv(host_wpn)
        for old, new in w.get("kv_replace", {}).get(kind, []):
            if old not in txt:
                raise SystemExit(f"{spec['key']}: kv_replace '{old}' not found in {host_wpn}.txt")
            txt = txt.replace(old, new)
        txt = set_kv(txt, "viewmodel", spec["view"])
        txt = set_kv(txt, "playermodel", spec["world"])
        if kind == "melee":
            txt = set_kv(txt, "holstermodel", w.get("holstermodel", "mdl/dev/empty_model.rmdl"))
        # the melee's name is what the kill feed shows (it deals the damage)
        name_keys = ("printname", "shortprintname", "description", "longdesc") if kind == "primary" else ("printname", "shortprintname")
        for k in name_keys:
            txt = set_kv(txt, k, name)
        if not host_fx:
            # host callbacks play the host's FX at the host's attachment names
            generic = {"melee": ("OnWeaponActivate_weapon_melee_primary", "OnWeaponDeactivate_weapon_melee_primary")}
            act, deact = generic.get(kind, (None, None))
            txt = set_kv(txt, "OnWeaponActivate", act)
            txt = set_kv(txt, "OnWeaponDeactivate", deact)
        for k, v in w.get("kv", {}).get(kind, {}).items():
            txt = set_kv(txt, k, v)
        for k in w.get("kv_remove", {}).get(kind, []):
            txt = set_kv(txt, k, None)
        cls = names[kind]
        weapons_dir.mkdir(parents=True, exist_ok=True)
        (weapons_dir / f"{cls}.txt").write_text(txt, encoding="utf-8", newline="")
        out.append(cls)
        log(f"  script {cls}.txt (from {host_wpn})")
    if not is_gun(spec):
        parity_gate([(weapons_dir / f"{names[k]}.txt", hw) for k, hw in (("primary", h["primary"]), ("melee", h.get("melee"))) if k in names])
    return out


def parity_gate(pairs: list) -> None:
    """The server refuses a heirloom whose gameplay values differ from its stock host; fail here first."""
    args = [str(a) for p, hw in pairs for a in (p, hw)]
    r = subprocess.run([sys.executable, str(ROOT / "lib" / "heirloom_parity_check.py"), str(GAME_WEAPONS), str(PARITY_KEYS), *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stdout.write(r.stdout[-3000:] + r.stderr[-1500:])
        raise SystemExit("parity gate: gameplay values differ from the stock host (see MISMATCH lines)")
    log("  parity gate ok")


# ---------------------------------------------------------------- pack / gate
def repak(man: Path, pak: Path) -> float:
    t0 = time.time()
    r = run([REPAK, man])
    for line in (r.stdout or "").strip().splitlines()[-2:]:
        log("  repak:", line)
    if not pak.is_file() or pak.stat().st_mtime < t0 - 1:
        raise SystemExit(f"Repak did not write {pak}")
    return t0


def gate_pak(pak: Path, expect: dict[str, list[str]], extra_paks: list[Path]) -> None:
    """Every new asset is in the pak and each ref resolves to an asset some loaded pak provides."""
    work = pak.parent
    dep, lst = work / "heirlooms_deps.txt", work / "heirlooms_list.csv"
    run([RSX, "-nogui", "-nocachedb", "--list", lst, "--listformat", "csv", "--depfilepath", dep,
         pak, *extra_paks], cwd=work)
    have, names = set(), {}
    with open(lst, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            g = int(row["guid"], 16)
            names.setdefault(g, row["asset_name"].lower())
            if row["file_name"].lower() == pak.name.lower():
                have.add(g)
                names[g] = row["asset_name"].lower()
    lines = {}
    for line in dep.read_text(encoding="utf-8", errors="replace").lower().splitlines():
        parts = line.split(",")
        lines[parts[0]] = parts[1:]
    for guid, refs in expect.items():
        g = int(guid, 16)
        if g not in have:
            raise SystemExit(f"pak gate: {guid} not in {pak}")
        deps = lines.get(names[g], [])
        for r in refs:
            want = names.get(int(r, 16))
            if want is None:
                raise SystemExit(f"pak gate: {names[g]} needs {r}, which no loaded pak provides")
            if want not in deps:
                raise SystemExit(f"pak gate: {names[g]} does not reference {want}")
    log(f"  pak gate ok {pak.name} ({len(expect)} assets)")


def replace_file(src: Path, dst: Path) -> None:
    """Unlink before copying, so a hardlinked file elsewhere is never edited through this path."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".heirlooms_tmp")
    shutil.copy2(src, tmp)
    dst.unlink(missing_ok=True)
    tmp.rename(dst)


def kill_game() -> None:
    for exe in ("r5apex.exe", "r5apex_ds.exe", "r5apex_dx12.exe"):
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)


MOD_ID = re.compile(r"^[A-Za-z0-9_]{1,24}\.[A-Za-z0-9_]{1,24}$")


def mod_namespace(spec: dict) -> str:
    return spec["mod"]["id"].replace(".", "_").lower()


def loc_token(spec: dict) -> str:
    return f"{mod_namespace(spec)}__{spec['key']}".upper()


def check_mod_spec(spec: dict) -> None:
    mod = spec["mod"]
    if not MOD_ID.match(mod.get("id", "")) or not 4 <= len(mod["id"]) <= 32:
        raise SystemExit(f"{spec['key']}: mod id must be <Team>.<Name>, 4-32 letters/digits/_")
    ns = mod_namespace(spec)
    if "__" in ns:
        raise SystemExit(f"{spec['key']}: mod id reads as '__' in file names; pick another id")
    for role in ("view", "world"):
        if not spec[role].lower().startswith(f"mdl/{ns}__"):
            raise SystemExit(f"{spec['key']}: {role} must live under mdl/{ns}__<name>/ (mod namespace)")
    for m in spec["materials"]:
        if not m.get("pack_name", "").lower().startswith(f"models/{ns}__"):
            raise SystemExit(f"{spec['key']}: material '{m['name']}' needs pack_name under models/{ns}__<name>/")


def mod_dir(spec: dict) -> Path:
    return Path(spec["mod"]["dir"])


def write_text_if_missing(path: Path, text: str) -> None:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        log(f"  wrote {path.name}")


def mod_scaffold(spec: dict, root: Path) -> None:
    """Fixed mod files; existing files are the author's and are left alone."""
    mod, ns = spec["mod"], mod_namespace(spec)
    team, name = mod["id"].split(".", 1)
    w = spec.get("weapon", {})
    printname = w.get("printname", spec["key"])
    desc = mod.get("description", f"{printname}: a custom heirloom on the {spec['host']} rig.")[:250]
    ver = mod.get("version", "1.0.0")
    write_text_if_missing(root / "mod.vdf", (
        '"mod"\n{\n'
        f'\t"author"\t\t"{mod.get("author", team)}"\n'
        f'\t"name"\t\t\t"{mod.get("name", printname)}"\n'
        f'\t"id"\t\t\t"{mod["id"]}"\n'
        f'\t"description"\t"{desc}"\n'
        f'\t"version"\t\t"{ver}"\n'
        '\t"realm"\t\t\t"both"\n\n'
        '\t"LocalizationFiles"\n\t{\n'
        f'\t\t"resource/{ns}__english.txt"\t"1"\n'
        '\t}\n}\n'))
    write_text_if_missing(root / "manifest.json", json.dumps({
        "name": name, "version_number": ver, "website_url": mod.get("website_url", "https://r5flowstate.org"),
        "description": desc, "dependencies": []}, indent=2) + "\n")
    tok = loc_token(spec)
    write_text_if_missing(root / "resource" / f"{ns}__english.txt", (
        '"lang"\n{\n\t"Language" "english"\n\t"Tokens"\n\t{\n'
        f'\t\t"{tok}"\t"{printname}"\n'
        '\t}\n}\n'))
    cls = class_names(spec)
    if is_gun(spec):
        about = (f"A custom weapon on the stock `{spec['host']}` animations. Server and players both "
                 f"install it.\n\nGive it with `give {cls['primary']}` where give is allowed.\n")
    else:
        about = (f"A custom heirloom on the stock `{spec['host']}` animations. Server and players both "
                 f"install it.\n\nGive it with `give {cls['primary']}` where give is allowed. The swing "
                 f"weapon is `{cls['melee']}` (offhand melee slot).\n")
    write_text_if_missing(root / "README.md", f"# {printname}\n\n{desc}\n\n{about}")
    write_text_if_missing(root / "scripts" / "vscripts" / "scripts.rson", "".join(
        f'When: "{vm}"\nScripts:\n[\n\t{ns}/sh_{ns}.gnut\n]\n\n' for vm in ("SERVER", "CLIENT", "UI")))
    idf = mod["id"].replace(".", "_")
    pre = "".join(f"\tPrecacheWeapon( \"{c}\" )\n" for c in cls.values())
    # lists the pair in Lab > Mods > Heirloom (the game's sh_mod_heirlooms.gnut)
    host_family = (HOSTS[spec["host"]].get("melee") or "")[len("melee_"):]
    reg = f"\tModWeapon_Register( \"{cls['primary']}\", \"{printname}\", \"{HOSTS[spec['host']]['primary']}\" )\n" if is_gun(spec) else (f"\tModHeirloom_Register( \"{cls['primary']}\", \"{cls['melee']}\", \"{printname}\" )\n"
                                   f"\tModHeirloom_SetHost( \"{cls['primary']}\", \"{host_family}\" )\n")
    write_text_if_missing(root / "scripts" / "vscripts" / ns / f"sh_{ns}.gnut", (
        f"#if SERVER\nglobal function CodeCallback_{idf}_ModInit\n#endif\n"
        f"#if CLIENT\nglobal function ClientCodeCallback_{idf}_ModInit\n#endif\n"
        f"#if UI\nglobal function UICodeCallback_{idf}_ModInit\n#endif\n\n"
        "// the UI VM compiles this file too and has no PrecacheWeapon: keep each init behind its VM\n"
        f"#if SERVER\nvoid function CodeCallback_{idf}_ModInit()\n{{\n{pre}{reg}}}\n#endif\n\n"
        f"#if CLIENT\nvoid function ClientCodeCallback_{idf}_ModInit()\n{{\n{pre}{reg}}}\n#endif\n\n"
        f"#if UI\nvoid function UICodeCallback_{idf}_ModInit()\n{{\n{reg}}}\n#endif\n"))


def build_mod(spec: dict, res: dict, install: bool) -> None:
    ns, root = mod_namespace(spec), mod_dir(spec)
    pak = f"{ns}__{spec['key']}"
    work = WORK / spec["key"] / "mod_paks"
    out_c, out_d = work / "client", work / "dedi"
    for d in (out_c, out_d):
        if d.exists():
            shutil.rmtree(d)  # Repak reuses stream offsets from an old rpak in outputDir
        d.mkdir(parents=True)
    cman = work / f"{pak}_client.json"
    cman.write_text(json.dumps({
        "version": 8, "name": pak, "keepDevOnly": True, "headerFlags": 32, "compressLevel": 0,
        "assetsDir": cs().as_posix() + "/", "outputDir": out_c.as_posix() + "/",
        "streamFileMandatory": f"paks\\Win64\\{pak}.starpak",
        "streamFileOptional": f"paks\\Win64\\{pak}.opt.starpak",
        "files": res["client_add"]}, indent=1), encoding="utf-8")
    repak(cman, out_c / f"{pak}.rpak")
    gate_pak(out_c / f"{pak}.rpak", res["client_expect"], GAME_PAKS)
    dman = work / f"{pak}_dedi.json"
    dman.write_text(json.dumps({
        "version": 8, "name": pak, "dedi": True, "keepClientOnly": False, "keepDevOnly": False,
        "headerFlags": 32, "compressLevel": 6,
        "assetsDir": ds().as_posix() + "/", "outputDir": out_d.as_posix() + "/",
        "files": res["dedi_add"]}, indent=1), encoding="utf-8")
    repak(dman, out_d / f"{pak}.rpak")
    gate_pak(out_d / f"{pak}.rpak", res["dedi_expect"], [GAME_DEDI_PAK])

    mod_scaffold(spec, root)
    for sub in ("paks/Win64", "paks/Win64_server"):
        d = root / sub
        if d.exists():
            for f in d.glob(f"{pak}.*"):
                f.unlink()
        d.mkdir(parents=True, exist_ok=True)
    for f in out_c.glob(f"{pak}*"):
        if f.suffix in (".rpak", ".starpak"):
            shutil.copy2(f, root / "paks/Win64" / f.name)
    shutil.copy2(out_d / f"{pak}.rpak", root / "paks/Win64_server" / f"{pak}.rpak")
    for sub in ("paks/Win64", "paks/Win64_server"):
        paks = sorted(p.name for p in (root / sub).glob(f"{ns}__*.rpak"))
        (root / sub / "preload.rson").write_text("Paks:\n[\n" + "".join(f"    {p}\n" for p in paks) + "]\n",
                                                 encoding="utf-8", newline="\n")
    write_scripts(spec, root / "scripts" / "weapons")

    render_mod_icon(spec, root)
    team = spec["mod"]["id"].split(".", 1)[0]
    r = subprocess.run([sys.executable, str(ROOT / "lib" / "package_mod.py"), str(root), "--team", team,
                        "--out", str(WORK / "dist")], capture_output=True, text=True)
    sys.stdout.write(r.stdout[-800:])
    if r.returncode != 0:
        sys.stdout.write(r.stderr[-1500:])
        raise SystemExit(f"mod package check failed for {root}")
    if install:
        install_mod(spec, root)


def render_mod_icon(spec: dict, root: Path) -> None:
    """A missing icon.png is rendered from the world SMD with each material's albedo."""
    icon = root / "icon.png"
    smds = source_smds(spec, "world")
    if icon.is_file() or not smds:
        return
    cmd = [sys.executable, str(ROOT / "lib" / "render_icon.py"),
           str(icon), str(smds[0])]
    for m in spec["materials"]:
        alb = m.get("textures", {}).get("albedoTexture", "")
        if alb and not alb.startswith("solid:"):
            p = Path(alb) if Path(alb).is_absolute() else Path(spec["source_root"]) / alb
            cmd += ["--mat", f"{m.get('smd_material', m['name'].rsplit('/', 1)[-1])}={p}"]
    run(cmd)
    log(f"  rendered {icon.name} from {smds[0].name}")


def install_mod(spec: dict, root: Path) -> None:
    team, name = spec["mod"]["id"].split(".", 1)
    dest = GAME / "mods" / f"{team}-{name}"
    if dest.exists():
        for f in sorted(dest.rglob("*"), reverse=True):
            rel = f.relative_to(dest)
            if f.is_file() and not (root / rel).is_file():
                f.unlink()
            elif f.is_dir() and not any(f.iterdir()):
                f.rmdir()
    for f in root.rglob("*"):
        if f.is_file():
            replace_file(f, dest / f.relative_to(root))
    vdf = GAME / "mods" / "mods.vdf"
    t = vdf.read_text(encoding="utf-8")
    mid = spec["mod"]["id"]
    if re.search(r'"%s"\s+"\d"' % re.escape(mid), t):
        t = re.sub(r'("%s"\s+)"\d"' % re.escape(mid), r'\1"1"', t)
    else:
        t = t.rstrip().rstrip("}") + f'\t"{mid}"\t\t"1"\n}}\n'
    vdf.write_text(t, encoding="utf-8")
    log(f"installed {mid} -> {dest} (enabled)")


# ---------------------------------------------------------------- main
def build_one(spec: dict) -> dict:
    key, host = spec["key"], HOSTS[spec["host"]]
    log(f"=== {key} (host {spec['host']}, mod {spec['mod']['id']}) ===")
    check_mod_spec(spec)
    work = WORK / key
    if work.exists():
        shutil.rmtree(work)
    set_stage(work / "assets", work / "dedi_assets")
    view_rigs = host["view_rigs"]
    oracle = ensure_oracle(spec["host"])

    att_offsets = spec.get("attachment_offsets", {})
    src = {}
    for role in ("view", "world"):
        src[role] = (compile_smd_source(spec, role, oracle[role], work / ("smd_" + role))
                     if source_smds(spec, role) else source_path(spec, role))
    view = convert(src["view"], work / "view")
    align(oracle["view"], view)
    if spec.get("attachments") == "host":
        attachments_from_host(view, oracle["view"], att_offsets.get("view", {}), spec.get("attachment_overrides", {}).get("view"))
    gate_model(view, oracle["view"], rig_ik_bones(rig_files(spec["host"], "view")))
    world = convert(src["world"], work / "world")
    if host["world_rigs"]:
        align(oracle["world"], world)
        if spec.get("attachments") == "host":
            attachments_from_host(world, oracle["world"], att_offsets.get("world", {}), spec.get("attachment_overrides", {}).get("world"))
        gate_model(world, oracle["world"], rig_ik_bones(rig_files(spec["host"], "world")))
    else:
        gate_model(world, None)

    stage_model(view, spec["view"], view_rigs)
    stage_model(world, spec["world"], host["world_rigs"])
    staged_view, staged_world = cs() / spec["view"], cs() / spec["world"]

    res = {"client_add": [], "dedi_add": [], "client_expect": {}, "dedi_expect": {}}
    mat_guids, remap = [], {}
    adir = cs().as_posix() + "/"
    SHARED_TEX.clear()
    added_tex = set()
    for m in spec["materials"]:
        texs, mats = stage_material(spec, m, oracle)
        for t in texs:
            if t in added_tex:
                continue
            added_tex.add(t)
            res["client_add"].append({"_type": "txtr", "_path": t, "$strmMips": 0, "$optMips": 0, "$assetsDir": adir})
        for path, guid in mats:
            res["client_add"].append({"_type": "matl", "_path": path, "$guid": guid, "$assetsDir": adir})
        mat_guids.append(mats[-1][1])
        refs = [guid_hex(t) for t in texs] + ([mats[0][1]] if len(mats) > 1 else [])
        res["client_expect"][mats[-1][1]] = refs
        engine = guid_hex(f"material/{m['name']}_sknp.rpak")
        if engine != mats[-1][1]:
            remap[engine] = mats[-1][1]
    for model in (staged_view, staged_world):
        repoint_model_materials(model, remap)
    check_model_materials(staged_view, set(mat_guids), spec["view"])
    check_model_materials(staged_world, set(mat_guids), spec["world"])

    for role, rigs, model in (("view", view_rigs, staged_view), ("world", host["world_rigs"], staged_world)):
        entry = {"_type": "mdl_", "_path": spec[role], "$assetsDir": adir}
        if rigs:
            entry["$animrigs"] = rigs
        res["client_add"].append(entry)
        used = {"0x%016X" % x for x in model_material_guids(model)}
        res["client_expect"][guid_hex(spec[role])] = rigs + [g for g in mat_guids if g in used]

    # The server picks viewmodel activities from its own copy of the model:
    # without the rigs there it has no sequences and the client sits on ref frame 0.
    dedi_mode = spec.get("dedi") or ("source" if src["view"].suffix.lower() == ".rmdl" else "host")
    for role, rigs in (("view", view_rigs), ("world", host["world_rigs"])):
        if dedi_mode == "source":
            if src[role].suffix.lower() != ".rmdl":
                raise SystemExit(f"{key}: dedi 'source' needs an S3 .rmdl for {role}; use \"dedi\": \"host\"")
            stage_model(src[role], spec[role], rigs, ds(), (".rmdl", ".phy"))
        elif dedi_mode == "compiled":
            stage_dedi_compiled(src[role], spec[role], work / ("dedi_" + role), oracle["dedi_" + role])
        else:
            stage_dedi_from_host(oracle["dedi_" + role], spec[role])
        client_skins = rdump.parse(str(cs() / spec[role]))[1]["numskinfamilies"]
        dedi_skins = v54_skin_families(ds() / spec[role])
        if client_skins > 1 and dedi_skins != client_skins:
            raise SystemExit(f"{key} {role}: client has {client_skins} skin families, dedi {dedi_skins} -- "
                             f"SetSkin past the dedi's count is rejected; use \"dedi\": \"compiled\"")
        entry = {"_type": "mdl_", "_path": spec[role], "$assetsDir": ds().as_posix() + "/"}
        if rigs:
            entry["$animrigs"] = rigs
        res["dedi_add"].append(entry)
        res["dedi_expect"][guid_hex(spec[role])] = rigs
    log(f"  dedi model from {dedi_mode}")
    return res


NEW_SPEC_NOTE = ("Model in Blender on skeleton/<view skeleton>.smd (view) and skeleton/<world skeleton>.smd "
                 "(world). Do not move, add, rename or delete bones. Skin the weapon to the host's core bone "
                 "(hosts/hosts.md). Name each Blender material like the last part of a materials[].name "
                 "below, export body.smd and w_body.smd next to this file, fill in the textures, then run "
                 "melee_modding.py build <key> --install.")


def cmd_hosts() -> int:
    for k, h in sorted(HOSTS.items()):
        moving = [b for b in h["view_mesh_bones"] if b != h["core_bone"] and "smear" not in b]
        log(f"{k:18} {h['melee']:28} core bone {h['core_bone']:22} {len(moving):2} moving parts | {h['good_for']}")
    log(f"details per host: {HOSTS_CACHE}")
    return 0


def cmd_new(key: str, host: str, mod_id: str, name: str | None) -> int:
    if host not in HOSTS:
        raise SystemExit(f"unknown host '{host}' (hosts: {', '.join(sorted(HOSTS))})")
    if not re.fullmatch(r"[a-z0-9_]{2,32}", key):
        raise SystemExit("key must be [a-z0-9_]{2,32}")
    if not MOD_ID.match(mod_id):
        raise SystemExit("mod id must be <Team>.<Name>, letters/digits/_ (e.g. Me.FrostAxe)")
    d = HEIRLOOMS / key
    if (d / "spec.json").exists():
        raise SystemExit(f"{d / 'spec.json'} already exists")
    sys.path.insert(0, str(ROOT / "lib"))
    from host_skeleton import write_skeleton_smd
    oracle = ensure_oracle(host)
    for role in ("view", "world"):
        n = write_skeleton_smd(oracle[role], d / "skeleton" / f"{Path(HOSTS[host][role]).stem}_skeleton.smd")
        log(f"  skeleton/{Path(HOSTS[host][role]).stem}_skeleton.smd ({n} bones)")
    ns = mod_id.replace(".", "_").lower()
    blank = {t: "" for t in ("albedoTexture", "normalTexture", "glossTexture", "specTexture")}
    spec = {
        "key": key, "host": host,
        "mod": {"id": mod_id, "author": mod_id.split(".")[0], "name": name or key, "version": "1.0.0",
                "description": f"{name or key}: a custom heirloom on the {host} animations."},
        "view": f"mdl/{ns}__{key}/ptpov_{key}.rmdl", "world": f"mdl/{ns}__{key}/w_{key}.rmdl",
        "view_src": "body.smd", "world_src": "w_body.smd", "dedi": "host",
        "materials": [{
            "name": f"models/{ns}__{key}/body", "pack_name": f"models/{ns}__{key}/body",
            "template": "0x57523F311A60CE34", "surfaceProp": "metal",
            "textures": dict(blank, emissiveTexture="solid:0,0,0,255", aoTexture="solid:255,255,255,255",
                             cavityTexture="solid:255,255,255,255")}],
        "weapon": {"printname": name or key},
        "_howto": NEW_SPEC_NOTE,
    }
    (d / "spec.json").write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    log(f"wrote {d / 'spec.json'}")
    log(NEW_SPEC_NOTE)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("hosts", help="list the heirloom rigs")
    n = sp.add_parser("new", help="start a heirloom: spec + host skeletons to model on")
    n.add_argument("key")
    n.add_argument("--host", required=True)
    n.add_argument("--mod", required=True, metavar="Team.Name")
    n.add_argument("--name", help="display name")
    b = sp.add_parser("build", help="build the mod")
    b.add_argument("key", help="heirlooms/<key>/spec.json, or a path to a spec .json")
    b.add_argument("--install", action="store_true", help="close the game, copy the mod into <game>/mods and enable it")
    o = sp.add_parser("oracle", help="export a host's models and materials into work/oracle")
    o.add_argument("host")
    args = ap.parse_args()

    if args.cmd == "hosts":
        return cmd_hosts()
    if args.cmd == "new":
        return cmd_new(args.key, args.host, args.mod, args.name)
    if args.cmd == "oracle":
        if args.host not in HOSTS:
            raise SystemExit(f"unknown host '{args.host}' (hosts: {', '.join(sorted(HOSTS))})")
        for k, v in ensure_oracle(args.host).items():
            log(f"{k}: {v}")
        return 0
    spec = load_spec(args.key)
    if args.install:
        kill_game()
    build_mod(spec, build_one(spec), install=args.install)
    cls = class_names(spec)
    log("DONE -- in game: sv_cheats 1; give " + cls["primary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
