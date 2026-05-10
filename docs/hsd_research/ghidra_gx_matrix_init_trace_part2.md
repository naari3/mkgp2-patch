# MKGP2 GX texgen 経路解析 — Part 2: `tobj.repeat_s/t = 1` fix の機序

date: 2026-05-11
relates to: `ghidra_gx_matrix_init_trace_partial.md` (Part 1)

## 結論サマリ (観察と仮説の分離)

### 観察 (Ghidra decompile + vanilla .dat dump で確証あり)

1. **runtime TObj は disk より大きい (0xAC vs 0x5C bytes)** — `TObj_ClassInit @ 0x802c0894` が `HSD_ClassInit("hsd_tobj", head_info_size=0x4c, head_obj_size=0xac)` を呼ぶ。Ghidra 上では struct `HSD_TObj_Runtime_Partial` (`/HSD/`) として登録済み。

2. **disk → runtime の field copy は `TObj_LoadFromDesc @ 0x802bdec8` (vtbl[0x40])** が行う。重要な事実:
   - 全 disk RST/wrap/repeat/flags フィールドが runtime の対応 offset へコピーされる
   - **`runtime.mtxid (+0x14) は無条件で 0x3c (= GX_PTIDENTITY) にハードコード**される (disk から来ない)
   - **`runtime.flags (+0x4c) には `0x80000000` (= MTX_DIRTY) が必ず OR される**
   - disk-runtime offset 対応の完全な表は `TObj_LoadFromDesc` の plate コメント参照

3. **`TObj_RebuildTransformMtx @ 0x802be1f0` (vtbl[0x3c]) で UV 行列が再構築される**
   呼び出されるのは `TObj_LoadTexMtxList` 内で MTX_DIRTY を見たとき (lazy)。式:

   ```
   if |SX| >= 1e-10:  sx = (double)repeat_s / SX
   else            :  sx = FLOAT_806dcb88 = 0.0   ← collapse case A
   同 for sy / repeat_t / SY.
   sz = (double)TZ.

   mtx = Scale(sx, sy, sz)
       * RotEulerXYZ(RX, RY, -RZ)
       * Trans(-TX, -(TY + wrap_t_adjust), TZ)
   ```

   `wrap_t_adjust` は `wrap_t == GX_MIRROR (=2)` のとき `1.0 / (repeat_t / SY)` を加算する補正項。

4. **`TObj_LoadTexMtxList @ 0x802be3c4` が UV 行列を毎 draw で TexMtx slot にロードする**
   - MTX_DIRTY が立っていたら `TObj_RebuildTransformMtx` を呼んで再構築 → flag clear
   - `coord_type = (flags & 0xF)` で分岐:
     - `4` (TOON): スキップ (matrix load なし)
     - `2` (HILIGHT): camera basis から hilight 行列を合成して slot=mtxid (=0x3c) にロード
     - `1` (REFLECTION): tobj.mtx を 0.5/-0.5 NDC remap した行列を slot=mtxid にロード
     - `3` (SHADOW): camera mtx と tobj.mtx を合成して slot=mtxid にロード
     - `0` (UV) / `5` (GRADATION): tobj.mtx を **そのまま slot=mtxid にロード** ← 通常パス
   - **slot=mtxid は常に 0x3c なので、GX_IDENTITY スロット (boot 時に identity 設定) が毎 draw で TObj 固有行列に上書きされる**

5. **`TObj_SetupTexCoordGenList @ 0x802be72c` が GXSetTexCoordGen2 を発行する**
   全 branch table:

   | flag bits | coord_type | GXSetTexCoordGen2 (coord, type, src, mtx, normalize, postmtx) | TexMtx source slot |
   |---|---|---|---|---|
   | NORMAL | 0 (UV) **or** 5 (GRADATION) | `(coord, MTX3x4=1, src=tobj.tex_gen_src, 0x3c, 0=DISABLE, mtxid=0x3c)` | **slot 60 (= the per-TObj UV mtx loaded in step 4)** |
   | NORMAL | 1 (REFL) / 2 (HILIGHT) | `(coord, MTX2x4=0, src=NRM=1, 0x1e=TEXMTX0, 1=ENABLE, mtxid=0x3c)` | TEXMTX0 (loaded by step 4 REFL/HILIGHT branch) |
   | NORMAL | 3 (SHADOW) | `(coord, MTX2x4=0, src=POS=0, 0=PNMTX0, 0=DISABLE, mtxid=0x3c)` | PNMTX0 (≠ TexMtx; uses position matrix directly) |
   | NORMAL | 4 (TOON) | `(coord, type=10=SRTG, src=tobj.tex_gen_src, 0x3c, 0, 0x7d=PT_IDENTITY)` | (special SRTG color path) |
   | NORMAL | (special: coord_id == DAT_806d0c90 = 0xFF) | `GXLoadTexMtxImm(&DAT_806ccf80, 0x21, 0); SetTexCoordGen2(coord, 0, 0, 0x21, 0, 0x7d)` | TEXMTX1 (special) |
   | BUMP (bit 24 set) | 同上 + **2 つ目の coord (coord+1)** が `(coord+1, BUMP_type, FUN_802c0078(coord), 0x3c, 0, 0x7d)` で発行される | |

### 観察 (vanilla course .dat の TObj field 確認)

`tools/hsd/dump_tobj_all.csx` で `MR_highway_short_A.dat` の TObj 群を dump:

```
[J0 D0 T0] flags=0x00030010 coord=UV bump=False lights=DIFFUSE, colorOp=3 alphaOp=0
    repeat_s=1 repeat_t=1 wrap_s=REPEAT wrap_t=REPEAT tex_gen_src=GX_TG_TEX0 mag=GX_LINEAR
    R=(0,0,0) S=(1,1,1) T=(0,0,0)
[J0 D1 T0] flags=0x00030010 coord=UV bump=False ...   (同じパターン)
[J0 D2 T0] flags=0x00030010 coord=UV bump=False ...
```

→ vanilla course mesh は coord=UV (=0) で、`fallback default` branch を通る。**vanilla も my_course も同じ UV 経路 = 同じ GXSetTexCoordGen2 引数 (slot 60 を mtx source に使う)**。差は loaded matrix の中身のみ。

### my_course flat-color 機序 (= fix 機序)

hsdraw export (fix 前) の TObj 默認値:
- `SX = SY = SZ = 1`
- `RX = RY = RZ = 0`
- `TX = TY = TZ = 0`
- `repeat_s = repeat_t = 0` ← **問題**

`TObj_RebuildTransformMtx` で:
- `|SX| = 1 ≥ 1e-10` なので `sx = (double)repeat_s / SX = 0 / 1 = 0`
- 同様 `sy = 0`, `sz = TZ = 0`
- `mtx = Scale(0, 0, 0)`

これが slot 0x3c (GX_IDENTITY) に上書きされ、`GXSetTexCoordGen2(coord, MTX3x4, src=TG_TEX0, mtx=0x3c, ...)` で UV (u, v, 1) を変換 → 出力 (0, 0, 0) → **すべての UV が (0, 0) に collapse** → texel(0,0) のみ sampling → flat color。

### fix `repeat_s = repeat_t = 1`:
- `sx = 1 / 1 = 1`, `sy = 1 / 1 = 1`, `sz = 0` (TZ そのまま)
- `mtx = Scale(1, 1, 0)`
- 出力 (u, v, 0) — Q=0 だが GameCube hardware は Q=0 を許容する (vanilla も同条件で動いているため)
- **UV pass-through で正常 sampling**

### 仮説 (要検証)

A. **`repeat_s/t = 1` fix の代替経路**:
   - **TZ (translation Z) を 1 に変更**しても `sz = 1` になり、`mtx = Scale(0, 0, 1)` だが U, V スケールが 0 のままなので無効。**alternative にならない**。
   - **SX/SY を 0 にする** と `|SX| < 1e-10` で `sx = FLOAT_806dcb88 = 0.0` (`= 0.0`、確認済み) → 同じく collapse。これも fix にならない。
   - **repeat_s/t と SX/SY を同時に変える** (e.g. repeat=2, SX=2 → sx=1) は理屈上 fix だが、SX=2 は「scale 2x」を意図する場合に発火する誤った設定。
   - **本質的には `repeat_s/t = 1` が唯一の正解 path**。HSDLib `HSD_TOBJ.New()` のデフォルトとも一致する (= 仕様準拠)。

B. **vanilla 経路で TEX0_MTX[0] (slot 30) に毎 draw camera mtx を load する経路**:
   - `FUN_802cc450 @ 0x802cc450` (POBJ shape mtx loader) が `GXLoadTexMtxImm(mtx, 0x1e=30, 0)` を呼ぶ — **POBJ shape path は coord_type=REFLECTION/HILIGHT (=1/2) のときに使う TEXMTX0 を load する**。これは `TObj_SetupTexCoordGenList` の REFL/HILIGHT branch (`mtx=0x1e`) に対応。
   - **UV path (coord_type=0) では TEXMTX0 を load する経路は使わない**。
   - 前回 trace の fifolog 観察 (`vanilla = MATIDX_A tex0 = slot 0`) は course mesh とは別の draw (おそらく shadow projector) のキャプチャと思われる — UV path の vanilla mesh は my_course と同じ MATIDX_A = slot 60 を発行するはず。fifolog 再採取で確認可。

C. **Q=0 行列出力の hardware 動作**:
   - `Scale(1, 1, 0)` は UV (u, v, 1) → (u, v, 0) を出力。Q=0 なので projection=ST モード hardware が division を skip して (s, t) をそのまま使うと推定。
   - vanilla course mesh も同様 (TZ=0, repeat=1, SX=1 → Scale(1, 1, 0)) なので、**Q=0 が hardware 上で問題なく動くことは vanilla で実証済み**。
   - hardware が Q=0 で何を返すかは GX 仕様書未確認。観察上は vanilla で正常表示されているため OK と判断。

## hsdraw 設計改善提案 (= elegant な fix 候補)

現状の fix は `tobj.repeat_s = tobj.repeat_t = 1` を hsdraw 側で setter 経由で書く対応。これで十分だが、**より本質的な改善**:

1. **hsdraw `TObj.alloc()` の default を HSDLib `HSD_TOBJ.New()` に合わせる**:
   - HSDLib: `SX = SY = SZ = 1, RepeatS = RepeatT = 1`
   - hsdraw 現状: 全 0 (たぶん)
   - **hsdraw 側 `TObj.alloc()` で `set_scale(1,1,1)` + `repeat_s/t = 1` をデフォルト適用**するのが筋。caller 側で毎回明示する必要がなくなる。
   - 関連 memory: `project_hsdraw_no_tex_gen_src_setter.md` (similar default 漏れ事例)、`project_hsdraw_set_cull_back_pobj_flag_trap.md`

2. **`coord_type` setter は安全だが、`tex_gen_src` setter の有無は要確認**:
   - 既存 memory `project_hsdraw_no_tex_gen_src_setter.md` で「hsdraw に `TObj.GXTexGenSrc` setter が無い、byte patch workaround」と記録あり。
   - これも「default が間違ってる」型の問題なので一括対応するなら hsdraw 側 default 修正がエレガント。

3. **`MTX_DIRTY` rebuild の trigger が runtime コピー時の OR `0x80000000` だけ** であることに注意:
   - hsdraw export 後に runtime で別経路で書き換えると lazy rebuild が走らない可能性 (= mod 系 patch で TObj fields を後書きするときは MTX_DIRTY を立て直す必要)。今回は disk export なので問題なし。

## 関数 trace table (rename 済み)

Part 1 で挙げた関数のうち、本 trace で意味が確定したもの — **Ghidra 上で全件 rename + prototype + plate comment 済み**:

| 旧名 | 新名 | 意味 |
|---|---|---|
| `FUN_802be72c` | `TObj_SetupTexCoordGenList` | per-DObj texgen setup loop, branch table on coord_type |
| `FUN_802be3c4` | `TObj_LoadTexMtxList` | per-DObj UV mtx loader (lazy rebuild + GXLoadTexMtxImm to slot=mtxid) |
| `FUN_802be1f0` | `TObj_RebuildTransformMtx` | RST + repeat → 3x4 matrix rebuild (vtbl[0x3c]) |
| `FUN_802bdec8` | `TObj_LoadFromDesc` | disk → runtime field copy, mtxid hardcoded to 0x3c (vtbl[0x40]) |
| `FUN_802bdff0` | `TObj_AllocAndLoadDesc` | recursive TObj alloc + loadDesc dispatch |
| `FUN_802c0894` | `TObj_ClassInit` | hsd_tobj class info init (head_obj_size = 0xac) |
| `FUN_802bfd08` | `TObj_SetupTextureList` | per-DObj texture upload (TLUT, GXInitTexObj, GXLoadTexObj) |
| `FUN_80266020` | `GXSetTexCoordGen2` | XF TEXMTXINFO + MATIDX programming |
| `FUN_8026a3d8` | `GXLoadTexMtxImm` | stream 3x4 / 2x4 mtx to TexMtx slot |
| `FUN_8026a304` | `GXLoadPosMtxImm` | stream 3x4 mtx to PNMTX slot |
| `FUN_802682a8` | `GXInitTexObjFilter` | populate filter / bias / aniso fields of GX_TexObj |
| `FUN_8025d494` | `PSMTXConcat` | 3x4 mtx multiply (paired-singles) |
| `FUN_8025d9ec` | `PSMTXTrans` | 3x4 translation mtx builder |
| `FUN_8025da6c` | `PSMTXScale` | 3x4 scale mtx builder |
| `FUN_802d6e3c` | `MTXRotEulerXYZ` | Euler XYZ → 3x4 rotation mtx |
| `FUN_80264290` | `GXInit_LoadIdentityTexMtxSlots` | boot: identity → slot 0x3c, 0x7d |

加えて `/HSD/HSD_TObj_Runtime_Partial` struct (172 bytes) を Ghidra に登録済み。
- `tobj` parameter 型として `TObj_LoadFromDesc / TObj_AllocAndLoadDesc / TObj_LoadTexMtxList / TObj_RebuildTransformMtx / TObj_SetupTexCoordGenList` に適用済み

## 残ギャップ

1. **`FUN_802c4010` の中身 (BUMP path で呼ばれる lightmask query)** — BUMP path は my_course では使われないため未調査。
2. **`DAT_806ccf80` の中身 (special coord_id branch で TEXMTX1 にロードされる行列)** — coord_id=0xFF 用なので未調査。
3. **fifolog 観察「vanilla MATIDX_A tex0 = slot 0」と vanilla course .dat の TObj.coord_type=0 (UV) が矛盾**する点。SHADOW projector などの別 draw を見ていた可能性が高い。再 fifolog 採取で確証取れる (要 in-game 検証)。
4. **Q=0 の hardware 動作の仕様書確認**。vanilla で動いている = hardware が tolerate すると観察済みだが GX 仕様書 (Yagcd など) で文献確認はしていない。

## 派生 (memory 候補)

- `project_tobj_repeat_s_t_one_required.md`: hsdraw default `repeat_s=repeat_t=1` 必須、機序は disk→runtime field map + Scale(repeat/SX, ...) 行列で 0/1 の collapse 経路、`/HSD/HSD_TObj_Runtime_Partial` struct + 16 関数 rename 完了
