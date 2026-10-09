"""Write a host model's skeleton (bones + rest pose) as an SMD to import into Blender."""
from __future__ import annotations

import math
import struct
from pathlib import Path

from rmdl_v17_dump import FIX, bones, parse


def qmat(q):
    x, y, z, w = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def mat_euler(m):
    """R = Rz(z) Ry(y) Rx(x) -> radians (x, y, z), the SMD convention."""
    sy = max(-1.0, min(1.0, -m[2][0]))
    y = math.asin(sy)
    if abs(math.cos(y)) > 1e-6:
        return math.atan2(m[2][1], m[2][2]), y, math.atan2(m[1][0], m[0][0])
    return math.atan2(-m[0][1], m[1][1]), y, 0.0


def write_skeleton_smd(host_rmdl: Path, out_smd: Path) -> int:
    d, h = parse(str(host_rmdl))
    db = FIX(h["boneDataOffset"])
    bs = []
    for b in bones(d, h):
        o = db + b["i"] * 128
        bs.append((b["name"], b["parent"], struct.unpack_from("<3f", d, o + 64), struct.unpack_from("<4f", d, o + 76)))
    smd = ["version 1", "nodes"]
    smd += ['  %d "%s" %d' % (i, name, parent) for i, (name, parent, _, _) in enumerate(bs)]
    smd += ["end", "skeleton", "time 0"]
    for i, (_, _, pos, quat) in enumerate(bs):
        smd.append("  %d %f %f %f %f %f %f" % (i, *pos, *mat_euler(qmat(quat))))
    smd += ["end"]
    out_smd.parent.mkdir(parents=True, exist_ok=True)
    out_smd.write_text("\n".join(smd) + "\n", encoding="ascii")
    return len(bs)
