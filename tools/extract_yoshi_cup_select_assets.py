#!/usr/bin/env python3
"""Extract cup-select banner crops to PNG (Yoshi name_top / name_bot 候補).

extract_yoshi_round_assets.py と同じ枠組みで、cup-select page で使われる
2 atlas の crop を出力する:

  CUPsel01.tpl  (RGB gk 0x0443 / alpha 0x0444)  → 0x1758..0x175A の 3 stripe
                 banner top (cup category 小ラベル, 151×29)
  CUPname.tpl   (RGB gk 0x0441 / alpha 0x0442)  → 0x1729..0x172F の 7 stripe
                 banner bot (cup name, 256×46)

vanilla の DAT_8049ade4 [cursor*6] short[0]/short[1] が動的に
書き込まれる経路なので、Yoshi 専用 ID は scene 起動中の memory dump を
取らないと確定しない。当面 3 + 7 = 10 候補すべてを書き出して、目視で
test_cup の placeholder を選ぶ運用にする。
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from extract_yoshi_round_assets import (  # noqa: E402
    load_tpl_rgba, crop_rgba, compose_alpha,
)
from tpl_dump import write_png  # noqa: E402

OUT_DIR = r"C:\Users\naari\Downloads\yoshi_cup_assets"
os.makedirs(OUT_DIR, exist_ok=True)

# (label, rgb_id, rgb_group, offset_x, offset_y, size_x, size_y, alpha_group)
# resource_table.txt @ ResourceEntry table 0x80422208 由来。
ASSETS = [
    # --- Banner top (CUPsel01 atlas, 151×29 ribbon) -----------------------
    # 0x1758..0x175A の 3 stripe を縦に並べた layout:
    #   (452, 375), (452, 404), (452, 433) — 高さ 29 の repeat
    ("cup_name_top_1758",  0x1758, 0x0443, 452.0, 375.0, 151.0, 29.0, 0x0444),
    ("cup_name_top_1759",  0x1759, 0x0443, 452.0, 404.0, 151.0, 29.0, 0x0444),
    ("cup_name_top_175A",  0x175A, 0x0443, 452.0, 433.0, 151.0, 29.0, 0x0444),
    # --- Banner bot (CUPname atlas, 256×46 cup name strip) ----------------
    # 0x1729..0x172F の 7 stripe:
    ("cup_name_bot_1729",  0x1729, 0x0441,   0.0,   0.0, 256.0, 46.0, 0x0442),
    ("cup_name_bot_172A",  0x172A, 0x0441, 256.0,   0.0, 256.0, 46.0, 0x0442),
    ("cup_name_bot_172B",  0x172B, 0x0441,   0.0,  46.0, 256.0, 46.0, 0x0442),
    ("cup_name_bot_172C",  0x172C, 0x0441, 256.0,  46.0, 256.0, 46.0, 0x0442),
    ("cup_name_bot_172D",  0x172D, 0x0441,   0.0,  92.0, 256.0, 46.0, 0x0442),
    ("cup_name_bot_172E",  0x172E, 0x0441, 256.0,  92.0, 256.0, 46.0, 0x0442),
    ("cup_name_bot_172F",  0x172F, 0x0441,   0.0, 138.0, 256.0, 46.0, 0x0442),
]


def main():
    for label, rid, rgb_gk, ox, oy, sx, sy, alpha_gk in ASSETS:
        try:
            w_src, h_src, rgba_rgb, name_rgb = load_tpl_rgba(rgb_gk)
            w_alp, h_alp, rgba_alp, name_alp = load_tpl_rgba(alpha_gk)
        except Exception as e:
            print(f"[{label}] LOAD FAIL: {e}")
            continue

        ox_i, oy_i, sx_i, sy_i = int(ox), int(oy), int(sx), int(sy)
        crop_rgb = crop_rgba(rgba_rgb, w_src, h_src, ox_i, oy_i, sx_i, sy_i)
        # CUPsel01.tpl と CUPsel01_a.tpl は同名 atlas ペアでも内部 layout が
        # 異なる場合がある (CUPsel01_a の 0x175D 起点は 401x125 など別座標系)。
        # まず素朴に同座標で crop してみて、結果が壊れていれば alpha 側だけ
        # 別 offset 表で対応する。
        crop_alp = crop_rgba(rgba_alp, w_alp, h_alp, ox_i, oy_i, sx_i, sy_i)
        merged = compose_alpha(crop_rgb, crop_alp, sx_i, sy_i)

        out_path = os.path.join(OUT_DIR, f"{label}.png")
        write_png(out_path, sx_i, sy_i, merged)
        print(f"[{label}] wrote {out_path} ({sx_i}x{sy_i})")
        print(f"    rgb   : id=0x{rid:04X} gk=0x{rgb_gk:04X}  {name_rgb} ({w_src}x{h_src})")
        print(f"    alpha : gk=0x{alpha_gk:04X} {name_alp} ({w_alp}x{h_alp})")
        print(f"    crop  : ({ox_i},{oy_i}) size {sx_i}x{sy_i}")


if __name__ == "__main__":
    main()
