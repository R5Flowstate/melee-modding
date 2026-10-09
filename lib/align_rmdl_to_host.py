#!/usr/bin/env python3
"""Align a converted v17 rmdl skeleton to a stock host of the same family.

Reorders bones to the host's order, copies bind-pose + flags from the host
by name, and remaps every bone index the file carries (parents, linearbone,
attachments, hitboxes, seq weightlist, boneState). The mesh/VG stays ours;
boneState bytes are rewritten so VG local bones still hit the same names.
"""
from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rmdl_v17_dump import FIX, bones, parse  # noqa: E402

BONE_HDR = 12
BONE_DATA = 128
SEQ_STRIDE = 112
ATTACH_STRIDE = 56
HITBOX_STRIDE = 32
BONE_USED_BY_VERTEX_LOD0 = 0x400
BONE_USED_BY_VERTEX_MASK = 0x3FC00  # LOD0..LOD7


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _i16(b: bytes, o: int) -> int:
    return struct.unpack_from("<h", b, o)[0]


def _perm_bytes(src: bytes, order: list[int], elem: int) -> bytes:
    out = bytearray(len(order) * elem)
    for new_i, old_i in enumerate(order):
        out[new_i * elem:(new_i + 1) * elem] = src[old_i * elem:(old_i + 1) * elem]
    return bytes(out)


def _encode_fix(delta: int) -> int:
    if delta < 0 or delta > 0xFFFFF:
        raise ValueError("name offset %d" % delta)
    if delta <= 0xFFFE and (delta & 1) == 0:
        return delta
    if (delta % 16) == 0:
        o = (delta // 16) + 1
        if o <= 0xFFFF and (o & 1):
            return o
    raise ValueError("cannot pack name offset %d" % delta)


def align(host_path: Path, ours_path: Path) -> None:
    host_d, host_h = parse(str(host_path))
    ours_d, ours_h = parse(str(ours_path))
    host_bones = bones(host_d, host_h)
    ours_bones = bones(ours_d, ours_h)
    ours_by_name = {b["name"]: b for b in ours_bones}

    order_names: list[str] = []
    seen: set[str] = set()
    for b in host_bones:
        if b["name"] in ours_by_name and b["name"] not in seen:
            order_names.append(b["name"])
            seen.add(b["name"])
    extras = [b["name"] for b in ours_bones if b["name"] not in seen]
    order_names.extend(extras)
    if len(order_names) != len(ours_bones):
        raise SystemExit(f"{ours_path}: name collision in reorder")

    old_of = {b["name"]: b["i"] for b in ours_bones}
    order = [old_of[n] for n in order_names]
    old_to_new = {old: new for new, old in enumerate(order)}
    n = len(order)

    raw = bytearray(ours_d)
    hb = FIX(ours_h["boneHdrOffset"])
    db = FIX(ours_h["boneDataOffset"])
    old_hdr = bytes(raw[hb:hb + n * BONE_HDR])
    old_dat = bytes(raw[db:db + n * BONE_DATA])
    new_hdr = bytearray(_perm_bytes(old_hdr, order, BONE_HDR))
    new_dat = bytearray(_perm_bytes(old_dat, order, BONE_DATA))
    for new_i, old_i in enumerate(order):
        old_szn = struct.unpack_from("<H", old_hdr, old_i * BONE_HDR + 10)[0]
        old_abs = hb + old_i * BONE_HDR + FIX(old_szn)
        new_rel = old_abs - (hb + new_i * BONE_HDR)
        struct.pack_into("<H", new_hdr, new_i * BONE_HDR + 10, _encode_fix(new_rel))

    host_by_name = {b["name"]: b for b in host_bones}
    host_idx = {b["name"]: b["i"] for b in host_bones}
    host_hb = FIX(host_h["boneDataOffset"])

    for new_i, name in enumerate(order_names):
        parent = _i16(new_dat, new_i * BONE_DATA + 116)
        if parent >= 0:
            struct.pack_into("<h", new_dat, new_i * BONE_DATA + 116, old_to_new[parent])
        hb_host = host_by_name.get(name)
        if hb_host is None:
            continue
        src = host_d[host_hb + hb_host["i"] * BONE_DATA:host_hb + (hb_host["i"] + 1) * BONE_DATA]
        new_dat[new_i * BONE_DATA:new_i * BONE_DATA + 116] = src[0:116]
        struct.pack_into("<H", new_dat, new_i * BONE_DATA + 118, struct.unpack_from("<H", src, 118)[0])
        struct.pack_into("<i", new_dat, new_i * BONE_DATA + 120, struct.unpack_from("<i", src, 120)[0])
        hp = hb_host["parent"]
        if hp >= 0:
            hp_name = host_bones[hp]["name"]
            if hp_name in old_of:
                struct.pack_into("<h", new_dat, new_i * BONE_DATA + 116, order_names.index(hp_name))

    raw[hb:hb + n * BONE_HDR] = new_hdr
    raw[db:db + n * BONE_DATA] = new_dat

    # linearbone arrays
    lb = FIX(ours_h["linearboneindex"])
    if lb and lb + 14 <= len(raw):
        lb_n, flags_i, par_i, pos_i, quat_i, rot_i, p2b_i = struct.unpack_from("<7H", raw, lb)
        if lb_n != n:
            raise SystemExit(f"{ours_path}: linearbone {lb_n} != {n}")
        specs = (
            (flags_i, 4),
            (par_i, 2),
            (pos_i, 12),
            (quat_i, 16),
            (rot_i, 12),
            (p2b_i, 48),
        )
        for rel, elem in specs:
            if not rel:
                continue
            arr = lb + FIX(rel)
            block = bytes(raw[arr:arr + n * elem])
            raw[arr:arr + n * elem] = _perm_bytes(block, order, elem)
        if par_i:
            arr = lb + FIX(par_i)
            for new_i in range(n):
                p = _i16(raw, arr + new_i * 2)
                if p >= 0:
                    struct.pack_into("<h", raw, arr + new_i * 2, old_to_new[p])
        hlb = FIX(host_h["linearboneindex"])
        if hlb and hlb + 14 <= len(host_d):
            h_n, h_flags_i, h_par_i, h_pos_i, h_quat_i, h_rot_i, h_p2b_i = struct.unpack_from("<7H", host_d, hlb)
            overlays = (
                (flags_i, h_flags_i, 4),
                (pos_i, h_pos_i, 12),
                (quat_i, h_quat_i, 16),
                (rot_i, h_rot_i, 12),
                (p2b_i, h_p2b_i, 48),
            )
            for ours_rel, host_rel, elem in overlays:
                if not ours_rel or not host_rel:
                    continue
                arr = lb + FIX(ours_rel)
                h_arr = hlb + FIX(host_rel)
                for new_i, name in enumerate(order_names):
                    hi = host_idx.get(name)
                    if hi is None or hi >= h_n:
                        continue
                    raw[arr + new_i * elem:arr + (new_i + 1) * elem] = host_d[h_arr + hi * elem:h_arr + (hi + 1) * elem]
            if par_i and h_par_i:
                arr = lb + FIX(par_i)
                for new_i, name in enumerate(order_names):
                    hb = host_by_name.get(name)
                    if hb is None or hb["parent"] < 0:
                        continue
                    hp_name = host_bones[hb["parent"]]["name"]
                    if hp_name in old_of:
                        struct.pack_into("<h", raw, arr + new_i * 2, order_names.index(hp_name))

    # attachments
    na = ours_h["numlocalattachments"]
    att = FIX(ours_h["localattachmentindex"])
    for i in range(na):
        o = att + i * ATTACH_STRIDE
        bone = _u16(raw, o + 2)
        if bone < n:
            struct.pack_into("<H", raw, o + 2, old_to_new[bone])

    # hitboxes
    if ours_h["numhitboxsets"]:
        hs = FIX(ours_h["hitboxsetindex"])
        nhb = _u16(raw, hs + 2)
        hbi = FIX(_u16(raw, hs + 4))
        box0 = hs + hbi
        for i in range(nhb):
            o = box0 + i * HITBOX_STRIDE
            bone = _u16(raw, o)
            if bone < n:
                struct.pack_into("<H", raw, o, old_to_new[bone])

    # seq weightlist (one placeholder seq)
    if ours_h["numlocalseq"]:
        seq = FIX(ours_h["localseqindex"])
        # Stock ref: flags 0x80000, paramindex -1/-1, fade 0.2/0.2.
        # 0,0 is pose slot 0 and sticks the gun on seq 0.
        sflags = struct.unpack_from("<i", raw, seq + 4)[0]
        struct.pack_into("<i", raw, seq + 4, sflags | 0x80000)
        struct.pack_into("<hh", raw, seq + 44, -1, -1)
        struct.pack_into("<ff", raw, seq + 64, 0.2, 0.2)
        wl = _u16(raw, seq + 82)
        if wl:
            arr = seq + FIX(wl)
            if arr + n * 4 <= len(raw):
                block = bytes(raw[arr:arr + n * 4])
                raw[arr:arr + n * 4] = _perm_bytes(block, order, 4)

    # boneState: values are studio bone indices
    bsc = ours_h["boneStateCount"]
    if bsc:
        bso = 0xB0 + FIX(ours_h["boneStateOffset"])
        for i in range(bsc):
            old_b = raw[bso + i]
            if old_b < n:
                raw[bso + i] = old_to_new[old_b]

    # Host flags describe the host mesh's skinning. Our mesh may ride a bone the host
    # only uses for attachments (e.g. knife_base vs the host's def_magazine); without
    # the vertex bits the renderer never sets that bone up and the mesh collapses.
    src_flags = {b["name"]: b["flags"] for b in ours_bones}
    used: set[int] = set()
    if bsc:
        bso = 0xB0 + FIX(ours_h["boneStateOffset"])
        for i in range(bsc):
            b = raw[bso + i]
            while 0 <= b < n and b not in used:
                used.add(b)
                b = _i16(raw, db + b * BONE_DATA + 116)
    lb_flags = None
    if lb and lb + 14 <= len(raw):
        lb_flags = lb + FIX(struct.unpack_from("<7H", raw, lb)[1])
    for b in sorted(used):
        vbits = src_flags[order_names[b]] & BONE_USED_BY_VERTEX_MASK
        if not vbits:
            vbits = BONE_USED_BY_VERTEX_LOD0
        o = db + b * BONE_DATA + 120
        cur = struct.unpack_from("<i", raw, o)[0]
        if cur & vbits != vbits:
            struct.pack_into("<i", raw, o, cur | vbits)
            if lb_flags:
                fo = lb_flags + b * 4
                struct.pack_into("<i", raw, fo, struct.unpack_from("<i", raw, fo)[0] | vbits)
            print(f"  vertex bits {vbits:#x} kept on {order_names[b]} (host {cur:#x})")

    # bonetablebyname: rebuild name-sorted index list
    btn = FIX(ours_h["bonetablebynameindex"])
    if btn and btn + n <= len(raw):
        ranked = sorted(range(n), key=lambda i: order_names[i].lower())
        raw[btn:btn + n] = bytes(ranked)

    bak = ours_path.with_suffix(ours_path.suffix + ".bak_pre_hostalign")
    if not bak.is_file():
        shutil.copy2(ours_path, bak)
    ours_path.write_bytes(raw)

    # verify
    nd, nh = parse(str(ours_path))
    nb = bones(nd, nh)
    names = [b["name"] for b in nb]
    if names != order_names:
        raise SystemExit(f"{ours_path}: reorder verify failed")
    host_names = [b["name"] for b in host_bones if b["name"] in ours_by_name]
    if names[:len(host_names)] != host_names:
        raise SystemExit(f"{ours_path}: host prefix mismatch")
    print("aligned", ours_path.name, "n", n, "host-prefix", len(host_names), "extras", len(extras))


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: align_rmdl_to_host.py <host.rmdl> <model.rmdl>")
    align(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
