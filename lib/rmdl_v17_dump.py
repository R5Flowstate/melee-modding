"""Dump and survey S21 client mdl_ v17 (.rmdl) headers, bones and local sequences.

The v17 studiohdr is 228 bytes with 2-byte packed offsets. The engine decodes them as
FIX(o) = (o & 0xFFFE) << (4 * (o & 1)), so an ODD offset is the compact large-offset form
and means (o - 1) * 16. Most header offsets are relative to the start of the struct; a few
(boneState, groupHeader, lod) are relative to their own field.

Usage:
  rmdl_v17_dump.py header <file> [...]        header fields
  rmdl_v17_dump.py bones  <file>              bone table
  rmdl_v17_dump.py seqs   <file> [...]        local sequence descriptors
  rmdl_v17_dump.py survey <dir> [...]         flags/bone census over a corpus
  rmdl_v17_dump.py diff   <fileA> <fileB>     bone-table diff between two models
"""
import collections
import os
import struct
import sys


def FIX(o):
    return (o & 0xFFFE) << (4 * (o & 1))


HDR = [
    ("flags", "i", 0x00), ("checksum", "i", 0x04), ("sznameindex", "H", 0x08),
    ("hitboxsetindex", "H", 0x34), ("numhitboxsets", "B", 0x36),
    ("boneCount", "H", 0x74), ("boneHdrOffset", "H", 0x76), ("boneDataOffset", "H", 0x78),
    ("numlocalseq", "H", 0x7A), ("localseqindex", "H", 0x7C),
    ("activitylistversion", "b", 0x82),
    ("numlocalattachments", "B", 0x83), ("localattachmentindex", "H", 0x84),
    ("numlocalnodes", "H", 0x86), ("localnodenameindex", "H", 0x88),
    ("localNodeDataOffset", "H", 0x8A),
    ("numikchains", "H", 0x8C), ("ikchainindex", "H", 0x8E),
    ("numtextures", "H", 0x90), ("textureindex", "H", 0x92),
    ("numskinref", "H", 0x94), ("numskinfamilies", "H", 0x96), ("skinindex", "H", 0x98),
    ("numbodyparts", "H", 0x9A), ("bodypartindex", "H", 0x9C),
    ("uiPanelCount", "H", 0x9E), ("uiPanelOffset", "H", 0xA0),
    ("numlocalposeparameters", "H", 0xA2), ("localposeparamindex", "H", 0xA4),
    ("surfacepropindex", "H", 0xA6), ("keyvalueindex", "H", 0xA8),
    ("mdlHandle", "H", 0xAA),
    ("meshCount", "H", 0xAC), ("bonetablebynameindex", "H", 0xAE),
    ("boneStateOffset", "H", 0xB0), ("boneStateCount", "H", 0xB2),
    ("groupHeaderOffset", "H", 0xB4), ("groupHeaderCount", "H", 0xB6),
    ("lodOffset", "H", 0xB8), ("lodCount", "H", 0xBA),
    ("fadeDistance", "f", 0xBC), ("gatherSize", "f", 0xC0),
    ("numsrcbonetransform", "H", 0xC4), ("srcbonetransformindex", "H", 0xC6),
    ("sourceFilenameOffset", "H", 0xC8), ("linearboneindex", "H", 0xCA),
    ("procBoneCount", "H", 0xCC), ("procBoneOffset", "H", 0xCE),
    ("linearProcBoneOffset", "H", 0xD0),
    ("boneFollowerCount", "H", 0xD2), ("boneFollowerOffset", "H", 0xD4),
    ("bvhOffset", "H", 0xD6), ("bvhUnk", "H", 0xD8),
    ("unkDataCount", "H", 0xDA), ("unkDataOffset", "H", 0xDC),
    ("unkStrcOffset", "H", 0xDE), ("unk_E0", "i", 0xE0),
]

HEX_FIELDS = ("flags", "checksum", "unk_E0")

# studiohdr.flags bits that carry meaning for this pipeline. Bit 19 is the one that
# decides whether the model can animate at all: with it clear the client's model cache
# hands back no virtual model, so the animrig is never bound.
FLAG_NAMES = {
    0x00000001: "AUTOGEN_HITBOX",
    0x00000002: "USES_ENV_CUBEMAP",
    0x00000004: "FORCE_OPAQUE",
    0x00000008: "TRANSLUCENT_TWOPASS",
    0x00000010: "STATIC_PROP",
    0x00000080: "USES_BUMPMAPPING",
    0x00004000: "EXTRA_BONE_WEIGHTS",
    0x00010000: "AMBIENT_BOOST",
    0x00040000: "CAST_TEXTURE_SHADOWS",
    0x00080000: "USES_VIRTUAL_MODEL",
    0x00100000: "NO_ANIM_EVENTS",
    0x01000000: "USES_VERTEX_COLOR",
}


def parse(path):
    d = open(path, "rb").read()
    if len(d) < 0xE4:
        raise ValueError("shorter than a 228-byte v17 header")
    h = {n: struct.unpack_from("<" + f, d, o)[0] for n, f, o in HDR}
    return d, h


def cstr(d, o, limit=128):
    e = d.find(b"\0", o, o + limit)
    return d[o:e].decode("latin1") if e >= 0 else None


def flag_names(v):
    named = [n for bit, n in sorted(FLAG_NAMES.items()) if v & bit]
    rest = v & ~sum(b for b in FLAG_NAMES if v & b)
    if rest:
        named.append("0x%X" % rest)
    return "|".join(named) if named else "-"


def bones(d, h):
    out = []
    hb, db = FIX(h["boneHdrOffset"]), FIX(h["boneDataOffset"])
    for i in range(h["boneCount"]):
        ho = hb + i * 12
        contents, unk4, spl, spi, phys, szn = struct.unpack_from("<IBBHHH", d, ho)
        do = db + i * 128
        parent, unk76, flags, col, proctype, procindex = struct.unpack_from("<hHiBBH", d, do + 116)
        out.append(dict(i=i, name=cstr(d, ho + FIX(szn)) if szn else "",
                        parent=parent, flags=flags, unk76=unk76, col=col,
                        proctype=proctype, procindex=procindex,
                        contents=contents, phys=phys, unk4=unk4))
    return out


def seqs(d, h):
    out = []
    base = FIX(h["localseqindex"])
    for i in range(h["numlocalseq"]):
        o = base + i * 112
        szl, sza, flags, act, actw, nev, evi = struct.unpack_from("<HHiHHHH", d, o)
        numblends, animidxidx = struct.unpack_from("<HH", d, o + 40)
        out.append(dict(i=i, off=o, label=cstr(d, o + FIX(szl)) if szl else None,
                        activityName=cstr(d, o + FIX(sza)) if sza else None,
                        flags=flags, activity=act, actweight=actw, numevents=nev,
                        numblends=numblends, animindexindex=animidxidx))
    return out


def walk(paths):
    for p in paths:
        if os.path.isdir(p):
            for dp, _, fn in os.walk(p):
                for f in fn:
                    if f.lower().endswith(".rmdl"):
                        yield os.path.join(dp, f)
        else:
            yield p


def cmd_header(paths):
    for p in walk(paths):
        d, h = parse(p)
        print("=" * 72)
        print("%s  (%d bytes)" % (p, len(d)))
        for n, _, o in HDR:
            v = h[n]
            s = "0x%08X" % (v & 0xFFFFFFFF) if n in HEX_FIELDS else v
            print("  %-24s 0x%02X  %s" % (n, o, s))
        print("  flags decode:            %s" % flag_names(h["flags"] & 0xFFFFFFFF))


def cmd_bones(paths):
    for p in walk(paths):
        d, h = parse(p)
        print("=" * 72)
        print("%s  (%d bones)" % (p, h["boneCount"]))
        for b in bones(d, h):
            print("  %-4d %-34s parent=%-5d flags=0x%06X col=%-4d phys=%-4d proc=%d/%d"
                  % (b["i"], b["name"], b["parent"], b["flags"] & 0xFFFFFF,
                     b["col"], b["phys"], b["proctype"], b["procindex"]))


def cmd_seqs(paths):
    for p in walk(paths):
        d, h = parse(p)
        print("=" * 72)
        print("%s  numlocalseq=%d localseqindex=%d" % (p, h["numlocalseq"], h["localseqindex"]))
        for s in seqs(d, h):
            print("  seq%-3d off=%-7d label=%-24r act=%-20r flags=0x%X activity=%d "
                  "actweight=%d numevents=%d numblends=%d"
                  % (s["i"], s["off"], s["label"], s["activityName"], s["flags"],
                     s["activity"], s["actweight"], s["numevents"], s["numblends"]))


def cmd_survey(paths):
    """Flag/bone census. This is what settled the 0x80000 animation rule: across 623
    stock models every model carrying bit 19 is multi-bone and none is a static prop."""
    rows, bad = [], []
    for p in walk(paths):
        try:
            d, h = parse(p)
        except Exception as e:
            bad.append((p, e))
            continue
        rows.append((p, h["flags"] & 0xFFFFFFFF, h["boneCount"], h["numlocalseq"]))
    print("parsed %d models (%d unreadable)" % (len(rows), len(bad)))
    if not rows:
        return
    vm = [r for r in rows if r[1] & 0x80000]
    no = [r for r in rows if not r[1] & 0x80000]
    print("\nUSES_VIRTUAL_MODEL (0x80000) set: %d   clear: %d" % (len(vm), len(no)))
    print("  set   -> multi-bone %d / single-bone %d / static-prop %d"
          % (sum(1 for r in vm if r[2] > 1), sum(1 for r in vm if r[2] <= 1),
             sum(1 for r in vm if r[1] & 0x10)))
    print("  clear -> multi-bone %d / single-bone %d / static-prop %d"
          % (sum(1 for r in no if r[2] > 1), sum(1 for r in no if r[2] <= 1),
             sum(1 for r in no if r[1] & 0x10)))
    susp = [r for r in no if r[2] > 1 and not r[1] & 0x10]
    print("\nmulti-bone, non-static-prop, WITHOUT 0x80000 -- these cannot animate (%d):" % len(susp))
    for r in susp[:40]:
        print("   %-56s flags=0x%08X bones=%d" % (os.path.basename(r[0]), r[1], r[2]))
    if len(susp) > 40:
        print("   ... and %d more" % (len(susp) - 40))
    print("\nflag histogram:")
    for v, n in collections.Counter("0x%08X" % r[1] for r in rows).most_common(20):
        print("   %s  x%-5d %s" % (v, n, flag_names(int(v, 16))))
    bits = collections.Counter()
    for r in rows:
        for b in range(32):
            if r[1] >> b & 1:
                bits[1 << b] += 1
    print("\nbit frequency:")
    for bit, n in sorted(bits.items(), key=lambda kv: -kv[1]):
        print("   0x%08X  x%-5d %s" % (bit, n, FLAG_NAMES.get(bit, "?")))


def cmd_diff(paths):
    a, b = paths[0], paths[1]
    da, ha = parse(a)
    db, hb = parse(b)
    print("A %s\nB %s" % (a, b))
    for n, _, o in HDR:
        if ha[n] != hb[n]:
            fmt = (lambda v: "0x%08X" % (v & 0xFFFFFFFF)) if n in HEX_FIELDS else str
            print("  hdr %-24s A=%-14s B=%s" % (n, fmt(ha[n]), fmt(hb[n])))
    ba, bb = bones(da, ha), bones(db, hb)
    if len(ba) != len(bb):
        print("  bone count differs (%d vs %d) -- skipping bone diff" % (len(ba), len(bb)))
        return
    keys = ["name", "parent", "flags", "col", "proctype", "procindex", "phys"]
    ndiff = 0
    for x, y in zip(ba, bb):
        d = {k: (x[k], y[k]) for k in keys if x[k] != y[k]}
        if d:
            ndiff += 1
            if ndiff <= 20:
                print("  bone %-4d %s" % (x["i"], d))
    print("  %d/%d bones differ" % (ndiff, len(ba)))


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmds = {"header": cmd_header, "bones": cmd_bones, "seqs": cmd_seqs,
            "survey": cmd_survey, "diff": cmd_diff}
    fn = cmds.get(argv[1])
    if not fn:
        print(__doc__)
        return 2
    fn(argv[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
