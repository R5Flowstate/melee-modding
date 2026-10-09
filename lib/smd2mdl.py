#!/usr/bin/env python3
"""Compile Blender Source Tools SMD meshes into a Source MDL v49 (.mdl + .vvd + .dx90.vtx),
the input rmdlconv turns into an S21 model. Replaces studiomdl for heirloom meshes.

The skeleton comes from the first SMD (export it with the full host skeleton). With --host,
attachments and non-vertex bone flags are copied by name from the host S21 model, so no QC
is needed.

  python smd2mdl.py out/ptpov_x.mdl body.smd [more.smd ...]
         [--host host_v17.rmdl] [--rename blade=models/ns__x/blade_sknp ...]
         [--skins skins.json] [--surfaceprop metal]
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdl49 as M  # noqa: E402

BONE_USED_BY_HITBOX = 0x100
BONE_USED_BY_ATTACHMENT = 0x200
BONE_USED_BY_VERTEX_LOD0 = 0x400
BONE_VERTEX_MASK = 0x3FC00
MAX_WEIGHTS = 3


# ---------------------------------------------------------------- math
def quat_from_euler(x, y, z):
    sr, cr = math.sin(x / 2), math.cos(x / 2)
    sp, cp = math.sin(y / 2), math.cos(y / 2)
    sy, cy = math.sin(z / 2), math.cos(z / 2)
    return (sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def mat_from_euler(x, y, z, pos):
    sr, cr = math.sin(x), math.cos(x)
    sp, cp = math.sin(y), math.cos(y)
    sy, cy = math.sin(z), math.cos(z)
    return [[cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy, pos[0]],
            [cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy, pos[1]],
            [-sp, sr * cp, cr * cp, pos[2]]]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) + (a[i][3] if j == 3 else 0) for j in range(4)] for i in range(3)]


def mat_inv(m):
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [r[i] + [t[i]] for i in range(3)]


def xform(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) + m[i][3] for i in range(3))


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    n = math.sqrt(dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


# ---------------------------------------------------------------- smd
def parse_smd(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    nodes, frame, tris = [], {}, []
    i, n = 0, len(lines)
    while i < n:
        t = lines[i].strip()
        i += 1
        if t == "nodes":
            while lines[i].strip() != "end":
                parts = lines[i].split('"')
                idx, name, parent = int(parts[0]), parts[1], int(parts[2])
                nodes.append((idx, name, parent))
                i += 1
            i += 1
        elif t == "skeleton":
            seen_time = False
            while lines[i].strip() != "end":
                s = lines[i].split()
                if s[0] == "time":
                    if seen_time:
                        while lines[i].strip() != "end":
                            i += 1
                        break
                    seen_time = True
                else:
                    v = [float(x) for x in s[1:7]]
                    frame[int(s[0])] = (tuple(v[:3]), tuple(v[3:]))
                i += 1
            i += 1
        elif t == "triangles":
            while lines[i].strip() != "end":
                mat = lines[i].strip()
                corners = []
                for k in range(3):
                    s = lines[i + 1 + k].split()
                    f = [float(x) for x in s[1:9]]
                    links = []
                    if len(s) > 9:
                        nl = int(s[9])
                        links = [(int(s[10 + 2 * j]), float(s[11 + 2 * j])) for j in range(nl)]
                    if not links:
                        links = [(int(s[0]), 1.0)]
                    corners.append((tuple(f[0:3]), tuple(f[3:6]), (f[6], f[7]), links))
                tris.append((mat, corners))
                i += 4
            i += 1
    if not nodes:
        raise SystemExit(f"{path}: no nodes block")
    return {"nodes": nodes, "frame": frame, "tris": tris}


# ---------------------------------------------------------------- host (S21 v17 rmdl)
def v17_host(path: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import rmdl_v17_dump as rd
    data, h = rd.parse(str(path))
    bones = rd.bones(data, h)
    names = [b["name"] for b in bones]
    flags = {b["name"]: b["flags"] for b in bones}
    base = rd.FIX(h["localattachmentindex"])
    atts = []
    for i in range(h["numlocalattachments"]):
        o = base + i * 56
        name = rd.cstr(data, o + rd.FIX(struct.unpack_from("<H", data, o)[0]))
        bone = struct.unpack_from("<H", data, o + 2)[0]
        aflags = struct.unpack_from("<i", data, o + 4)[0]
        local = struct.unpack_from("<12f", data, o + 8)
        atts.append((name, names[bone], aflags, local))
    return {"flags": flags, "attachments": atts}


# ---------------------------------------------------------------- build
class Blob:
    """Little-endian output buffer with deferred string offsets (relative to a struct base)."""

    def __init__(self):
        self.b = bytearray()
        self.strings: list[tuple[int, int, str]] = []  # (field offset, struct base, text)

    def tell(self) -> int:
        return len(self.b)

    def align(self, n: int = 4) -> None:
        self.b += b"\0" * (-len(self.b) % n)

    def put(self, data: bytes) -> int:
        o = len(self.b)
        self.b += data
        return o

    def patch(self, off: int, fmt: str, *v) -> None:
        struct.pack_into(fmt, self.b, off, *v)

    def string(self, field_off: int, base: int, text: str) -> None:
        self.strings.append((field_off, base, text))

    def write_strings(self) -> None:
        pool: dict[str, int] = {}
        for field, base, text in self.strings:
            if text not in pool:
                pool[text] = self.put(text.encode("ascii") + b"\0")
            self.patch(field, "<i", pool[text] - base)


def compile_model(out: Path, smds: list[Path], host: Path | None, rename: dict[str, str],
                  skins: list[list[str]] | None, surfaceprop: str) -> dict:
    parsed = [parse_smd(p) for p in smds]
    ref = parsed[0]
    order = sorted(ref["nodes"])
    bone_names = [name for _, name, _ in order]
    if len(set(bone_names)) != len(bone_names):
        raise SystemExit("duplicate bone names in the skeleton")
    by_name = {name: i for i, name in enumerate(bone_names)}
    parents = [p for _, _, p in order]
    for i, p in enumerate(parents):
        if p >= i:
            raise SystemExit(f"bone {bone_names[i]} has parent {p} at or after itself; export parents first")
    local = []
    for idx, _, _ in order:
        if idx not in ref["frame"]:
            raise SystemExit(f"{smds[0].name}: no rest pose for bone {idx}")
        pos, rot = ref["frame"][idx]
        local.append((pos, rot))
    world = []
    for i, (pos, rot) in enumerate(local):
        m = mat_from_euler(*rot, pos)
        world.append(m if parents[i] < 0 else mat_mul(world[parents[i]], m))

    host_info = v17_host(host) if host else None
    nb = len(bone_names)
    flags = [0] * nb

    def mark(b: int, bit: int) -> None:
        while b >= 0:
            flags[b] |= bit
            b = parents[b]

    # materials in first-use order across all SMDs
    materials: list[str] = []
    for p in parsed:
        for mat, _ in p["tris"]:
            if mat not in materials:
                materials.append(mat)
    tex_names = [rename.get(m, m) for m in materials]

    # meshes: per body, per material; vertices deduplicated per mesh
    bodies = []
    for si, p in enumerate(parsed):
        remap = {}
        for idx, name, _ in p["nodes"]:
            if name not in by_name:
                raise SystemExit(f"{smds[si].name}: bone '{name}' is not in the skeleton of {smds[0].name}")
            remap[idx] = by_name[name]
        meshes = {}
        for mat, corners in p["tris"]:
            mesh = meshes.setdefault(materials.index(mat), {"verts": [], "index": {}, "tris": []})
            tri = []
            for pos, nrm, uv, links in corners:
                links = sorted(((remap[b], w) for b, w in links if w > 0), key=lambda bw: -bw[1])[:MAX_WEIGHTS]
                tot = sum(w for _, w in links) or 1.0
                links = tuple((b, w / tot) for b, w in links)
                key = (pos, nrm, uv, links)
                vi = mesh["index"].get(key)
                if vi is None:
                    vi = len(mesh["verts"])
                    mesh["index"][key] = vi
                    mesh["verts"].append(key)
                    for b, _ in links:
                        mark(b, BONE_USED_BY_VERTEX_LOD0)
                tri.append(vi)
            mesh["tris"].append(tri)
        for m in meshes.values():
            if len(m["verts"]) > 65535:
                raise SystemExit(f"{smds[si].name}: a material uses {len(m['verts'])} vertices; split it (max 65535)")
        bodies.append((smds[si].stem, [(k, meshes[k]) for k in sorted(meshes)]))

    # tangents (per mesh, from UV derivatives; V flipped the way the engine stores it)
    for _, meshes in bodies:
        for _, mesh in meshes:
            acc = [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in mesh["verts"]]
            for a, b, c in mesh["tris"]:
                va, vb, vc = mesh["verts"][a], mesh["verts"][b], mesh["verts"][c]
                e1, e2 = sub(vb[0], va[0]), sub(vc[0], va[0])
                du1, dv1 = vb[2][0] - va[2][0], (1 - vb[2][1]) - (1 - va[2][1])
                du2, dv2 = vc[2][0] - va[2][0], (1 - vc[2][1]) - (1 - va[2][1])
                r = du1 * dv2 - du2 * dv1
                if abs(r) < 1e-20:
                    continue
                r = 1.0 / r
                s = tuple((e1[k] * dv2 - e2[k] * dv1) * r for k in range(3))
                t = tuple((e2[k] * du1 - e1[k] * du2) * r for k in range(3))
                for v in (a, b, c):
                    for k in range(3):
                        acc[v][k] += s[k]
                        acc[v][3 + k] += t[k]
            tans = []
            for (pos, nrm, uv, links), a in zip(mesh["verts"], acc):
                s, t = tuple(a[:3]), tuple(a[3:])
                tan = norm(sub(s, tuple(nrm[k] * dot(nrm, s) for k in range(3))))
                if tan == (0.0, 0.0, 0.0):
                    tan = norm(cross(nrm, (0.0, 0.0, 1.0) if abs(nrm[2]) < 0.9 else (1.0, 0.0, 0.0)))
                w = -1.0 if dot(cross(nrm, tan), t) < 0 else 1.0
                tans.append(tan + (w,))
            mesh["tangents"] = tans

    # attachments + host bone flags
    attachments = []
    if host_info:
        for name, bone, aflags, loc in host_info["attachments"]:
            if bone in by_name:
                attachments.append((name, by_name[bone], aflags, loc))
                mark(by_name[bone], BONE_USED_BY_ATTACHMENT)
        for i, name in enumerate(bone_names):
            if name in host_info["flags"]:
                flags[i] = (flags[i] & BONE_VERTEX_MASK) | (host_info["flags"][name] & ~BONE_VERTEX_MASK)

    # bounds (studiomdl stores the hull in a frame rotated 90 degrees about Z)
    pts = [v[0] for _, ms in bodies for _, m in ms for v in m["verts"]] + [tuple(w[k][3] for k in range(3)) for w in world]
    rot = [(-p[1], p[0], p[2]) for p in pts]
    hull_min = tuple(min(p[k] for p in rot) for k in range(3))
    hull_max = tuple(max(p[k] for p in rot) for k in range(3))
    illum = tuple((hull_min[k] + hull_max[k]) / 2 for k in range(3))

    checksum = zlib.crc32(b"".join(p.read_bytes() for p in smds)) - (1 << 31)
    name = out.name
    f = Blob()
    hdr_off = f.put(b"\0" * M.size(M.HDR_FIELDS))
    hdr2_off = f.put(b"\0" * M.size(M.HDR2_FIELDS))
    H = {"id": 0x54534449, "version": 49, "checksum": checksum, "name": name.encode()[:63],
         "eyeposition": (0.0, 0.0, 0.0), "illumposition": illum, "hull_min": hull_min, "hull_max": hull_max,
         "view_bbmin": (0.0, 0.0, 0.0), "view_bbmax": (0.0, 0.0, 0.0), "flags": 0, "mass": 1.0, "contents": 1,
         "studiohdr2index": hdr2_off}

    # bones
    H["numbones"], H["boneindex"] = nb, f.tell()
    for i in range(nb):
        pos, rot_e = local[i]
        o = f.tell()
        f.put(M.pack(M.BONE_FIELDS, {
            "parent": parents[i], "bonecontroller": (-1,) * 6, "pos": pos, "quat": quat_from_euler(*rot_e),
            "rot": rot_e, "posscale": (0.0,) * 3, "rotscale": (0.0,) * 3,
            "poseToBone": tuple(x for row in mat_inv(world[i]) for x in row), "qAlignment": (0.0,) * 4,
            "flags": flags[i], "contents": 1}))
        f.string(o, o, bone_names[i])
        f.string(o + dict(zip((k for k, _ in M.BONE_FIELDS), _field_offsets(M.BONE_FIELDS)))["surfacepropidx"], o, surfaceprop)
    H["bonecontrollerindex"] = f.tell()
    H["numlocalattachments"], H["localattachmentindex"] = len(attachments), f.tell()
    for aname, bone, aflags, loc in attachments:
        o = f.put(M.pack(M.ATTACH_FIELDS, {"flags": aflags, "localbone": bone, "local": loc}))
        f.string(o, o, aname)
    H["numhitboxsets"], H["hitboxsetindex"] = 1, f.tell()
    o = f.put(M.pack(M.HBSET_FIELDS, {"numhitboxes": 0, "hitboxindex": 12}))
    f.string(o, o, "default")
    H["bonetablebynameindex"] = f.tell()
    f.put(bytes(sorted(range(nb), key=lambda i: bone_names[i].lower())))
    f.align()

    # one "ref" sequence over a one-frame "@ref" animation
    H["numlocalanim"], H["localanimindex"] = 1, f.tell()
    anim_off = f.put(b"\0" * M.size(M.ANIM_FIELDS))
    anim_data = f.put(struct.pack("<BBh", 255, 0, 0))
    f.align()
    f.b[anim_off:anim_off + M.size(M.ANIM_FIELDS)] = M.pack(M.ANIM_FIELDS, {
        "baseptr": -anim_off, "fps": 30.0, "numframes": 1, "animindex": anim_data - anim_off})
    f.string(anim_off + 4, anim_off, "@ref")
    H["numlocalseq"], H["localseqindex"] = 1, f.tell()
    seq_off = f.put(b"\0" * M.size(M.SEQ_FIELDS))
    weights = f.put(struct.pack(f"<{nb}f", *([1.0] * nb)))
    blend = f.put(struct.pack("<h", 0))
    f.align()
    f.b[seq_off:seq_off + M.size(M.SEQ_FIELDS)] = M.pack(M.SEQ_FIELDS, {
        "baseptr": -seq_off, "activity": -1, "eventindex": M.size(M.SEQ_FIELDS), "bbmin": hull_min, "bbmax": hull_max,
        "numblends": 1, "animindexindex": blend - seq_off, "groupsize": (1, 1), "paramindex": (-1, -1),
        "fadeintime": 0.2, "fadeouttime": 0.2, "weightlistindex": weights - seq_off})
    f.string(seq_off + 4, seq_off, "ref")
    f.string(seq_off + 8, seq_off, "")

    # bodyparts / models / meshes
    H["numbodyparts"], H["bodypartindex"] = len(bodies), f.tell()
    bp_offs = [f.put(b"\0" * 16) for _ in bodies]
    vvd_verts, vvd_tans = [], []
    lod_meshes = []
    for (bname, meshes), bpo in zip(bodies, bp_offs):
        mo = f.put(b"\0" * M.size(M.MODEL_FIELDS))
        f.b[bpo:bpo + 16] = M.pack(M.BODYPART_FIELDS, {"nummodels": 1, "base": 1, "modelindex": mo - bpo})
        f.string(bpo, bpo, bname)
        meo = f.tell()
        model_first = len(vvd_verts)
        offset = 0
        for k, (mat, mesh) in enumerate(meshes):
            o = f.put(M.pack(M.MESH_FIELDS, {
                "material": mat, "modelindex": mo - (meo + k * M.size(M.MESH_FIELDS)), "numvertices": len(mesh["verts"]),
                "vertexoffset": offset, "meshid": k, "numLODVertexes": (len(mesh["verts"]),) * 8}))
            offset += len(mesh["verts"])
            vvd_verts += mesh["verts"]
            vvd_tans += mesh["tangents"]
            lod_meshes.append(mesh)
        f.b[mo:mo + M.size(M.MODEL_FIELDS)] = M.pack(M.MODEL_FIELDS, {
            "name": (bname + ".smd").encode()[:63], "nummeshes": len(meshes), "meshindex": meo - mo,
            "numvertices": offset, "vertexindex": model_first * 48, "tangentsindex": model_first * 16})
    for name_ in ("localnodeindex", "localnodenameindex", "flexdescindex", "flexcontrollerindex", "flexruleindex",
                  "ikchainindex", "mouthindex", "localposeparamindex", "localikautoplaylockindex", "flexcontrolleruiindex"):
        H[name_] = f.tell()

    # textures, cdtextures, skins (family 0 is the SMD materials in first-use order)
    families = [list(range(len(tex_names)))]
    for fam in (skins or [])[1:]:
        if len(fam) != len(materials):
            raise SystemExit(f"skin family {fam} needs {len(materials)} entries (one per material)")
        row = []
        for m in fam:
            n_ = rename.get(m, m)
            if n_ not in tex_names:
                tex_names.append(n_)
            row.append(tex_names.index(n_))
        families.append(row)
    H["numtextures"], H["textureindex"] = len(tex_names), f.tell()
    for t in tex_names:
        o = f.put(M.pack(M.TEXTURE_FIELDS, {}))
        f.string(o, o, t)
    H["numcdtextures"], H["cdtextureindex"] = 1, f.tell()
    f.string(f.put(b"\0" * 4), 0, "")
    H["numskinref"], H["numskinfamilies"], H["skinindex"] = len(materials), len(families), f.tell()
    f.put(struct.pack(f"<{len(materials) * len(families)}h", *[i for fam in families for i in fam]))
    f.align()
    H["keyvalueindex"], H["keyvaluesize"] = f.tell(), 0
    H["includemodelindex"] = H["animblockindex"] = f.tell()

    # linear bone table (hdr2-relative)
    lb = f.tell()
    f.put(b"\0" * M.size(M.LINEARBONE_FIELDS))
    arrays = {}
    for key, fmt_, vals in (
            ("flagsindex", "i", flags), ("parentindex", "i", parents),
            ("posindex", "3f", [l[0] for l in local]), ("quatindex", "4f", [quat_from_euler(*l[1]) for l in local]),
            ("rotindex", "3f", [l[1] for l in local]),
            ("posetoboneindex", "12f", [tuple(x for row in mat_inv(w) for x in row) for w in world]),
            ("posscaleindex", "3f", [(0.0,) * 3] * nb), ("rotscaleindex", "3f", [(0.0,) * 3] * nb),
            ("qalignmentindex", "4f", [(0.0,) * 4] * nb)):
        arrays[key] = f.tell() - lb
        for v in vals:
            f.put(struct.pack("<" + fmt_, *(v if isinstance(v, tuple) else (v,))))
    f.b[lb:lb + M.size(M.LINEARBONE_FIELDS)] = M.pack(M.LINEARBONE_FIELDS, dict(numbones=nb, **arrays))
    H2 = {"srcbonetransformindex": f.tell() - hdr2_off, "linearboneindex": lb - hdr2_off}

    f.b[hdr_off:hdr_off + M.size(M.HDR_FIELDS)] = M.pack(M.HDR_FIELDS, H)
    f.b[hdr2_off:hdr2_off + M.size(M.HDR2_FIELDS)] = M.pack(M.HDR2_FIELDS, H2)
    ho = dict(zip((k for k, _ in M.HDR_FIELDS), _field_offsets(M.HDR_FIELDS)))
    f.string(hdr_off + ho["surfacepropindex"], hdr_off, surfaceprop)
    f.string(hdr_off + ho["szanimblocknameindex"], hdr_off, "")
    f.string(hdr2_off + dict(zip((k for k, _ in M.HDR2_FIELDS), _field_offsets(M.HDR2_FIELDS)))["sznameindex"], hdr2_off, name)
    f.write_strings()
    f.align()
    f.patch(hdr_off + ho["length"], "<i", f.tell())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(f.b))

    # VVD
    nv = len(vvd_verts)
    vvd = bytearray(M.pack(M.VVD_HDR, {"id": 0x56534449, "version": 4, "checksum": checksum, "numLODs": 1,
                                        "numLODVertexes": (nv,) * 8, "fixupTableStart": 64, "vertexDataStart": 64,
                                        "tangentDataStart": 64 + 48 * nv}))
    for pos, nrm, uv, links in vvd_verts:
        w = [l[1] for l in links] + [0.0] * (3 - len(links))
        b = [l[0] for l in links] + [0] * (3 - len(links))
        vvd += struct.pack("<3f3Bb3f3f2f", *w, *b, len(links), *pos, *nrm, uv[0], 1.0 - uv[1])
    for t in vvd_tans:
        vvd += struct.pack("<4f", *t)
    out.with_suffix(".vvd").write_bytes(bytes(vvd))

    # VTX: one LOD, one strip group and one triangle-list strip per mesh
    out.with_suffix(".dx90.vtx").write_bytes(build_vtx(bodies, checksum))
    return {"bones": nb, "materials": tex_names, "verts": nv, "tris": sum(len(m["tris"]) for _, ms in bodies for _, m in ms),
            "attachments": len(attachments), "skins": len(families)}


def _field_offsets(fields):
    offs, o = [], 0
    for _, fm in fields:
        offs.append(o)
        o += struct.calcsize("<" + fm)
    return offs


def build_vtx(bodies, checksum: int) -> bytes:
    hdr_size = M.size(M.VTX_HDR)
    out = bytearray(b"\0" * hdr_size)
    nbp = len(bodies)
    bp_off = len(out)
    out += b"\0" * (8 * nbp)
    for bi, (_, meshes) in enumerate(bodies):
        model_off = len(out)
        struct.pack_into("<2i", out, bp_off + 8 * bi, 1, model_off - (bp_off + 8 * bi))
        out += b"\0" * 8
        lod_off = len(out)
        struct.pack_into("<2i", out, model_off, 1, lod_off - model_off)
        out += b"\0" * 12
        mesh_off = len(out)
        struct.pack_into("<2if", out, lod_off, len(meshes), mesh_off - lod_off, 0.0)
        out += b"\0" * (9 * len(meshes))
        sg_offs = []
        for k in range(len(meshes)):
            sg = len(out)
            sg_offs.append(sg)
            struct.pack_into("<2iB", out, mesh_off + 9 * k, 1, sg - (mesh_off + 9 * k), 0)
            out += b"\0" * M.size(M.VTX_STRIPGROUP)
        for k, (_, mesh) in enumerate(meshes):
            sg = sg_offs[k]
            bones = []
            for v in mesh["verts"]:
                for b, _ in v[3]:
                    if b not in bones:
                        bones.append(b)
            hw = {b: i for i, b in enumerate(bones)}
            strip = len(out)
            out += b"\0" * M.size(M.VTX_STRIP)
            verts = len(out)
            for vi, v in enumerate(mesh["verts"]):
                ids = [hw[b] for b, _ in v[3]] + [0] * (3 - len(v[3]))
                out += struct.pack("<3BBH3b", 0, 1, 2, len(v[3]), vi, *ids)
            idx = len(out)
            for tri in mesh["tris"]:
                out += struct.pack("<3H", tri[0], tri[2], tri[1])  # studiomdl winding
            bsc = len(out)
            for i, b in enumerate(bones):
                out += struct.pack("<2i", i, b)
            topo = len(out)
            out[strip:strip + M.size(M.VTX_STRIP)] = M.pack(M.VTX_STRIP, {
                "numIndices": 3 * len(mesh["tris"]), "numVerts": len(mesh["verts"]), "numBones": len(bones),
                "flags": 1, "numBoneStateChanges": len(bones), "boneStateChangeOffset": bsc - strip,
                "topologyOffset": 0})
            out[sg:sg + M.size(M.VTX_STRIPGROUP)] = M.pack(M.VTX_STRIPGROUP, {
                "numVerts": len(mesh["verts"]), "vertOffset": verts - sg, "numIndices": 3 * len(mesh["tris"]),
                "indexOffset": idx - sg, "numStrips": 1, "stripOffset": strip - sg, "flags": 2,
                "topologyOffset": topo - sg})
    mrl = len(out)
    out += struct.pack("<2i", 0, 0)
    out[:hdr_size] = M.pack(M.VTX_HDR, {"version": 7, "vertCacheSize": 24, "maxBonesPerStrip": 53, "maxBonesPerFace": 9,
                                       "maxBonesPerVert": 3, "checkSum": checksum, "numLODs": 1,
                                       "materialReplacementListOffset": mrl, "numBodyParts": nbp, "bodyPartOffset": bp_off})
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("smd", type=Path, nargs="+")
    ap.add_argument("--host", type=Path)
    ap.add_argument("--rename", action="append", default=[], metavar="SMD_MATERIAL=ENGINE_NAME")
    ap.add_argument("--skins", type=Path, help="JSON list of skin families, each a list of SMD material names")
    ap.add_argument("--surfaceprop", default="metal")
    a = ap.parse_args()
    rename = dict(r.split("=", 1) for r in a.rename)
    skins = json.loads(a.skins.read_text(encoding="utf-8")) if a.skins else None
    info = compile_model(a.out, a.smd, a.host, rename, skins, a.surfaceprop)
    print(f"{a.out.name}: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
