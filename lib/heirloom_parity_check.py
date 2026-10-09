"""Offline twin of the server's heirloom parity gate.

For each mod weapon file, compares every key the server checks (MOD_HEIRLOOM_PARITY_KEYS) against the
stock heirloom it rides. Exit code = number of mismatches.

  python heirloom_parity_check.py <game weapons dir> <parity keys .gnut> <mod weapon .txt> <stock class> [...]
"""
import os
import re
import sys


def top_level(text):
    out, depth = {}, 0
    for line in text.splitlines():
        line = line.split("//")[0].strip()
        if line.startswith("{"):
            depth += 1
            continue
        if line.startswith("}"):
            depth -= 1
            continue
        m = re.match(r'"([^"]+)"\s+"([^"]*)"', line)
        if m and depth == 1 and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def stock(weapons, cls):
    path = os.path.join(weapons, cls + ".txt")
    if not os.path.isfile(path):
        raise SystemExit(f"weapon file {path} is missing from the game install")
    return top_level(open(path, encoding="utf-8", errors="ignore").read())


def numeric_equal(a, b):
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def main():
    args = sys.argv[1:]
    if len(args) < 4 or len(args) % 2:
        raise SystemExit(__doc__)
    weapons, keys_file = args[0], args[1]
    keys = re.findall(r'^\t"([^"]+)",$', open(keys_file, encoding="utf-8").read(), re.M)
    if not keys:
        raise SystemExit(f"no parity keys found in {keys_file}")
    bad = 0
    for path, cls in zip(args[2::2], args[3::2]):
        mine = top_level(open(path, encoding="utf-8", errors="ignore").read())
        host = stock(weapons, cls)
        for k in keys:
            if not numeric_equal(mine.get(k), host.get(k)):
                print(f"MISMATCH {os.path.basename(path)} {k}: mod={mine.get(k)} stock {cls}={host.get(k)}")
                bad += 1
    print(f"{bad} mismatch(es)")
    sys.exit(min(bad, 100))


if __name__ == "__main__":
    main()
