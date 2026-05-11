# meadow_loop course (cup 17 round 4) 追加 todo

> 要件: 8 の字 / 草原テーマ / 明確な勾配 / 30〜45 秒 lap / 周長 3000-4000 unit

## 設計

- **形状**: 8 の字 (中央交差) — 平面 cross + 各 loop で勾配 (heightfield 制約遵守、立体交差はせず spline crossing 1 枚)
- **ジオメトリ** (HSD world 座標):
  - 中央 crossing: (0, 0, 0)
  - 北 loop: 中心 (0, 0, -350)、半径 350、北側で hill (Y up to 80)
  - 南 loop: 中心 (0, 0, +350)、半径 350、南側で valley (Y down to -40)
  - road 幅: 280 unit
  - 周長: 2 × 2π × 350 ≈ 4400 unit (3 lap で 13200 unit、150cc ~250 unit/sec で ~50 sec/lap → 仕様内)
- **テーマ**: 緑芝 infield + 茶色オフロード + 木 (cone-low-poly) 6 個 + 青空 skydome + 赤白 curb
- **lap path** (variant 6, 64 wp): 北 loop 32 wp → 中央交差 → 南 loop 32 wp → 中央交差で 1 周
- **AI lines** (variant 0..5): 同じ形状を radial offset 6 本
- **start_positions**: 中央交差から南方向手前 ~ Z=400-700 に 8 体並べ、finish_line を Z=300 (中央交差南側)
- **finish_line**: [[ -150, 0, 300 ], [ 150, 0, 300 ]]、wp 0 を XZ で横切る

## 実装手順

- [x] 設計検討 (この doc)
- [ ] features/course_models.yaml に `meadow_loop` entry 追加
- [ ] features/cups.yaml に round 4 entry 追加 (id=round4, course_model=meadow_loop)
- [ ] thumb 画像 placeholder 2 枚 (test_cup_course4_thumb.png 128x128, test_cup_course4_thumb_road.png 128x160) を programmatic に生成
- [ ] Blender 構築 script 書く + execute (`tools/blender/_build_meadow_loop.py`):
  - vis:meadow_loop collection: road / infield / outfield / wall_outer / centerline / scenery (trees/skydome)
  - MKGP2_Course/meadow_loop collection: CollisionMesh / WallSegments / line root + 7 variants / Auto F + R
- [ ] ExportHSD operator (vis:) → meadow_loop.dat
- [ ] Full Course Export operator → meadow_loop.bin / meadow_loop_line.bin / meadow_loop_Auto.bin / Auto_R.bin
- [ ] bash build.sh
- [ ] in-game (cup 17 round 4) 確認 — user 待ち
- [ ] commit + push

## 注意 (skill / memory から拾った)

- vis: の filter は MESH/FONT/CURVE/SURFACE/META に拡張済 (commit 6526dbc)
- collision は heightfield (memory: project_collision_heightmap_obstacle_recipe.md) — 同一 (X,Z) で複数 Y は不可、立体交差は disable
- finish_line を start_positions が跨ぐと順位 cap (memory: project_position_cap_finish_line_straddle.md) — 全 8 体を同じ側に
- line.bin variant 6 = lap path 固定 slot 6 (memory: project_line_bin_lap_path_fixed_slot6.md)
- POBJ + JObj + MObj + texture の 5 要素セット (skill 「点滅が消えた構成」) を維持
- BSDF Base Color → 4×4 RGBA8 自動 fallback (`_make_textured_mobj`)
- Blender Z-up → MKGP2 Y-up 変換: `(x, z, -y)` (`_blender_to_hsd`)
