"""RTech::StringToGuid: the pak asset-name hash (e.g. "material/<name>_sknp.rpak")."""

M32 = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF


def string_to_guid(s: str) -> int:
    b = s.encode("latin1") + b"\x00" * 8  # pad for the 4-byte over-read

    def u32(off):
        return int.from_bytes(b[off:off + 4], "little")

    v1 = 0
    i = 0
    while True:
        w = u32(i)
        v4 = (~w & (w - 0x1010101) & 0x80808080) & M32
        v5 = (v4 ^ (v4 - 1)) & M32
        v6 = (((v5 & w) ^ 0x5C5C5C5C)) & M32
        v7 = (~v6 & (v6 - 0x1010101) & 0x80808080) & M32
        v8 = (v7 & ((-v7) & M32)) & M32
        if v7 != v8:
            v9 = 0xFF000000
            while True:
                v10 = v9
                if (v9 & v6) == 0:
                    v8 = (v8 | (v9 & 0x80808080)) & M32
                v9 = v9 >> 8
                if not (v10 >= 0x100):
                    break
        v11 = (0x633D5F1 * v1) & M64
        inner = ((((v5 & w) - 45 * (v8 >> 7)) & M32) & 0xDFDFDFDF) & M32
        v12 = ((0xFB8C4D96501 * inner) & M64) >> 24
        if v4:
            break
        i += 4
        acc = (v11 + v12) & M64
        v1 = ((acc >> 61) ^ acc) & M64

    v13 = (v5.bit_length() - 1) if v5 != 0 else -1
    idx = (i + (int(v13 / 8))) & M32  # C truncates toward zero
    return ((v12 + v11 - 0xAE502812AA7333 * idx)) & M64


if __name__ == "__main__":
    target_district = 0xF8268129E8031940
    target_colony = 0xB01E3461D1957788
    assert string_to_guid("ui_image/loadscreens/mp_rr_district_widescreen.rpak") == target_district
    assert string_to_guid("ui_image/loadscreens/mp_rr_colony_widescreen.rpak") == target_colony
    print("self-test OK")
