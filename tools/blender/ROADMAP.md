# Blender Course Tools — Roadmap

現状 (`v0.1.0`) と、次に何を積むかの整理。実装着手前に書き留めるためのドキュメント。

## 現状 (v0.1.1, 2026-05-08)

| 機能 | 状態 |
|---|---|
| 旧 v0.1.0 の機能群 | OK |
| Custom course 1 collection 1 round flow (`scene.mkgp2_new_course` / `scene.mkgp2_import_course` / `scene.mkgp2_export_course`) | OK (Phase B) |
| `mkgp2_import_course` の HSD slot (任意の `scene.json` を course collection 内に nest) | OK (Phase B 追加) |
| course collection の `mkgp2_hsd_dat` custom prop (元 .dat 名を保存) | OK |

= **Custom course は 1 .dat (HSD road) + 4 .bin に集約**。HSD は `tools/hsd/hsd_export_for_blender.csx` で .dat → scene.json に展開した bundle を addon が任意で取り込む。export 側はまだ collision/line/auto のみ (HSD は read-only)。

## test_course の dev 残骸 (調査ログ, 2026-05-08)

`features/cup_page3/files/` の test_course は ISO に 7 つ .dat があるが、実際 main.dol が runtime にロードするのは **`test_course_road.dat` 1 個のみ**。

| ファイル | runtime 状態 |
|---|---|
| `test_course_road.dat` | ロード対象。`GetCourseModelFilename` (0x8009c418) → `PTR_s_test_course_road_dat_8040b940` テーブル経由 |
| `test_course_f_ship.dat`, `test_course_start_gate.dat`, `test_course_coconut.dat` | .rodata に文字列+joint 名は残る (`0x8031cd94..0x8031ce1c`) が、`courseObjectTable` の cup0 slot (0x8040b960) が **全 NULL** で読まれない (course-object 残骸) |
| `test_course_ground.dat`, `test_course_bm.dat`, `test_course_obj.dat` | main.dol に名前文字列すら無し (完全な ISO 残骸) |

加えて `.rodata` 内に `test_course_ph_pos_a/b/c_joint`, `test_course_p_flower_joint`, `test_course_kinopio_byebye/jump/sit_joint`, `test_course_poihana_b/r_joint` が並ぶが、対応する .dat 名は無し — MR_highway の course-object set のコピーで、test_course 開発時に **MR の course-object .dat を joint 名解決経由で借用する設計** だったが未完成のまま放棄された痕跡。

→ **結論**: vanilla mkgp2 でもカスタムコースでも、addon は **1 .dat = 1 round の単純合成** で十分。multi-.dat schema (`course_models.yaml` の `joints: []` 拡張など) は phantom problem だったのでスキップ。Phase B で `hsd_path` を 1 つ受け付けるだけで完結。

## 現状 (v0.1.0, 2026-05-07)

| 機能 | 状態 |
|---|---|
| HSD scene.json import (Emission shader + vertex color α pre-mul + ColorOp chain) | OK |
| Collision .bin import / export (3D triangles + 2D wall edges) | OK |
| `_line.bin` import / export (variant 0..N) | OK |
| `_Auto.bin` import / export | OK |
| Full Course unified import (HSD + col + line + auto を 1 click) | OK |
| File > Import / Export menu integration | OK |
| View3D Sidebar `MKGP2` tab | OK |
| `Reload course modules` (hot reload) | OK |
| Junction install (`%APPDATA%\Blender Foundation\Blender\4.3\scripts\addons\mkgp2_course`) | OK |

= **read-only L1 視覚参照** + **collision/line/auto 双方向**。HSD だけ書き戻し未対応。

---

## 検討軸

- **Audience**: 自分専用に最適化するか、配布 (Blender Extensions Platform / GitHub release zip) も視野に入れるか
- **Depth**: L1 (見るだけ) → L2 (編集して書き戻し) → L3 (新規コースをゼロから生成) のどこまで踏み込むか
- **Bandwidth**: 小機能の積み重ね vs HSD export のような大物に取り組むか

---

## Tier 1: 近接タスク (~1 day〜1 week 各)

### T1a — Validation operators
編集ミスを fail-fast で潰すための内部チェック。ほぼ既存コード再利用。

- **Line round-trip operator**: 既存 `_line_roundtrip_test.py` を operator 化。export 直後に「parse → write → byte-identical 比較」を内部で走らせ、失敗したらレポート
- **Collision integrity check**: `CollisionMesh` の triangle が grid AABB を逸脱してないか、`WallSegments` が Y=0 を保ってるかを scan
- **Auto path waypoint count check**: terminator count と record 数の整合
- **Naming convention check**: `LineVariant_<i>_<stem>` / `Auto_<stem>` 等の固定命名を warning 化

実装コスト: 半日〜1 日。ユーザー体験は地味だが「export 後に Dolphin で初めて壊れに気付く」frustration を減らせる。

### T1b — Visualization QoL
毎日触る部分なので投資効果高。

- **Line variant visibility panel**: v0..v6 を一括 hide/show、現在 visible な variant をハイライト
- **Auto path direction arrows**: edge を矢印描画 (geometry nodes or custom draw)、進行方向確認
- **Waypoint number overlay**: index を 3D Viewport overlay (View3D の draw handler)
- **Collision material color**: triangle の `material_id` を `vertex color` 属性として書き、Solid view で色分け
- **Course origin marker**: Game (0,0,0) に gizmo / empty を置く operator
- **Outliner naming**: 現在は `mkgp2:MR_highway_short_A` collection が深い、トップ階層を `MKGP2 Course / mr_highway_short / {hsd, collision, lines, auto}` の 2 階層 collection 構造に再編

実装コスト: 1 機能あたり 1-3 時間、累計 1 週間で QoL 一気に上がる。

### T1c — Distribution & install ergonomics
他人に配るなら必須、自分専用でも install 手順が楽になる。

- **`tools/blender/build_addon_zip.py`**: `blender_addon_mkgp2_course/` + source 8 module を zip にバンドル。source bundle option を有効にすると addon 単独で動作する (= mkgp2-patch を clone しなくても install 可能)
- **Blender 4.2+ Extension format 対応**: `bl_info` を `__init__.py` 冒頭にした古い形式と、4.2 以降の `manifest.toml` 形式の両対応
- **Install script**: `tools/blender/install_addon.ps1` — Junction を自動作成 (今手動でやったコマンドを script 化)
- **Uninstall script**: 同上を逆にする helper

実装コスト: 1 日。配布は別途リリース手順 (CHANGELOG.md, tag, GitHub Releases) が要る。

### T1d — yaml-driven course routing
`features/cups.yaml` に course path / file 名対応を書いてあるので、addon から「現在編集中のコースを正しい location に export」できるようにする。

- yaml を Blender 側で読んで、コース名 dropdown を panel に追加
- export 時に dropdown 選択 → 正しい file 名で `<dolphin_dump>/files/...` に書く
- `<dolphin_dump>` の path は addon preference に追加

実装コスト: 半日。`cups.yaml` の現在のスキーマを確認後 path 自動補完が組める。

---

## Tier 2: 中期タスク (~1〜2 week 各)

### T2a — Material Tier 2 round-trip (= "r")
現状 import は Tier 1 (texture + vertex color + alpha + ColorOp + Blending) のみ。Tier 2 を追加すると視覚 fidelity 向上 + 将来の HSD export の足場。

- `PEDesc` (alpha test threshold, Z-write enable, blend mode src/dst)
- `AmbientColor`
- `ShadowOffset` (= z-bias)
- 各 stage の TEV register settings (深掘り option)

csx 側 (`hsd_export_for_blender.csx`) に DTO 追加 + Blender 側で material custom properties に格納。HSD export を実装する時に同じ DTO を逆向きに使う。

実装コスト: 2-3 日 (HSDRawViewer ソース + GLSL を再追跡)。

### T2b — Course skeleton wizard
新規コースをゼロから作るための minimum bootstrap。

operator 1 つで:
1. blank `.dat` (single root JObj + 1 quad mesh + 1 default texture) を生成
2. `<prefix>_short.bin` / `_long.bin` (空の collision、grid 1×1 + 1 ground triangle)
3. `<prefix>_short_line.bin` / `_long_line.bin` (REPLICATE_TO_N=7 で 1 mesh から 7 variant)
4. `<prefix>_short_Auto.bin` / `_long_Auto.bin` / `_R.bin` 4 ファイル
5. Riivolution xml に course entry を追加するパッチを生成

実装後: Blender 内で track 形を modeling → operator 押し → Dolphin で「test_course」が動く。

実装コスト: 1-2 週間。`mkgp2_new_course_from_scratch.md` の §6.7-6.10 gap (TA bin / course objects / WeatherSystem / b_line) を fill する作業を含む。

### T2c — Coordinate root system
import 時に共通の "course root" empty を作って全 asset の親に置く。export で root transform を逆適用すれば、Blender 内でコース全体を回転 / 移動 / scale して試せる。

- `MKGP2_Course_Root` empty を Full Course import で生成
- 全 sub-object の parent に
- export 時に root transform を逆適用、root が identity でなければ警告

実装コスト: 半日。地味だが debugging の自由度大幅 up。

---

## Tier 3: 野心タスク (~3〜6 week 各)

### T3a — HSD export (Blender → .dat)
最大物。read-only から write-back へ。

- Blender mesh → POBJ (vertex/index encode + DL pack)
- material custom props → MObj/TObj reconstruct
- texture image → GX texture encode (CMP/RGB5A3/I8 等)
- joint hierarchy → JObj tree
- `HSD_JOBJDesc` rebuild + scene_data write
- HSDLib API は C# なので csx 側で実装、Blender が JSON 中間表現で渡す形

リスク: GX DL pack の細部 (matrix index、vertex format selection) はソースが少なく、HSDRawViewer の `HSD_DOBJ` writer を読み解く必要あり。

実装コスト: 3-4 週間。優先度は cup 追加だけなら不要 (HSDRawViewer で可)、新規コース完全自動化なら必要。

### T3b — Texture replace operator
Blender で .dat の texture image を画面で操作 (color tweak, replace) → 書き戻し。

- 既存 .dat の TObj リストを Blender に表示
- Image を Blender editor で diff/edit
- 書き戻しは csx (HSDLib) で TObj.ImageData を encode 上書き

実装コスト: 1 週間 (T3a の subset)。

### T3c — Inline alias root manager
`hsd_add_alias_root.csx` を Blender operator 化。既存 .dat の root list を表示して、選択 JObj を public symbol で alias 追加するボタン。

- 現在 csx から手動実行 → Blender 内 GUI 化
- 「test_cup_inu モデルを `MR_highway_inu_joint` で alias 公開」ワンクリック

実装コスト: 3-4 日。

---

## 推奨優先順序

短期決着しやすい順:

1. **T1a (validation)** — 半日、即効性高
2. **T1b (visualization QoL)** — 1 週間、毎日恩恵
3. **T1c (distribution)** — 1 日、配布視野なら必須
4. **T1d (yaml routing)** — 半日、export 摩擦↓

中期で 1 つ選ぶなら:

- 「**新規コースを作りたい**」が動機 → **T2b (skeleton wizard)** ※ ただし T3a HSD export まで踏み込まないと完全自動化は無理
- 「**既存コース改造の精度上げたい**」 → **T2a (material Tier 2)**
- 「**Blender 内で実験しやすくしたい**」 → **T2c (course root)**

野心系:

- **T3a (HSD export)** は単発で意味薄、T2b と組み合わせて初めて「ゼロからコース」が成立。先に T2 を片付けてから着手するのが筋

---

## 当面の判断ポイント

- (a) 先に **distribution + validation 整備** (T1a + T1c) で v0.2.0 を切るか
- (b) 動機ドリブンで **T2b skeleton wizard** に直行するか (= 新規 course を作る決意があるなら)
- (c) 視覚 fidelity を上げる **T2a** に行くか
- (d) **dm_stadium 11 variant の dead 部分の真意** を line.bin 側で再調査 (Ghidra で v7..v10 に xref する関数を探す) を寄り道で挟むか

(c) は Tier 2 中の最小スコープなので、ここで挟むのもあり。
