"""Render an SMD weapon mesh into a 256x256 mod icon (software rasterizer, numpy + PIL).

  py render_icon.py <out.png> <model.smd> --mat NAME=albedo.png[,emissive.png[,r,g,b]] ... [--flipv] [--rot DEG] [--gain X]

No SMD? Export one from a built pak: rsx -nogui -export --exporttypes mdl_ --exportsetting mdl_=3
--exportpak <pak name> <pak> <common.rpak> (always pass --exportpak or RSX exports every model).

NAME is the SMD material name ("*" = any material without its own entry). An emissive map
adds glow; r,g,b tints it (e.g. a glowing blade colour). --flipv when the texture lands
upside down (render both and look). --rot rotates the final icon (multiples of 90).
"""
import argparse
import math

import numpy as np
from PIL import Image, ImageFilter

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("smd")
ap.add_argument("--mat", action="append", default=[])
ap.add_argument("--flipv", action="store_true")
ap.add_argument("--rot", type=int, default=0)
ap.add_argument("--gain", type=float, default=1.0, help="exposure for dark albedos")
a = ap.parse_args()
SS = 4
N = 256 * SS

mats = {}
for spec in a.mat:
    name, files = spec.split("=", 1)
    parts = files.split(",")
    alb = np.asarray(Image.open(parts[0]).convert("RGB"), np.float64) / 255
    emi = np.asarray(Image.open(parts[1]).convert("L"), np.float64) / 255 if len(parts) > 1 and parts[1] else None
    tint = np.array([float(x) for x in parts[2:5]]) / 255 if len(parts) >= 5 else np.ones(3)
    mats[name] = (alb, emi, tint)


def load_smd(path):
    tris, mat_of, cur, mat, sec = [], [], [], None, None
    for line in open(path, encoding="latin1"):
        s = line.strip()
        if s in ("nodes", "skeleton", "triangles"):
            sec = s
            continue
        if s == "end":
            sec = None
            continue
        if sec != "triangles" or not s:
            continue
        parts = s.split()
        if len(parts) < 9:
            mat = s
            continue
        try:
            v = [float(x) for x in parts[1:9]]
        except ValueError:
            mat = s
            continue
        cur.append(v)
        if len(cur) == 3:
            tris.append(cur)
            mat_of.append(mat)
            cur = []
    return np.array(tris, dtype=np.float64), mat_of


tris, mat_of = load_smd(a.smd)
pos = tris[:, :, 0:3].reshape(-1, 3)
mn, mx = pos.min(0), pos.max(0)
ext = mx - mn
long_ax, thin_ax = int(np.argmax(ext)), int(np.argmin(ext))
mid_ax = 3 - long_ax - thin_ax
c = (mn + mx) / 2
P, Nn = tris[:, :, 0:3] - c, tris[:, :, 3:6]
sx, sy, Z = P[..., long_ax], P[..., mid_ax], P[..., thin_ax]
nx, ny, nz = Nn[..., long_ax], Nn[..., mid_ax], Nn[..., thin_ax]
ang = math.radians(-45)
ca, sa = math.cos(ang), math.sin(ang)
X, Y = sx * ca - sy * sa, sx * sa + sy * ca
NX, NY = nx * ca - ny * sa, nx * sa + ny * ca
span = max(X.max() - X.min(), Y.max() - Y.min())
scale = N * 0.86 / span
X = (X - (X.max() + X.min()) / 2) * scale + N / 2
Y = N / 2 - (Y - (Y.max() + Y.min()) / 2) * scale

color, glow, glowc = np.zeros((N, N, 3)), np.zeros((N, N)), np.zeros((N, N, 3))
alpha, zbuf = np.zeros((N, N)), np.full((N, N), np.inf)
light = np.array([-0.45, 0.6, 0.66])
light /= np.linalg.norm(light)
grey = (np.full((4, 4, 3), 0.5), None, np.ones(3))

for t in range(len(tris)):
    alb, emi, tint = mats.get(mat_of[t], mats.get("*", grey))
    th, tw = alb.shape[:2]
    x, y, z = X[t], Y[t], Z[t]
    x0, x1 = int(max(0, np.floor(x.min()))), int(min(N - 1, np.ceil(x.max())))
    y0, y1 = int(max(0, np.floor(y.min()))), int(min(N - 1, np.ceil(y.max())))
    if x1 < x0 or y1 < y0:
        continue
    gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
    d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
    if abs(d) < 1e-12:
        continue
    w0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / d
    w1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / d
    w2 = 1 - w0 - w1
    inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
    sub = zbuf[y0:y1 + 1, x0:x1 + 1]
    upd = inside & (zz < sub)
    if not upd.any():
        continue
    u = w0 * tris[t, 0, 6] + w1 * tris[t, 1, 6] + w2 * tris[t, 2, 6]
    v = w0 * tris[t, 0, 7] + w1 * tris[t, 1, 7] + w2 * tris[t, 2, 7]
    if a.flipv:
        v = 1 - v
    tu = np.clip((u % 1.0) * tw, 0, tw - 1).astype(int)
    tv = np.clip((v % 1.0) * th, 0, th - 1).astype(int)
    nrm = np.stack([w0 * NX[t, 0] + w1 * NX[t, 1] + w2 * NX[t, 2],
                    w0 * NY[t, 0] + w1 * NY[t, 1] + w2 * NY[t, 2],
                    -(w0 * nz[t, 0] + w1 * nz[t, 1] + w2 * nz[t, 2])], -1)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True) + 1e-9
    lam = np.abs(nrm @ light)
    spec = np.clip(nrm @ np.array([0.0, 0.0, 1.0]), 0, 1) ** 24 * 0.35
    base = alb[tv, tu]
    col = (base * (0.38 + 0.75 * lam)[..., None] + spec[..., None]) * a.gain
    e = np.zeros_like(lam)
    if emi is not None:
        eh, ew = emi.shape
        e = emi[np.clip((v % 1.0) * eh, 0, eh - 1).astype(int), np.clip((u % 1.0) * ew, 0, ew - 1).astype(int)]
        col = col * (1 - e[..., None] * 0.7) + e[..., None] * (tint * 0.6 + 0.55)
    sub[upd] = zz[upd]
    color[y0:y1 + 1, x0:x1 + 1][upd] = col[upd]
    glow[y0:y1 + 1, x0:x1 + 1][upd] = e[upd]
    glowc[y0:y1 + 1, x0:x1 + 1][upd] = (e[..., None] * tint)[upd]
    alpha[y0:y1 + 1, x0:x1 + 1][upd] = 1.0

yy, xx = np.mgrid[0:N, 0:N] / N - 0.5
r = np.sqrt(xx ** 2 + yy ** 2)
bg = np.stack([0.03 + 0.05 * (1 - r), 0.05 + 0.08 * (1 - r), 0.12 + 0.16 * (1 - r)], -1).clip(0, 1)
def gblur(arr, rad):
    return np.asarray(Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rad)), np.float64) / 255
sil = gblur(alpha, 10 * SS)
halo_c = np.stack([gblur(glowc[..., i], 9 * SS) for i in range(3)], -1)
cyan = np.array([0.25, 0.8, 1.0])
bg = (bg + sil[..., None] * 0.3 * cyan + halo_c * 1.6).clip(0, 1)
img = bg * (1 - alpha[..., None]) + color.clip(0, 1) * alpha[..., None]
out = Image.fromarray((img * 255).astype(np.uint8)).resize((256, 256), Image.LANCZOS)
for _ in range((a.rot // 90) % 4):
    out = out.transpose(Image.Transpose.ROTATE_90)
out.save(a.out)
print("wrote", a.out, "tris", len(tris), "materials", sorted(set(mat_of)))
