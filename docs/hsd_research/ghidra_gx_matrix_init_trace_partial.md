# MKGP2 GX texmtx slot 60 (GX_IDENTITY) 初期化経路 — Ghidra trace

date: 2026-05-11

## 結論サマリ (観察と仮説の分離)

### 観察 (確定)

1. **GX_IDENTITY (slot 60) は boot 時に 1 回だけ identity 行列で初期化される**
   経路: `BootDispatcher → FUN_802d4030 (hsd_init) → FUN_80263af8 (GXInit) → FUN_80264290 (GX state final setup)`
   ```
   FUN_80264290 @ 80264290:
     local_74..local_48 = identity 3x4 (1,0,0,0, 0,1,0,0, 0,0,1,0)
     FUN_8026a3d8(&local_74, 0x3c, 0)  // GXLoadTexMtxImm(identity, 60, MTX3x4) → XF[0xF0]
     FUN_8026a3d8(&local_74, 0x7d, 0)  // GXLoadTexMtxImm(identity, 125, MTX3x4) → XF[0x5F4] (PT_IDENTITY)
   ```
   `FUN_8026a3d8` は GXLoadTexMtxImm 等価:
   - param2 < 0x40 → XF address = param2*4 (matrix region 0x000..0x4FF)
   - param2 >= 0x40 → XF address = (param2-0x40)*4 + 0x500 (DTT/PT region)

2. **MObj.disp = `FUN_802c35dc`** (vtable[0x3c] of hsd_mobj class info at `&PTR_FUN_804fc1c0`)
   呼び出し chain: `HSD_DObj_Disp @ 802bd4c0 → mobj.vtbl[0x3c] @ 802c35dc → FUN_802bfd08 (TObj setup loop) → FUN_802be3c4 (TObjSetupMtx, per-TObj) + FUN_802be72c (TexCoordGen setup, per-TObj)`

3. **Default UV path で hardware に渡される TexCoordGen の入力 mtx は GX_IDENTITY (slot 60)**
   `FUN_802be72c` の最終 fallback ブランチ (uVar1 == 0、normal LIGHTMAP_DIFFUSE only):
   ```
   FUN_80266020(coord_id, 1, tobj.tex_gen_src, 0x3c, 0, tobj.mtxid)
   ```
   = `GXSetTexCoordGen2(coord, GX_TG_MTX2x4, src, mtx=0x3c=GX_IDENTITY, normalize=0=DISABLE, postmtx=mtxid)`
   `normalize=DISABLE` → postmtx は適用されない → **入力 mtx (slot 60) のみが UV 計算に使われる**。

4. **Slot 60 を上書きしうる per-frame パスは **見つからなかった****
   - `FUN_802be3c4` (TObjSetupMtx) は `FUN_8026a3d8(tobj.mtx, tobj.mtxid, ...)` を呼ぶが、`tobj.mtxid` は HSDLib 由来で `GX_PTTEXMTX0..7 (= 64..71)` に割り当てられ、slot 60 には行かない。
   - `FUN_802cc450` / `FUN_802cc6d0` / `FUN_802cca50` (SHAPE/ENVELOPE POBJ matrix loaders) は slot 0x1e..0x39 (TEXMTX0..9) を書くが、`FUN_802c02d0(idx)` の呼び出し index は 0..9 のみで slot 0x3c (60) には到達しない。
   - LObj 系 (`FUN_802c4800`)、`GX_BeginDraw` 経由の reset dispatch (804fbf50 table) も slot 60 を触らない。

5. **`FUN_802c0220` の存在で slot 60 が "TEX10 slot" として認識されていることが確認できる**
   ```
   case 0x3c: uVar1 = 10;  // slot 60 → texmtx index 10 (after TEXMTX0..9)
   ```
   このテーブルは TObj loader が slot 番号 → texmtx 配列インデックスへの逆引きに使うのみ。

### 仮説 (要検証)

A. **メイン仮説: slot 60 は GXInit 時に identity が 1 回 load されたあと、誰も上書きしないので、my_course でも vanilla course でも UV collapse は起こらないはず**
   → my_course の flat-color はおそらく **slot 60 問題ではない**。別の原因 (texmap 設定、TObj.flags、TEV stage 設定、または DObj/JObj の draw pass bit) を疑うべき。

B. **代替仮説: tobj.tex_gen_src の値 (4=TG_TEX0) が runtime で再解釈され、GXTexCoordGen に渡る `iVar2` が想定外の値になる可能性**
   `FUN_802bfd08` の TObj setup loop で `iVar2 = piVar8[2] = image.format`。これが想定外の値だと `FUN_80268014` (basic init) ではなく `FUN_80268260` (advanced LOD path) に流れる。
   しかし image.format は image bin format (RGBA8/CMP/RGB5A3 等の小さい値) で、TObj.tex_gen_src ではない。

C. **代替仮説: `tobj.mtxid` (runtime offset 0x14) が my_course の TObj だけ `60 (= GX_IDENTITY)` に設定されてしまっている**
   `FUN_802be3c4` で `FUN_8026a3d8(tobj.mtx, tobj.mtxid, 0)` が呼ばれると slot `mtxid` に書き込まれる。もし mtxid が 60 ならば、TObj の `tobj.mtx` (= `tobj.scale * tobj.rotation` の合成行列、通常 identity) が **slot 60 を上書きする**。
   現在 hsdraw の export では `tobj.scale = (1,1,1)`, `tobj.rot = (0,0,0)`, `tobj.translation = default(0,0,0)` を明示的に setter で書いているはずだから、結果の `tobj.mtx` は identity に近いはず。だが `set_scale(0,0,0)` のままだったコミットがあれば即 collapse する。

D. **代替仮説: my_course の TObj の `tobj.flags & 0xF` (= coord_type) が 0 以外の値になっており、`FUN_802be3c4` の bumpmap / shadow / reflect 経路に入って slot 60 とは別の slot を load**
   現在の code では `tobj.set_coord_type(0)` を呼んでいる (= UV)。 ただし `flags & 0xF` を runtime が読む場所と、hsdraw が `coord_type` として設定する場所が一致していない可能性は要 byte-diff 確認。

## 詳細トレース

### 関数アドレス table

| Function | Address | Role |
|---|---|---|
| `BootDispatcher` | 8002dd58 | Boot entry |
| `FUN_802d4030` (hsd_init) | 802d4030 | HSD library init, calls GXInit |
| `FUN_80263af8` (GXInit) | 80263af8 | Main GXInit; CP/XF state setup |
| `FUN_80264290` (GX final state) | 80264290 | **Loads identity → slot 60 + 125** |
| `FUN_8026a3d8` (GXLoadTexMtxImm) | 8026a3d8 | Matrix loader; `param2 < 0x40` → XF[param2*4] |
| `FUN_80266020` (GXSetTexCoordGen2) | 80266020 | TexCoord generation; XF[0x1040+coord], XF[0x1050+coord] |
| `HSD_DObj_Disp` | 802bd4c0 | DObj draw entry; calls mobj vtbl[0x3c]+vtbl[0x50] + per-pobj draw |
| `HSD_PObj_Disp` | 802ccce4 | POBJ draw; CULLBACK from POBJ.flags & 0xC000 + vtbl[0x40] dispatch |
| MObj vtbl[0x3c] (mobj.disp) | 802c35dc | Per-MObj setup: TObj setup + TEV setup |
| `FUN_802bfd08` (TObj setup loop) | 802bfd08 | Walks tobj list, calls TObjSetupMtx + GX_InitTexObj |
| `FUN_802be3c4` (TObjSetupMtx) | 802be3c4 | **Loads `tobj.mtx` to slot `tobj.mtxid` via FUN_8026a3d8** |
| `FUN_802be72c` (setupTextureCoordGen) | 802be72c | **Calls GXSetTexCoordGen2 with mtx=0x3c (slot 60)** |
| `FUN_80268014` (GX_InitTexObj basic) | 80268014 | TObj struct init in memory (no FIFO) |
| `FUN_80268260` (GX_InitTexObjLOD) | 80268260 | TObj init with mip / LOD chain |
| `FUN_802685a8` (GXLoadTexObj) | 802685a8 | Final TObj install to texmap |
| `FUN_802cc450` (POBJ shape mtx) | 802cc450 | Loads slot 0x1e (TEXMTX0) for shape mode |
| `FUN_802cc6d0` (POBJ envelope mtx) | 802cc6d0 | Loads slot per `FUN_802c02d0(envelope_idx)`, idx 0..9 only |
| `FUN_802cca50` (POBJ misc mtx) | 802cca50 | Loads slot 0x1e/0x21 for shape/edge cases |
| `FUN_802c02d0` (texmtx slot lookup) | 802c02d0 | idx 10 → 0x3c (slot 60). **idx 10 は現状コードパス上 reach しない** |
| `FUN_802c0220` (texmtx index lookup) | 802c0220 | slot 0x3c → 10 (used by TObj loader inverse mapping) |
| `Scene3D_Init` | 80030bdc | Per-scene: 3D scene archive load |
| `Scene3D_SetupProjection` | 8003031c | Per-scene: COBJ projection setup (PNMTX 0 only, not texmtx) |
| `RaceScene_Init` | 800a1d80 | Per-race: Course/CarObject/Lakitu/HUD setup |
| `RaceScene_RenderPass` | 800a0be8 | Per-frame: scene draw + culling + HUD |
| `CourseScene_Load` | 800476d4 | Per-race: course archive + collision + checkpoints |

### Vtable (hsd_mobj class info @ `0x804fc1c0`)

```
0x00: parent class ptr = 802c3a74 (FUN_802c3a74 = hsd_mobj_ClassInit)
0x04: refs = 1
0x08: parent class name = "sysdolphin_base_library"
0x0C: own name = "hsd_mobj"
0x10: head info size = 0x20
0x12: head obj size  = 0x54
0x14: parent class ptr (recurse via HSD_ClassInit copy)
0x18..0x27: zeros / list link / asserts
0x28..: methods (memmove'd from parent)
0x30: FUN_802c3974
0x38: FUN_802c3a10
0x3c: FUN_802c35dc  ← MObj.disp
0x40: FUN_802c2890
0x44: FUN_802c2a40
0x48: FUN_802c3534
0x4c: LAB_802c23ac
0x50: FUN_802c3730  ← MObj.cleanup
0x54-0x60: more methods
```

### TObj runtime layout (推定)

hsdraw の on-disk TObj は 0x5C bytes、offset:
- 0x00 class
- 0x04 next
- 0x08 tex_map_id
- 0x0C tex_gen_src
- 0x10..0x33 RST (rotation/scale/translation)
- 0x34 wrap_s, 0x38 wrap_t
- 0x3C repeat_s (u8), 0x3D repeat_t (u8)
- 0x40 flags (coord_type | color_op | alpha_op)
- 0x44 blending
- 0x48 mag_filter
- 0x4C image_data ref
- 0x50 tlut ref
- 0x54 lod ref
- 0x58 tev ref

Runtime in-memory (decompile より逆算):
- `param_1[5]` = offset 0x14 = **tobj.mtxid** (runtime 割り当て)
- `param_1[0x13]` = offset 0x4C = **tobj.flags** (= 0x4C is image_data ref on disk; runtime overwrites?)
- `param_1[0x1d]` = offset 0x74 = **tobj.mtx[3][4]** start (runtime computed)

これは重要な不一致: **disk layout (hsdraw) と runtime layout (game) は別物**。runtime は load 時に `loadFromDesc` 的に再パックする。

つまり hsdraw でいう disk の `tobj.flags` (offset 0x40) は runtime では **別のフィールド**になる。runtime の `param_1[0x13]` (= byte offset 0x4C) を game が tobj.flags として使っているなら、disk 0x4C の image_data ref とは **別のフィールド**である。

→ HSDLib HSD_TOBJ vs sysdolphin H_TOBJ の **フィールドオフセット差異**を調査する必要あり (= 次のタスク)。

## 次に試すべき手 (調査ループ handoff)

1. **`MR_highway_short_A_inu_aliased.dat` (動く modded course) と `my_course.dat` (動かない) の TObj 全件を hex-diff し、特に offset 0x14 (runtime mtxid?) と 0x4C/0x40 (flags) の差を確認**
   `tools/hsd/hsd_dump.csx` か独自の Python で TObj struct の field 全件 dump → diff。

2. **Dolphin FifoLog (PixelLog) を two-frame 取得し、`GXLoadTexMtxImm(addr=0xF0)` の発火回数を vanilla cup vs my_course で比較**
   - vanilla cup (例: cup 0 MR_highway): GXInit 直後に 1 回、その後 0 回?
   - my_course: GXInit 後、追加で何回か発火しているなら slot 60 上書きの確証
   - 既に作ってあるはず: `tools/hsd/dff_parse.py` 系

3. **runtime の TObj 構造体 (sysdolphin H_TOBJ) の field offset を Ghidra で確定**
   - `HSD_JObjLoadDesc @ 802cfedc` 経由の TObj 構築 path を追って、disk `HSD_TObjDesc` → in-memory `HSD_TObj` の field 対応を struct 化。
   - 特に runtime `param_1[5]` (offset 0x14) と `param_1[0x13]` (offset 0x4C) が disk のどのフィールドから来ているのかを追跡。

4. **hsdraw 側で `tobj.mtxid` フィールドを on-disk struct に追加するか、`tobj.coord_type` の正しい on-disk offset を確認**
   - disk の TObj は 0x5C bytes だが、もし runtime mtxid (offset 0x14) が disk の何かを overwrite するなら export 時に 60 と被らないようにする必要がある。
   - 現状 hsdraw の `tobj.scale/rot/translation` setter は 0x10..0x30 を書くため、もし disk offset 0x14 が runtime mtxid にコピーされる field ならば、`tobj.set_rotation(0, ry, 0)` で `ry` を書いた値が runtime で mtxid として解釈され、**ry の整数部 % 256 == 60 のとき slot 60 が overwrite される**。
   - 現コード `tobj.set_rotation(0.0, 0.0, 0.0)` なので OK のはずだが、scale や translation の設定値が runtime offset 0x14 にコピーされていないことを確認すべき。

5. **`alloc_unlit_color` でなく `MObj.alloc()` に切り替えた変更 (commit 4cbaaf0) で TEV stage の数や順序が変わった可能性**
   ↓ FUN_802c2a40 (`mobj.disp_tev_setup`) の TEV stage 構築は MObj.tev_table を読む。my_course の MObj が `tev_table = NULL` なら default 4-stage TEV pipeline、`tev_table != NULL` なら custom。custom TEV で texgen 出力を読まない stage 構成になっていれば flat color になる。
   → my_course の MObj.tev_table を hsdraw 上で確認。NULL であることを期待。

## 観察とギャップ

- 「slot 60 を identity で初期化する関数を 1 つ以上特定」 → 達成 (`FUN_80264290 @ 80264290`、boot のみ)
- 「HSD scene init 経路で my_course にだけ呼ばれていない関数を特定」 → **未達成**。CourseScene_Load / RaceScene_Init を読んだが、my_course と vanilla で違う code path に分岐する箇所は見つからず。両者とも CourseData_GetOrCreate → 同じ Archive/JObj load path に入る。差は **course .dat の中身** だけ。
- 「TEX0_MTX[60] を別の値で上書きする経路」 → **見つからず**。slot 60 は GXInit 後に変更されないように見える。

## 補足: メイン仮説の再評価

ユーザーの当初の仮説 「TEX0_MTX[60] zero matrix で UV collapse」 は、本 trace の限りでは **支持できない**:

1. Slot 60 は boot 時に identity に初期化される (FUN_80264290)
2. その後、slot 60 を上書きする経路が見つからない (TObj.mtxid は 64..71、SHAPE/ENVELOPE は 30..57)
3. Default UV path (`tobj.tex_gen_src=TG_TEX0`) は GXSetTexCoordGen2(MTX2x4, src=TEX0, mtx=GX_IDENTITY, normalize=DISABLE, postmtx=mtxid) となる。`normalize=DISABLE` で postmtx 無効、入力 mtx (slot 60) のみが効く。slot 60 が identity なら UV は per-vertex TEX0 そのまま通る。

**flat color の真の原因は別の場所**にある可能性が高い:
- TObj の **disk vs runtime field offset 不整合** で `tobj.flags & 0xF` が想定外の値になり別 path に流れる
- TEV stage 設定 (mobj.render_flags=0x2011 + mobj.tev_table=NULL) で texgen 出力が TEV register に届かない
- POBJ display list の TEX0 attribute 形式不一致 (per-vertex u8/u16/f32 の取り違え)

これらの確認は前述の「次に試すべき手」1, 4, 5 で進める。

---

## 補足観察 (2026-05-11、part2 開始前): sysdolphin C source 断片 (LiveTObj.cs より)

`~/src/github.com/Ploaj/HSDLib/HSDRawViewer/Rendering/Models/LiveTObj.cs:49-79` の `MakeMatrix()` に、sysdolphin オリジナル C source の comment が残っている:

```c
// trans
trans.x = -tobj->translate.x;
trans.y = -(tobj->translate.y + (tobj->wrap_t == GX_MIRROR
                                 ? 1.0F / ((f32)tobj->repeat_t / tobj->scale.y)
                                 : 0.0F));
trans.z = tobj->translate.z;

// rot
rot.x =  tobj->rotate.x;
rot.y =  tobj->rotate.y;
rot.z = -tobj->rotate.z;

// scale ← 重要
scale.x = fabsf(tobj->scale.x) < FLT_EPSILON ? 0.0F : (f32)tobj->repeat_s / tobj->scale.x;
scale.y = fabsf(tobj->scale.y) < FLT_EPSILON ? 0.0F : (f32)tobj->repeat_t / tobj->scale.y;
scale.z = tobj->scale.z;

// matrix = trans * rot * scale
```

つまり TObj.transform matrix の **scale 成分は `repeat_s/t / scale.x/y` で計算される**:

| `repeat_s` | `scale.x` | `matrix.scale.x` | 結果の UV 変換 |
|---|---|---|---|
| 0 | 1.0  | 0.0    | **U 全 collapse → texel(0,0) flat** |
| 1 | 1.0  | 1.0    | identity (UV 通過) |
| 2 | 1.0  | 2.0    | UV doubled (texture 2x repeat) |
| 1 | 0.5  | 2.0    | UV doubled (different way) |
| 1 | 0    | 0.0 (else 分岐 `< FLT_EPSILON`) | **U 全 collapse** |

これは fix 後の挙動と一致する: hsdraw default `repeat_s=0` + 我々が明示 `set_scale(1,1,1)` で `scale.x=1` → matrix.scale.x = 0/1 = 0 → all U → 0。`repeat_s=1` に変更すると 1/1 = 1 → identity transform → UV 通過。

ただし: **この transform matrix が GX hardware の TEX0_MTX[0] に load されるのか、別の slot (60 = GX_IDENTITY) との関係はどうなるか** は別問題。fifolog の slot 0 vs slot 60 切り替えは `FUN_802be72c` 内の condition 分岐 (= part2 agent の主課題) で起こっているはず。

→ Part2 agent はこの C source を踏まえて、`FUN_802be72c` (= setupTextureCoordGen) と `FUN_802be3c4` (= TObjSetupMtx) の両方で `repeat_s/t` がどう使われているかを精査すべき。特に `tobj.scale.x = 0` のとき (= hsdraw default) の挙動と、`repeat_s = 0 / scale = 1` の挙動が **同じ** "scale=0" matrix に collapse することの意味。
