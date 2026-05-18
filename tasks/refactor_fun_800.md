# MKGP2 FUN_800* 全件リファクタリング

`/goal /mkgp2-ghidra と /ghidra-rename で、MKGP2 の FUN_800* に該当する関数すべてをリファクタリングする。`

## 方針 (2026-05-18 user confirm)

- **進行順序**: アドレス順 (FUN_80003100 から)
- **確証レベル**: 親子関係 (caller/callee) を注視し、なるべく rename する。命名不能だったものは「諦めリスト」に理由付きで残す。

## 規模 / 見積もり

- 対象範囲: 0x80003100 ~ 0x800ffbb8、約 1500 件
- 1 件 5-15 分 → 数百時間。**複数セッション縦断前提**。

## 完了基準 (関数 1 件)

1. 関数名 (rename_function)
2. prototype (set_function_prototype)
3. plate comment (set_plate_comment)
4. 変数 rename 試行率 80%+ (本文非登場 phantom は分母から除外)
5. struct アクセスがあれば apply_data_type (partial struct で良い)

## 進捗 (アドレス順)

各セッションで進めた範囲を記録。**「最後に処理した address」**を更新していけば、次セッションの再開点が明確になる。

- 開始: 2026-05-18
- 最後に処理した address: 0x80041fe0 (KartDriver_TriggerSlotC3Action rename 完)
- 次セッション開始点: 0x80042000 以降

### Session 27 完了分 (2026-05-18、8 件) — KartDriver root accessor + per-slot transform setters

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80041748 | FUN_80041748 | KartDriver_GetKartRootMtx | driver->kartModel の JObj local mtx 取得 (100+ caller) |
| 0x8004178c | FUN_8004178c | KartDriver_SetJointPosY_Slot3d | joint slot 0x3d の JObj position Y setter |
| 0x80041858 | FUN_80041858 | KartDriver_SetJointPosY_Slot3c | 同 slot 0x3c |
| 0x80041924 | FUN_80041924 | KartDriver_SetJointPosY_Slot3b | 同 slot 0x3b |
| 0x800419f0 | FUN_800419f0 | KartDriver_SetJointPosY_Slot3a | 同 slot 0x3a |
| 0x80041abc | FUN_80041abc | KartDriver_SetEulerY_4Joints_LastMirrored | 4 joint (3a/3b/59/5b) Euler Y、最後 mirror |
| 0x80041d80 | FUN_80041d80 | KartDriver_SetUniformScale_4Wheels | 4 wheel (3a-3d) uniform scale (X=Y=Z) |
| 0x80041fe0 | FUN_80041fe0 | KartDriver_TriggerSlotC3Action | slot 0xc3 non-NULL で FUN_80079224 trigger |

主要発見:
- **per-wheel transform setter pattern**: slot 0x3a..0x3d = 4 wheel joints (tire/ground 系)
- **mirror slot pair**: 0x59/0x5b は対称 part (= 左右の handle/wheel)、最後 element の
  rotation を -input で適用
- JOBJ_USE_QUATERN flag (0x20000) チェック付き Euler write (assert で QUATERN モード排除)
- **dirty mark pattern**: 0x2000000 skip-dirty flag + (0x800000=0 && 0x40!=0) で
  FUN_802d20ac (mark dirty) 呼び出しの共通 idiom (8+ 関数で同 boilerplate)

副次 rename 候補:
  FUN_802d20ac → JObj_MarkDirty (recurring)
  FUN_80079224 → KartDriver slot C3 用 action (副次調査)

### Session 26 完了分 (2026-05-18、8 件) — KartItem state begin family + KartDriver joint accessors

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800404c4 | FUN_800404c4 | KartItem_CancelIfNotForced | self[+0x5e] gate 付き state cancel (CancelAndQueueDefault のバリアント) |
| 0x80040890 | FUN_80040890 | KartItem_TryBeginState25 | state 0x25 の begin (no current/saved state 条件) |
| 0x800408f8 | FUN_800408f8 | KartItem_TryBeginState21Family_v1 | state 0x21-0x24 family、saved value で分岐、+0x78 init = 1 |
| 0x80040a28 | FUN_80040a28 | KartItem_TryBeginState21Family_v2 | 同 family の v2、+0x78 init = 0、param4 offset 差 |
| 0x80040b48 | FUN_80040b48 | KartItem_TryBeginState18Family | state 0x18-0x1B family (saved 0/3/4/その他で variant) |
| 0x80040d84 | FUN_80040d84 | KartDriver_GetJointPosition | joint translation (mtx col 0xc/0x1c/0x2c) → outVec 3 word |
| 0x80040dfc | FUN_80040dfc | KartDriver_GetJointMatrix4x3 | joint mtx を column-major → row-major 変換で出力 |
| 0x80040e9c | FUN_80040e9c | KartDriver_GetJointByIdx | jointSelector 0..0x10 → driver の joint field 別 JObj ptr 取得 (17-way dispatcher) |

主要発見:
- **KartDriver joint dispatcher** (0..0x10 = 17 種類):
  - 0..3: tires FL/FR/RL/RR
  - 4..7: ground FL/FR/RL/RR
  - 8: engine
  - 9: body
  - 10/11: mufflers L/R
  - 0xc: right hand
  - 0xd: TeresaNull
  - 0xe: InfoNull
  - 0xf: HeadNull
  - 0x10: koopa fire null (robo kart only)
- **state group ID mapping** (TryBeginStateXXFamily 解析より):
  - 0x18-0x1B family: saved 0/3/4/その他 → 0x18/0x1a/0x1b/0x19
  - 0x21-0x24 family: 同上 → 0x21/0x23/0x24/0x22
  - 0x25: 単独 state
- **state begin の前提条件 pattern**: self[+0x7c] (current state)、+0xa9 (saved state)、+0x6e (cooldown)、+0xb0 (item type for 0x51 exclusion)、+0x5e (force flag)

### Session 25 完了分 (2026-05-18、12 件) — KartItem field setters/getters + cancel

KartItem 系 cluster の continuation: field-level setter/getter 9 件 + state cancel/trigger 3 件。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003f2d8 | FUN_8003f2d8 | KartItem_BroadcastParamToCarAndISE | car render + ISE 両者に param sync (重複防止) |
| 0x8003f394 | FUN_8003f394 | KartItem_SetField358 | trivial setter +0x358 |
| 0x8003f39c | FUN_8003f39c | KartItem_SetShortArray34a | 5-short config 配列 (+0x34a..+0x352) setter |
| 0x8003f3e4 | FUN_8003f3e4 | KartItem_SetByte2b8 | trivial byte setter +0x2b8 |
| 0x8003f3ec | FUN_8003f3ec | KartItem_GetCurrentISESlot | self[+0x2dc + slot*4] (active ISE ptr) |
| 0x8003f410 | FUN_8003f410 | KartItem_SetField2ac | trivial setter +0x2ac |
| 0x8003f418 | FUN_8003f418 | KartItem_GetOrAssignId | lazy ID gen + cache (+0x244 flag、+0x23c cached) |
| 0x8003f554 | FUN_8003f554 | KartItem_SetVec3At338 | 3-word vec3 setter +0x338..+0x340 |
| 0x8003f584 | FUN_8003f584 | KartItem_IsAtStartSlot | +0x248 == g_kartStartSlot + callback |
| 0x8003f5c4 | FUN_8003f5c4 | KartItem_TriggerStateAction | self[+0x29c] への action trigger |
| 0x8003f660 | FUN_8003f660 | KartItem_GetField1f0 | trivial getter |
| 0x8003f668 | FUN_8003f668 | KartItem_CancelAndQueueDefault | active state を cancel + default (-1) を queue |

主要発見:
- **KartItem state struct layout 追加**:
  - +0x23c / +0x244: cached id + generated flag (lazy ID)
  - +0x248: kart slot id (= g_kartStartSlot 一致判定)
  - +0x29c: state object ptr (KartItem_TriggerStateAction の対象)
  - +0x338..+0x340: vec3 field (position?)
  - +0x34a..+0x352: 5-short config array
  - +0x358: 何かの param ptr
  - +0x378 / +0x374: ISE param sync sticky
- **CarObject ↔ ISE の sync 経路**: BroadcastParamToCarAndISE が両者に同時 propagate
- **state cancel + default queue pattern**: 既存 state を full reset してから -1 を queue

副次 rename 候補:
  FUN_8009c170 → CarObject_GetActive
  FUN_80250ae8 → CarObjectRender_SetParam
  FUN_8024f650 → ISE_SetParam
  FUN_8009bd04 → KartItemId_Generate
  FUN_801737cc → KartItem_state callback (+0x29c 用)
  FUN_801737f0 → KartItem_state action (+0x29c 用)
  g_kartStartSlot — kart slot id 比較定数

### Session 24 完了分 (2026-05-18、5 件) — KartItem state machine cluster

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003e41c | FUN_8003e41c | KartItem_BeginQueuedState | 次の item state を queue する setup (sound trigger 0x68/0x69) |
| 0x8003e694 | FUN_8003e694 | KartItem_TickEffectStage_a | per-frame tick (char* offset 版、+0x2a0/+0x290 駆動) |
| 0x8003eb20 | FUN_8003eb20 | KartItem_TickEffectStage_b | 同 logic の int* indexing 版、SE 0xc0 trigger |
| 0x8003ed8c | FUN_8003ed8c | KartItem_TickStunStateMachine | 3-stage (0→1→2) stun state、threshold 25a0/25a4/2560 |
| 0x8003ef20 | FUN_8003ef20 | KartItem_TickTrailingFx | trailing fx 用 cleanup tick、per-player tagged SE |

主要発見:
- **KartItem state machine の共通構造**:
  - +0x290 (= [0xa4]): remaining unit count (decreasing)
  - +0x294 (= [0xa5]): duration float
  - +0x298 (= [0xa6]): "in 1 unit" flag (byte)
  - +0x2a0 (= [0xa8]): phase progress (float, 0..threshold)
  - +0x2b3 (= [0xac+3]): is active flag (byte)
  - +0x2b4 (= [0xad]): stage within state (0/1/2)
  - +0x2c8 (= [0xb2]): item slot index (-1 = no item)
  - +0x2c0 (= [0xb0]): item type id
  - +0x2dc..: ISE slot ptr table (item-indexed)
  - +0x32c (= [0xcb]): ISE state object ptr
  - +0x29c (= [0xa7]): item-tracker object
  - +0x174 (= [0x5d]): "持っている" flag
  - +0x205 (= [0x81]): aux byte
  - +0x1cc (= [0x73]): per-frame phase increment
  - +0x1e0 (= [0x78]): damage progress (stun のみ)
- **per-player tagged SE**: ((self.player_idx & 0xf) << 0x1b) | 0xb3 で kart 番号別 channel
- 各 variant が異なる threshold / SE / cleanup chain を持つ

副次 rename 候補:
  FUN_8016c2d0 / 288 → SoundMgr_Play3DSound / Update3DPosition
  FUN_8016c360 / 394 → SoundMgr_IsSEPlaying / StopSE
  FUN_8007b44c / 468 → ISE state helpers
  FUN_80173b68 / be8 → KartItem state reset trio (recurring)
  FUN_80049cc4 / 9a90 → ISE finalize / delta apply
  ISESlot_Deactivate / StopEffect / StartCleanup (vanilla?)

### Session 23 完了分 (2026-05-18、3 件) — angular check + expression switcher + hand item anim

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003dcd4 | FUN_8003dcd4 | JObj_IsForwardDotBelowThreshold | JObj forward vec と target dir の dot < 閾値 (line-of-sight angular check) |
| 0x8003ddac | FUN_8003ddac | JObj_SetExpressionByIndex | 4-slot JObj (+0x140..+0x14c) 排他 show/hide (-1..3) |
| 0x8003df8c | FUN_8003df8c | KartHand_TickItemAnim | kart hand bone 上で item を swing → launch (countdown + spawn) |

主要発見:
- **kart hand item launch sequence**: angle swing (phase 0-10 で増、27-40 で減) + countdown
  (self+0x178 = 30 frame で spawn) のフレーム駆動 state machine
- **item alias map**: 0x6c → 0x35 (banana?)、0x6d → 0x36 (mushroom?)、0x6e → 0x49、0x6f → 0x70
- **4-slot expression switcher**: kart driver の表情 / wheel face / 状態 model 用
- AI 「視界外」判定: forward dot product と FLOAT_806d2544 cosθ 閾値で 2D angular check

副次 rename 候補 (大量):
  ISESlot_Deactivate
  FUN_8025d1b8 → PSMTXCopy
  FUN_8025d770 → PSMTXRotRad
  FUN_800dd8d8 / 904 / 8ac → ItemObject_SetPosition / Direction / Owner
  FUN_800dd89c → ItemObject_AttachSound
  FUN_80173b68 / 80173be8 → KartItem state reset trio
  FUN_802d1e34 → JObj eval/update
  ItemObject_SpawnWithAlias (vanilla?)

### Session 22 完了分 (2026-05-18、4 件) — Race progress comparator + Path participant array

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003cdd8 | FUN_8003cdd8 | Race_CompareKartProgress | 2-kart race progress comparator (lap*total + wp 圧縮 score) |
| 0x8003cee4 | FUN_8003cee4 | Path_ResetCursorForKart | 8-kart 中 1 名の cursor を -1 (= 未スタート) にリセット |
| 0x8003d158 | FUN_8003d158 | PathParticipantArray_Dtor | 8-kart cursor 配列の一括 clear + PathCursor_Free + free |
| 0x8003d304 | FUN_8003d304 | Object_RenderJObjIfWithinRange | distance scalar < 閾値 で Object_RenderJObjTree、LOD/cull gate |

主要発見:
- **PathManager struct layout**:
  - per-kart cursor stride 0x98 (cursor base +0x14, +0xac, +0x144, +0x1dc, +0x274, +0x30c, +0x3a4, +0x43c)
  - per-kart status byte at +0x4d4..+0x4db (8 bytes)
  - **8-kart 固定**
  - 4-byte field per cursor at offset 5 (= +0x14) = waypoint index (-1 で 未スタート)
  - 4-byte field at offset 0x18 (= +0x60) = lap count
- **race rank scoring**: progress = lap_count * total_waypoints + wp_index、wrap-around 補正
  (last-10 to first-10) で finish line over 検出
- LOD distance gate: FLOAT_806cedbc 閾値で render skip

副次 rename 候補:
  FUN_80276350 → abs / 標準 math
  FLOAT_806cedbc → LODDistanceThreshold

### Session 21 完了分 (2026-05-18、5 件) — MemoryManager_Alloc + SceneFlow state setters

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003b120 | FUN_8003b120 | MemoryManager_Alloc | HeapAlloc wrapper (size=0 → 1 補正、init check、150+ caller) |
| 0x8003b2d8 | FUN_8003b2d8 | SceneFlow_DefaultGetZero | constant 0 return、Flow_TransitionTo 内 callback |
| 0x8003b2e0 | FUN_8003b2e0 | SceneFlow_SetCleanupTag | DAT_806d1040 setter (BootDispatcher / Flow_TransitionTo) |
| 0x8003b2e8 | FUN_8003b2e8 | SceneFlow_InitCleanupList | DAT_805987b8..d0 doubly-linked list head reset |
| 0x8003b30c | FUN_8003b30c | SceneFlow_SetFlag1048 | DAT_806d1048 byte setter (race/timer 系 caller 2 件) |

主要発見:
- **Session 2 deferred の SceneTransitionCounter_*** 系を正式 rename (3 件)
- DAT_805987b8..d0 が sentinel-bookended doubly-linked list (空状態で head/tail cell が円環)
- BootDispatcher と Flow_TransitionTo が cleanup list を共有 (boot / scene change で flush)
- MemoryManager_Alloc (FUN_8003b120) を正式 rename

副次 rename 候補:
  DAT_806d1040 → g_sceneCleanupTag
  DAT_805987b8 → g_sceneCleanupListHead
  DAT_805987cc → g_sceneCleanupListTail
  DAT_806d1048 → g_sceneFlowFlag1048

### Session 20 完了分 (2026-05-18、3 件) — clRom table sweep + memory manager pair

Session 8 plate で「ObjectChain_Release」と仮称していた FUN_8003aee8 が実は
MemoryManager_Free (HeapFree wrapper) と確定。session 8/9 の plate 内 "FUN_8003aee8"
記述は新名で読み替え。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003ac84 | FUN_8003ac84 | ClRomTable_PurgeAll | DAT_80598678 (40-entry path-keyed table) を全 sweep dispose |
| 0x8003adfc | FUN_8003adfc | MemoryManager_AllocTagged | tag string で識別する allocator wrapper (debug error 識別用) |
| 0x8003aee8 | FUN_8003aee8 | MemoryManager_Free | HeapFree wrapper (init check + scope timer)、100+ caller |

主要発見:
- DAT_80598678 は clRom-keyed registry table (40 entries × (clRomEntry*, path*))
- MemoryManager 三組:
  - Alloc (FUN_8003b120、既知): 一般 alloc
  - AllocTagged (FUN_8003adfc、本 session): error message tag 付き alloc
  - Free (FUN_8003aee8、本 session): HeapFree wrapper
- Session 8 で誤称した FUN_8003aee8 = "ObjectChain_Release" は MemoryManager_Free が
  正解 (Object dtor で sub-resource ptr を free する用途で頻出)

副次 rename 候補:
  DAT_80598678 → g_clRomKeyedTable
  FUN_80278fd8 → strcmp (再認)
  FUN_8008ee60 → HeapFree_Internal
  FUN_8008ef5c → HeapAlloc_Internal
  DAT_806d0fa1 → g_memoryManagerInitialized
  DAT_806cf010 → g_mainHeap

### Session 19 完了分 (2026-05-18、6 件) — SceneRender per-object pipeline

HSD Scene + dynamic light culling + per-object render pipeline の cluster。CObj 系
(Session 3 で確定) と連携。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003a71c | FUN_8003a71c | SceneRender_BaseDtor | trivial base class vtable downgrade dtor (PTR_803f58e0) |
| 0x8003a764 | FUN_8003a764 | SceneRender_SetViewportRect | 6 float (position vec3 + size vec3) setter at +0x14..+0x28 |
| 0x8003a780 | FUN_8003a780 | SceneRender_DrawObjectWithLights | camera setup + dynamic light cull + per-obj draw + metric 0x12 |
| 0x8003a9e8 | FUN_8003a9e8 | SceneRender_CmdA_8a9e8 | gate付き FUN_802dbf20 forward |
| 0x8003aa20 | FUN_8003aa20 | SceneRender_CmdB_8aa20 | gate付き FUN_802dbe74 forward (CmdA とペア) |
| 0x8003aa58 | FUN_8003aa58 | SceneRender_Dtor | 派生 dtor: vtable + scene release + base chain |

主要発見:
- **per-object render pipeline 構造**: viewport apply (CObj_ApplyViewport) → matrices →
  light culling (distance gated) → ambient apply → draw chain
- **distance-gated light culling**: FLOAT_806d24ac 閾値で scene→root から light walker
- MetricsTable slot 0x12 を SceneRender_DrawObjectWithLights が消費 (memory_alloc と
  当初推定したが、object draw 計測の可能性)
- 6-float viewport rect layout (+0x14..+0x28) は (origin xyz, size xyz)

副次 rename 候補 (HSD scene helpers、大量):
  FUN_802dbb84 — HSD scene draw step
  FUN_802dc2c0 — light frustum setup
  FUN_802dc550 — add light contribution
  FUN_802dc4d4 — light ambient computed query
  FUN_802dc0f4 — ambient color apply
  FUN_802d6948 — JObj mtx → world position
  FUN_802d4748 / 47e8 — GX state save/restore
  FUN_802db570 / 740 / 9ec — HSD scene draw chain
  FUN_802dbf20 / be74 — scene state cmd pair
  FUN_802db468 — scene resource release

### Session 18 完了分 (2026-05-18、7 件) — my_class_library MObj/MJObj 派生クラス群

format string "my_class_library" + "model.mobj.1" / "model.mobj" / assert "model.c" で
mkgp2 独自の HSD MObj / JObj 派生クラス cluster を確認。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8003a154 | FUN_8003a154 | MJObj_SetColorRGBA_Float | 4 float RGBA → 255 scale + alpha clamp + byte 化 + MJObj_SetColorRGBA |
| 0x8003a30c | FUN_8003a30c | MObj1_ClassInit | "model.mobj.1" の class init (parent = MObj、TEV stage 拡張版) |
| 0x8003a368 | FUN_8003a368 | MObj1_TEVStateInit | MObj.1 instance の TEV stage / Z-mode 構成 build |
| 0x8003a49c | FUN_8003a49c | MObj_ClassInit | "model.mobj" の base class init (parent = HSD MObj) |
| 0x8003a4f8 | FUN_8003a4f8 | MObj_InitMember | MObj instance の per-class init (self+0x20 = default TObj) |
| 0x8003a5a8 | FUN_8003a5a8 | MJObj_InitMember_RGBA | MJObj instance の per-class init (+0x88..+0x8b = 0xff RGBA default) |
| 0x8003a608 | FUN_8003a608 | MJObj_SetColorRGBA | byte RGBA setter (各成分 [0, 0xff] clamp、assert で derived check) |

主要発見:
- **my_class_library** という mkgp2 独自の HSD 派生 class library 存在を確認
  (format string "my_class_library" @ 0x802e9cc8)
- **MObj 階層**: base "model.mobj" → 派生 "model.mobj.1" (TEV stage 拡張)
- **MJObj layout**: 親 HSD JObj + +0x88..+0x8b RGBA byte field
- **HSD_ClassInit** 6 引数 sig: (self vtable, parent vtable, library name, class name,
  instance_size 0x54, ?)
- assert source file "model.c" 確認 (mkgp2 model.h 系の class hierarchy)

副次 rename 候補:
  FUN_802df044 → TObj_BindToTEVStage (推測)
  FUN_802def84 → MObj_BuildGXState
  FUN_802dfef4 → MObj_SetZMode
  FUN_802df164/8ec/24c/dec → MObj TEV combiner setters
  DAT_803f5878 → MObj1 GX default constant
  DAT_804fc200 / 804fc9d4 → HSD MObj.init / JObj.init member pointers
  DAT_806d1038 → MObj default TObj data
  PTR_FUN_803f5888 / 5834 / 5874 / 58cc → my_class_library class vtables

### Session 17 完了分 (2026-05-18、3 件) — VolumeCalibration scene tick/ctor/dtor

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80039edc | FUN_80039edc | VolumeCalibration_Tick | per-frame JVS raw input min/max tracking + strpcb pos read + 入力分岐 |
| 0x8003a00c | FUN_8003a00c | VolumeCalibration_Dtor | commit calibration to DAT_80598a94..a0 + strpcb pos + NVRAM persist |
| 0x8003a0c4 | FUN_8003a0c4 | VolumeCalibration_Ctor | Alloc(0x28) + 初期値 (steering 0x7a40/+0x1ff、accel/brake は jvs raw) |

主要発見:
- VolumeCalibration scene struct (0x28 byte) layout: +0x4 current steering, +0x8 strpcb pos,
  +0x10/+0x14/+0x18/+0x1c/+0x20/+0x24 で steering/accel/brake の min/max
- 入力 button code: 0x401 = exit、0x40 = confirm/ack (cabinet ack feedback)
- DAT_80598a94..a0 = saved calibration globals (steering/center/accel/brake)
- 0x1ff = 中央 position (10-bit center)、再認

副次 rename 候補:
  DAT_80598a94..a0 → g_savedCalibration cluster
  FUN_8003959c / 955c / 951c → per-axis clamp/commit (3 axis ともに)
  FUN_80075470 → CalibrationData_SaveToNVRAM (推測)
  GameMode_BaseInit (FUN_???)
  PlayerInput vtable[8] → IsButtonPressed

### Session 16 完了分 (2026-05-18、4 件) — metrics init/shutdown + volume calibration overlay

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80039d38 | FUN_80039d38 | MetricsTable_Init | boot で g_metricsTable[48] を alloc + init + atexit 登録 |
| 0x80039d90 | FUN_80039d90 | MetricsTable_Shutdown | g_metricsTable を sweep して per-slot dtor 実行 |
| 0x80039dc8 | FUN_80039dc8 | MetricsTable_DefaultDtor | per-slot trivial dtor callback (実質 no-op) |
| 0x80039e10 | FUN_80039e10 | VolumeCalibration_DrawOverlay | "Volume caribration" (vanilla typo) HANDLE/PEDAL 値表示 |

主要発見:
- Triforce cabinet の boot "Volume calibration" 画面 (steering / pedal raw value 確認画面)
- format strings: "Volume caribration", "HANDLE NOW %d" (raw >> 6)
- PTR_s_RIGHT_PEDAL_806d2480 が 2+ 個分の input label table

副次 rename 候補:
  FUN_80270d6c → Array_AllocAndInit (推測)
  FUN_80270cf4 → Array_DisposeAll
  FUN_80270c30 → AtExit_Register (再認)
  PTR_s_RIGHT_PEDAL_806d2480 → g_calibrationInputLabels

### Session 15 完了分 (2026-05-18、3 件) — metrics table API trio

g_metricsTable[0..0x2f] (48 slot per-feature timing accumulator) の getter/setter/HUD 表示。
Session 2 で deferred 名 MetricsTable_Accumulate を正式採用。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80039be8 | FUN_80039be8 | MetricsTable_Get | slot 値を time-scale で割って double 返却 |
| 0x80039c40 | FUN_80039c40 | MetricsTable_DisplayOverlay | "%16s > %2.5f" format で debug HUD 表示 |
| 0x80039cf4 | FUN_80039cf4 | MetricsTable_Accumulate | += accumulator (slot 0..0x2f bounded) |

主要発見:
- PTR_s_SYSTEM_802e9b7c が 48 個分の slot label string ptr table
- Get 経路で FLOAT_806d2478 (epsilon / noise floor) と FLOAT_806d247c (time scale) を使用

副次 rename 候補:
  PTR_s_SYSTEM_802e9b7c → g_metricsSlotLabels
  FLOAT_806d2478 → MetricsEpsilon (noise floor)
  FLOAT_806d247c → MetricsTimeScale

### Session 14 完了分 (2026-05-18、15 件) — CW STL template + shared_ptr + ClStrPcb vtable cluster

CW MSL の STL container (set/map 風) と shared_ptr テンプレートの instantiation 群、
ClStrPcb vtable 3-level hierarchy の 2/3 段 dtor。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800388b4 | FUN_800388b4 | ClStrPcb_Mid_Dtor | CW C++ 2-level vtable downgrade dtor (中間→基底) |
| 0x80038934 | FUN_80038934 | STLContainer_GlobalInit_854 | DAT_80598554 を STL container として boot init + atexit 登録 |
| 0x80038980 | FUN_80038980 | STLContainer_HeaderInit | sentinel node + size/root 初期化 (CW MSL 風) |
| 0x800389a0 | FUN_800389a0 | STLContainer_Dtor | tree dispose entry (root != 0 で recursive dispose 呼び出し) |
| 0x80038a00 | FUN_80038a00 | STLContainer_DisposeRecursive | 4-level manual unroll の tree subtree dispose、CW template 展開 |
| 0x80038dc8 | FUN_80038dc8 | STLContainer_GetAllocatorSlot | self + 4 wrapper (trivial getter) |
| 0x80038dd0 | FUN_80038dd0 | Stub_NoOp_38dd0 | empty stub (template no-op) |
| 0x80038dd4 | FUN_80038dd4 | ClStrPcb_Base_Dtor | 基底 vtable のみ設定 shallow dtor |
| 0x80038e30 | FUN_80038e30 | SharedPtr_Dtor | refcounted ptr の dispose (refcount decrement + 0 で free) |
| 0x80038ea8 | FUN_80038ea8 | SharedPtr_Init | refcount cell alloc + value set ctor |
| 0x80038f54 | FUN_80038f54 | Stub_NoOp_38f54 | empty stub |
| 0x80038f58 | FUN_80038f58 | Stub_NoOp_38f58 | empty stub |
| 0x80038f5c | FUN_80038f5c | Allocator_Deallocate | STL allocator deallocate() wrapper (TimedFree) |
| 0x80038f80 | FUN_80038f80 | Stub_NoOp_38f80 | empty stub |
| 0x80038f84 | FUN_80038f84 | Mtx4x4_TransposeTo4x3 | 16-float column-major → 12-float row-major matrix 変換 |

主要発見:
- **CW MSL STL template の instantiation cluster**: STLContainer_*、Stub_NoOp_*、
  Allocator_Deallocate 等は CW template engine の per-type instantiation。trivial-type で
  empty stub になる関数が多い (= compiler が template に default 引数を渡した結果)
- **shared_ptr 実装** が SharedPtr_Init/Dtor で確認: 2-word struct = [raw_ptr, ref_cell]、
  refcell は alloc'd int (initial count = 1)
- **ClStrPcb は 3-level inheritance hierarchy**: 派生 vtable 803f56f0 → 中間 803f5700 →
  基底 803f5710、各レベルに dtor あり (ClStrPcb_Dtor / Mid_Dtor / Base_Dtor)
- **Mtx4x4_TransposeTo4x3**: 4x4 → 4x3 行列の transpose 経由 dimension reduction
  (PSMTX44 → PSMTX の変換ヘルパー)
- **STL container 用途は副次**: DAT_80598554 は副次 (game-global config or registry の可能性)

副次 rename 候補:
  DAT_80598554 → g_someSTLContainer (用途不明、副次調査)
  DAT_80598548 → g_someSTLContainerData
  FUN_8025d120 → 副次 (Mtx4x4_TransposeTo4x3 内、dst preprocessing)
  PTR_803f56f0 / 803f5700 / 803f5710 → ClStrPcb 3 vtable

### Session 13 完了分 (2026-05-18、8 件) — strpcb singleton lifecycle + clamp util

strpcb の singleton boot/shutdown 系と汎用 saturate/clamp util。Session 2 で deferred
名 `ApplicationRunFlag_Get` を採用した DAT_806d1010 getter は実は **strpcb singleton ptr
getter** だったので訂正。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80038574 | ApplicationRunFlag_Get | StrPcb_GetInstance | strpcb singleton (DAT_806d1010) getter — Session 2 deferred の名前を訂正 |
| 0x8003857c | FUN_8003857c | StrPcb_Shutdown | state 1 待ち block 後の singleton dispose (motor 暴走防止) |
| 0x800386d0 | FUN_800386d0 | StrPcb_EnsureInstance | singleton lazy init (sizeof 0x70、初期 pos 0x1ff) |
| 0x8003871c | FUN_8003871c | ObjectDtor_FreeField0_871c | dead code、no callers / no xrefs |
| 0x80038778 | FUN_80038778 | Saturate_Double | double clamp 汎用 util (引数順 val/low/high) |
| 0x80038798 | FUN_80038798 | Clamp_Int | int clamp 汎用 util (100+ caller 想定) |
| 0x800387d8 | FUN_800387d8 | StrPcb_RegisterAtExitCleanup | g_strPcbOutBuf cluster の 2 区画分の atexit cleanup 登録 |
| 0x80038824 | FUN_80038824 | ClStrPcb_Dtor | CW C++ ABI 3-level vtable downgrade dtor (派生→中間→基底) |

主要発見:
- **Session 2 訂正**: DAT_806d1010 は strpcb singleton ptr (= g_strPcbInstance)、
  ApplicationRunFlag ではない。StrPcb_Shutdown が +0x8/+0x20/+0x24/+0x4c/+0x68 を
  strpcb state struct として読み書きすることで確定
- **strpcb struct size = 0x70**: StrPcb_EnsureInstance の Alloc(0x70) で確定
- **初期 position 0x1ff = 中央**: 10-bit range 0..0x3ff の中点 (steering wheel home)
- **shutdown sequence**: motor 動作中 (state != 1 && != 4) は state 1 (= init/ready) 達成まで
  最大 0xe10 (3600) frames block してから free (force feedback 暴走防止)
- **CW C++ 3-vtable downgrade**: ClStrPcb_Dtor で vtable を 803f56f0 → 803f5700 → 803f5710
  順に「降ろす」破棄手順を観察 (CW ABI の典型)
- **Saturate_Double / Clamp_Int** は汎用 util、副次調査で 100+ caller 確認予定

副次 rename 候補:
  DAT_806d1010 → g_strPcbInstance
  FUN_80038e30 → ClStrPcb_Inner_Dtor (ClStrPcb_Dtor からの sub-object dispose)
  FUN_80270c30 → AtExit_Register or Singleton_RegisterCleanup
  PTR_803f56f0 / 803f5700 / 803f5710 → ClStrPcb 3-level vtable

### Session 12 完了分 (2026-05-18、16 件) — strpcb low-level setter/getter + ctor

strpcb の field-level setter/getter 群と ctor。状態構造を field 単位で読み書きする
内部 API。マニアックな field 直叩き helper が多いが、StrPcb_Init で全体構造が露呈。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80038050 | FUN_80038050 | StrPcb_ClearStatusBits | +0x24 status byte の bit clear |
| 0x80038060 | FUN_80038060 | StrPcb_SetStatusBits | +0x24 bit set、JVS debounce gate 経路あり |
| 0x800380c8 | FUN_800380c8 | StrPcb_SetTimer3c40 | timer B (intensity-scaled、modulo なし) を arm |
| 0x800380f0 | FUN_800380f0 | StrPcb_SetTimer3034_38 | timer A (intensity-scaled、modulo 付き sign-flipping) を arm |
| 0x8003811c | FUN_8003811c | StrPcb_SetCmdByte2f | cmd byte +0x2f setter |
| 0x8003812c | FUN_8003812c | StrPcb_SetCmdByte2e | cmd byte +0x2e setter |
| 0x8003813c | FUN_8003813c | StrPcb_SetCmdByte2d | cmd byte +0x2d setter |
| 0x8003814c | FUN_8003814c | StrPcb_SetCounterField14 | +0x14 (counter offset) setter |
| 0x8003815c | FUN_8003815c | StrPcb_GetReceivedPosData | +0x44 (H-prefix 16-bit pos) getter |
| 0x80038164 | FUN_80038164 | StrPcb_SetPositionTarget | +0xc を 10-bit mask して set |
| 0x80038178 | FUN_80038178 | StrPcb_IsInErrorState | +0x60 (countdown phase) == 4 判定 |
| 0x8003818c | FUN_8003818c | StrPcb_GetIntensityScale | +0x4 master intensity scale getter |
| 0x80038194 | FUN_80038194 | StrPcb_SetIntensityMode | mode 0..2 で DAT_802e9848 から intensity scale 適用 |
| 0x800381e4 | FUN_800381e4 | StrPcb_BeginEffect | effect id で +0x6c (completion mask) + countdown 開始 |
| 0x80038204 | FUN_80038204 | StrPcb_DrainInputBuffer | ring buffer 残留 byte を 20 cycle で全 drain |
| 0x80038288 | FUN_80038288 | StrPcb_Init | strpcb ctor、boot で 1 回 (serial open + 2ms wait + drain) |

主要発見:
- **StrPcb state struct** layout が StrPcb_Init で大半確定:
  - +0x04: master intensity scale (float, from DAT_802e9848[mode])
  - +0x08: intensity mode (0..2)
  - +0x0c: target position (10-bit)
  - +0x10..+0x13, +0x28..+0x2b, +0x44..+0x47: RGBA 4-byte fields (0,0,1,255 = ?)
  - +0x18: dirty flag (cmd 変更 → 次フレーム送信)
  - +0x24..+0x27: status byte + flags
  - +0x20: state (0=idle, 1=init, 3=running, 4=error)
  - +0x2d/+0x2e/+0x2f: 3-byte command preset (neutral = 0x2d/0x14/0)
  - +0x30..+0x43: timer A/B state (sign-flipping vibration)
  - +0x44: H-prefix received 16-bit pos
  - +0x48: intensity (0..1)
  - +0x4c: intensity delta
  - +0x58..+0x5a: last error code 3 char
  - +0x5c: sticky error flag
  - +0x60: countdown phase (1/2/3/4)
  - +0x64: countdown value
  - +0x68: alloc'd handle (4 byte)
  - +0x6c: effect completion mask byte
- **JVS との関連**: StrPcb_SetStatusBits の bit 4/8 で g_jvsDebounceEnable check
  → status byte には JVS 由来の input も統合される
- **timer 2 種類**: SetTimer3c40 (B、固定 duration) / SetTimer3034_38 (A、sign-flipping vibration)
- **state machine が二重**: +0x20 (overall state、StrPcb_HasError) と +0x60 (countdown phase、
  StrPcb_IsInErrorState) が独立

副次 rename 候補:
  DAT_802e9848 (3-float intensity mode table)
  DAT_802e9838 (4-byte effect completion mask table)
  FUN_802939f0 → SerialPort_Open (推測)
  FUN_802554dc / FUN_802e5380 (boot/comm setup helpers)

### Session 11 完了分 (2026-05-18、11 件) — strpcb (Steering PCB) public API

Session 10 で確定した strpcb subsystem の public-facing API 群。state machine 上で
session 開始/停止、エラー検査、コマンドリセット、blocking sync wait を提供する。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80037874 | FUN_80037874 | StrPcb_TimerTick | timer +0x54 駆動の 60 frame 周期 neutral 再送、phase +0x50 で動作分岐 |
| 0x80037ab0 | FUN_80037ab0 | StrPcb_BeginTimedNeutral | timer 0xe10 + state 3 + neutral 0x2d/0x14 で session 開始 |
| 0x80037b68 | FUN_80037b68 | StrPcb_HasError | E?? (non-E00) or sticky error +0x5c を集約した bool |
| 0x80037b9c | FUN_80037b9c | StrPcb_GetCommErrorFlag | byte +0x26 (10-retry 失敗 flag) getter |
| 0x80037ba4 | FUN_80037ba4 | StrPcb_GetErrorCodeString | 最後の error 3-char code or fallback string |
| 0x80037bd0 | FUN_80037bd0 | StrPcb_ResetCommands_Zero | command bytes を 0/0/0 にリセット (中立コマンド + 任意 intensity reset) |
| 0x80037c44 | FUN_80037c44 | StrPcb_ResetCommands_Neutral | command bytes を 0x2d/0x14/0 にリセット (neutral preset + 任意 intensity reset) |
| 0x80037cc0 | FUN_80037cc0 | StrPcb_WaitForState1 | state == 1 (= init/ready) になるまで block (0xe10 frames timeout) |
| 0x80037e08 | FUN_80037e08 | StrPcb_RunAndWaitIdle | state 3 強制 + state == 0 (idle) になるまで block (1-shot 完了待ち) |
| 0x80037f64 | FUN_80037f64 | StrPcb_ResetCommands_NeutralDefault | neutral preset、param 無し、intensity 維持 |
| 0x80037fb4 | FUN_80037fb4 | StrPcb_ForceRun_Neutral | state 3 強制 + neutral (timer/wait なし) |

主要発見:
- **state machine values 集約**:
  - 0 = idle (動作完了)
  - 1 = init/ready (BeginTimedNeutral で達する目標)
  - 3 = running (Begin 系で強制設定)
  - 4 = error (timeout / retry 限界)
- **command byte preset 2 種類**:
  - Zero (0/0/0): 完全停止
  - Neutral (0x2d/0x14/0): 「中立位置で待機」 (steering wheel の home position)
- **reset variant 3 種類** (param 有無 + intensity reset 有無 で organize)
- **sync wait pattern**: Frame_PostDrawOverlay + Frame_UpdatePerFrameState を毎フレーム
  呼んで GUI を更新しつつ state 変化を待つ (0xe10 = 3600 frames = 60 秒 @60fps timeout)
- **3 つの reset 系 (Zero / Neutral / NeutralDefault)** は CW テンプレートか overload の
  ような系譜だが、param なし / intensity reset 有無で組分けされている

### Session 10 完了分 (2026-05-18、9 件) — strpcb (Steering PCB 通信) subsystem

format string "strpcb result code is %c%c%c" で system 名 strpcb 確定。Triforce arcade
cabinet の steering wheel PCB との 3-char ASCII 制御コード通信。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800350a8 | FUN_800350a8 | Object_ReleaseAnimResource_Impl | obj+0x14 gated chain release worker |
| 0x800369c4 | FUN_800369c4 | QuadFrame_FromPackedCorners | 4-vec3 packed → tangent frame thin wrapper |
| 0x800369f8 | FUN_800369f8 | QuadFrame_FromCornerPtrs | 4-corner quad の right/forward 単位 vec + center-Y 計算 (rsqrt + NR) |
| 0x80036988 | FUN_80036988 | ObjectDtor_Trivial_988 | dead code (no callers / no xrefs) |
| 0x80036e40 | FUN_80036e40 | StrPcb_Dtor | strpcb 系オブジェクトの vtable trivial dtor、13+ 件で fn ptr 渡される |
| 0x80036e7c | FUN_80036e7c | StrPcb_StateTick | state machine countdown + "E20" error trigger + handle query |
| 0x80036ffc | FUN_80036ffc | StrPcb_ParseResponse | 3-char response code "C/E/H" parser |
| 0x800372f4 | FUN_800372f4 | StrPcb_InputTick | response 受信 + 3-byte rolling buffer + timeout 検出 |
| 0x800374f8 | FUN_800374f8 | StrPcb_OutputTick | per-frame 8-byte command output + state/input tick chain |

主要発見:
- **strpcb (Steering PCB) 通信 subsystem 確定**: log format "strpcb result code is %c%c%c" /
  "strpcb_e: result code is %c%c%c" でシステム名特定
- **3-char ASCII 制御プロトコル**:
  - "C01" / "C06" → Ack (state 遷移)
  - "E00".."E2x" → エラーコード (E20 = expected reset)
  - "H<hi><lo>" → handle/position data
- **serial ring buffer 経由**: port 0 を SerialQueue_PushByte (FUN_80293338) /
  PopByte (FUN_8029321c) / AvailableBytes (FUN_80293290) で I/O
- **StrPcb state struct** layout 大半確定 (0x60+ byte、+0x10 = current position,
  +0x20 = state, +0x24 = status byte, +0x48 = intensity, +0x58..0x5a = last error code)
- **QuadFrame_From* 数値計算**: 4-corner quad から tangent/forward unit vec + center-Y、
  rsqrt + 2-step Newton-Raphson 精度向上の典型 (epsilon fallback 付き)
- **trivial dtor の重複**: CW C++ ABI が per-class __dt を生成するため、内容が同一でも
  別 vtable 参照のため別関数として残る (ObjectDtor_Trivial_988 と StrPcb_Dtor は同 body)

副次 rename 候補:
  FUN_80293338 → SerialQueue_PushByte
  FUN_80293290 → SerialQueue_AvailableBytes
  FUN_8029321c → SerialQueue_PopByte
  FUN_80038778 → saturate (float)
  FUN_80038798 → clamp (int)
  FUN_802768b4 → memmove
  DAT_80598500-80598508 → g_strPcbOutBuf
  DAT_806d1000-1004 → g_strPcbRecvBuf / g_strPcbRecvIdx
  DAT_806d100c → g_strPcbLastRecvTick

### Session 9 完了分 (2026-05-18、6 件) — clRom (ROM/asset loader) subsystem

clRom = ROMData / asset loader 系。debug HUD の "clRom: %04d" format string で system 名確定。
双方向リンクトリスト + refcount 管理。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80034a84 | FUN_80034a84 | clRom_GetActiveCount | DAT_806d0fec (active count) getter、debug HUD のみ |
| 0x80034a94 | FUN_80034a94 | clRom_DtorForce | refcount 無視で force dtor + unlink + free |
| 0x80034c38 | FUN_80034c38 | DVDFile_LoadSync | DVD file 同期読み + 32-align alloc (clRom と independent) |
| 0x80034ca0 | FUN_80034ca0 | clRom_DumpListOverlay | debug HUD で全 entry 表示 ("No %02d %02d %s") |
| 0x80034d24 | FUN_80034d24 | clRom_PurgeAll | 全 entry sweep 40+ caller (scene change 用) |
| 0x80034dfc | FUN_80034dfc | clRom_Release | refcount decrement、0 で dtor (Object dtor から呼ばれる) |

主要発見:
- **clRom subsystem 確定**: debug HUD format string s_clRom__04d_802e8e68 で system 名特定
- **double-linked list + refcount**: PTR_806d0fe0 (head) / PTR_806d0fe4 (tail) /
  DAT_806d0fe8 (entry count) / DAT_806d0fec (active count、別 counter)
- **LoaderEntry_Partial layout** (Ghidra struct 既定義):
  - +0x00: refcount
  - +0x08: prev / +0x0c: next
  - +0x10: handle / +0x14: flags (0/1 で 解放経路分岐)
  - +0x18+: path string
- **dtor variant trio**:
  - clRom_DtorForce: refcount 無視 (path-keyed table dispose)
  - clRom_Release: refcount check、0 で free (Object dtor 経路)
  - clRom_PurgeAll: 全 sweep (scene change)
- **DVDFile_LoadSync は clRom と independent**: 32-aligned 1 ショット buffer 確保 + sync read
- 副次 rename 候補 5 件 (DAT_*, FUN_802db2d4/dc964/8007e344/8003b120/8007dfe4)

### Session 8 完了分 (2026-05-18、9 件) — animation binding pair + HSD hierarchy getters

Session 7 で Object_SetAnimBinding (AOBJ slot = obj+0x1c) を rename。本セッションはそのペアの
MTX slot 系 (obj+0x18) と、JObj render forwarder、anim drive helper、HSD hierarchy getter trio。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80034100 | FUN_80034100 | Object_BindMatrixSource | obj[+0x18] (mtx slot) に bind。Object_SetAnimBinding (aobj slot=+0x1c) と alternate |
| 0x80034220 | FUN_80034220 | Object_RenderJObjEx | 3-arg JObj render forwarder (vestigial obj param)、FUN_802cf3b0 へ |
| 0x80034260 | FUN_80034260 | Object_RenderJObjTree | 2-arg wrapper、obj の primary JObj (+0x2c) を auto 取得して render。70+ caller |
| 0x800342a4 | FUN_800342a4 | Object_DriveAnimMatrix | mtx slot or aobj slot を PSMTXCopy で jobj+0x44 に反映 + metric slot 10 |
| 0x800346e4 | FUN_800346e4 | Object_DtorWithGXSync | GX_DrawDoneAndWait 経由の dtor (GPU sync barrier + sub-resource release + free) |
| 0x80034a10 | FUN_80034a10 | Object_ReleaseAnimResource | obj[+0x24] gate で FUN_800350a8 (anim chain release impl) を呼ぶ |
| 0x80034a3c | FUN_80034a3c | JObj_GetNext | HSD JObj.next (jobj+0x08) getter、NULL-safe |
| 0x80034a54 | FUN_80034a54 | JObj_GetChild | HSD JObj.child (jobj+0x10) getter、NULL-safe |
| 0x80034a6c | FUN_80034a6c | DObj_GetNext | HSD DObj.next (dobj+0x04) getter、NULL-safe |

主要発見:
- **mtx/aobj 二重 slot system**: obj+0x18 (mtx) と obj+0x1c (aobj) は alternate slot
  (片方 set でもう片方 0)。両方とも 4x3 matrix ptr を保持し、Object_DriveAnimMatrix が
  priority order (mtx → aobj → default) で読んで PSMTXCopy。
- **HSD JObj layout 追加 field 確定** (clNormal3D_SetFlags 経由):
  - +0x08: next (sibling JObj)
  - +0x10: child (first child JObj)
- **HSD DObj layout 確定**: +0x04: next (sibling DObj)。JObj.next と offset 違うので注意。
- **GX_DrawDoneAndWait barrier dtor** pattern: GPU outstanding が壊れないように
  free 前に必ず sync する Object dtor variant。

### Session 7 完了分 (2026-05-18、5 件) — animation + skin pipeline

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800326d0 | FUN_800326d0 | ObjectTree_BlendQuatLerp | 2 JObj pose を blend (linear pos/scale + quat slerp rot) |
| 0x80032a70 | FUN_80032a70 | Object_SetField8_10_14 | obj+0x8/+0x10/+0x14 一括 setter |
| 0x800331e0 | FUN_800331e0 | Object_DriveAnimAndSkin | animation drive + skinning pipeline core (metric 8/0xb/7) |
| 0x80033f10 | FUN_80033f10 | Object_ClearAnimBinding | anim binding clear (obj+0x3c) |
| 0x80033fe0 | FUN_80033fe0 | Object_SetAnimBinding | anim binding set (obj+0x3c) |

### Session 6 完了分 (2026-05-18、8 件) — JObj getters + pose blend tree

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800320e0 | FUN_800320e0 | Object_GetJObjPositionVec | translate getter (assert "translate") |
| 0x80032188 | FUN_80032188 | Object_GetJObjLocalMatrix | jobj+0x44 ptr (dirty recalc) |
| 0x80032234 | FUN_80032234 | Object_SetField14_IfValid | obj+0x14 float setter (用途未特定) |
| 0x8003227c | FUN_8003227c | Object_SetField8_AndDirty | obj+0x8 float setter + dirty marker |
| 0x800322cc | FUN_800322cc | Object_GetField8 | obj+0x8 float getter |
| 0x800322f8 | FUN_800322f8 | ObjectTree_BlendOrCopy | pose blend/copy 再帰 walker |
| 0x80032540 | FUN_80032540 | ObjectTree_BlendOrCopy_Timed | wrapper + metric slot 9 |
| 0x8003267c | FUN_8003267c | Object_CopyFieldsRotPosScale | 単 node の transform copy helper |

## 累計 (Session 1-27)

合計 **263 件処理** (rename ~255、諦め ~8) / 1500 件 ≒ **17.5%**

主要発見:
- mkgp2 universal base class **ObjectBase** (vtable @ 0x803f5658)、CW C++ ABI 的 dtor chain。
- **FlowDispatcher** (singleton @ DAT_806d0f80、0x38 byte struct) で scene state machine を管理。Flow_TransitionTo / FlowDispatcher_Create / FlowDispatcher_Dtor の trio。
- **MainGameLoop** の per-frame: Frame_Begin → Scene_Draw → Frame_PostDraw_BackupBuffer → Frame_PostDrawOverlay → Vtable_CallSlot2 → Flow_TransitionTo の cycle。
- **Service / VBlank latch** system (Enable + Value 2 byte ペア × 2)、boot で init、ServiceButton_Handler / PCBComm が consume。
- **HSD JObj** struct layout 確定 (assert "scale" / "translate" 経由):
  - +0x1c..0x24: rotation Euler XYZ
  - +0x2c..0x34: scale XYZ
  - +0x38..0x40: position/translate XYZ
- **CObj** (Camera Object、cobj.c) の projection / matrix cache / line buffer / render pass dispatcher。
- **ScopedTimer + MetricsTable** (g_metricsTable[0..0x2f]) で per-feature timing 計測 (draw=slot 2、run=slot 6、frame=slot 0、backup buffer=slot 0x15、pose blend=slot 9)。
- "Dead instrumentation" cluster (DAT_806d0fb8 / DAT_805940ec / DAT_806ced48): write-only globals、release build で消された debug counter の残骸。

### Session 5 完了分 (2026-05-18、8 件 + 訂正 2 件) — JObj position/rotation getters/setters

assert "translate" (FUN_80031f10、jobj.h:0x3aa) で確定: HSD JObj layout
- jobj+0x1c..0x24: rotation Euler XYZ
- jobj+0x2c..0x34: scale XYZ
- jobj+0x38..0x40: position/translate XYZ
- jobj+0x14: flags (0x2000000 = skip dirty, 0x800000 = no propagate, 0x40 = ?)

訂正 (Session 4 で position と命名したが scale だった):
- JObj_SetPosition (0x8003151c) → JObj_SetScale
- Object_SetJObjPositionXYZ (0x80031ab4) → Object_SetJObjScaleXYZ

Session 5 完了:
| Address | 旧名 | 新名 |
|---|---|---|
| 0x80031cf8 | FUN_80031cf8 | Object_SetJObjPositionY |
| 0x80031da0 | FUN_80031da0 | Object_SetJObjPositionX |
| 0x80031e48 | FUN_80031e48 | Object_SetJObjPositionXYZ |
| 0x80031f10 | FUN_80031f10 | Object_SetJObjPositionVec (assert "translate") |
| 0x80031fe8 | FUN_80031fe8 | Object_GetJObjRotationZ |
| 0x80032038 | FUN_80032038 | Object_GetJObjRotationY |
| 0x80032088 | FUN_80032088 | Object_GetJObjRotationX |
| 0x800320d8 | FUN_800320d8 | Object_SetByte48 (obj+0x48、用途未特定) |

### Session 4 完了分 (2026-05-18、12 件) — JObj forwarding setters

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80031408 | FUN_80031408 | Object_SetColorRGBA | RGBA + dirty (obj[0x4c..0x58]) |
| 0x80031488 | FUN_80031488 | Object_SetColorAlphaOnly | RGB 固定 + A 引数 |
| 0x8003151c | FUN_8003151c | JObj_SetPosition | 直接 JObj 3 float setter |
| 0x80031718 | FUN_80031718 | Object_JObjUpdate_be4 | JObj non-NULL で FUN_802d0be4 |
| 0x80031744 | FUN_80031744 | Object_JObjUpdate_870 | JObj non-NULL で FUN_802d0870 |
| 0x80031770 | FUN_80031770 | JObj_SetRotationQuat | jobj+0x1c..0x28 quat 候補 |
| 0x8003185c | FUN_8003185c | Object_SetJObjRotationZ | jobj+0x24 Euler Z |
| 0x80031924 | FUN_80031924 | Object_SetJObjRotationY | jobj+0x20 Euler Y |
| 0x800319ec | FUN_800319ec | Object_SetJObjRotationX | jobj+0x1c Euler X |
| 0x80031ab4 | FUN_80031ab4 | Object_SetJObjPositionXYZ | jobj+0x2c..0x34 scalar 3 |
| 0x80031b7c | FUN_80031b7c | Object_SetJObjScaleVec | 同 offset、assert "scale" |
| 0x80031c50 | FUN_80031c50 | Object_SetJObjField40 | jobj+0x40 1 float |

### Session 3 完了分 (2026-05-18、19 件)

CObj (Camera Object、cobj.c assert 経由で確証) と関連 helper 群。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80030008 | FUN_80030008 | CObj_GetLineBufferPtr | cobj + 0x3034 ptr getter |
| 0x80030010 | FUN_80030010 | CObj_UpdatePerspParam | persp param update + debug line buffer |
| 0x800300d8 | FUN_800300d8 | CObj_ProjectPoint | world → screen project |
| 0x80030200 | FUN_80030200 | CObj_UnprojectPoint | screen → world unproject |
| 0x800303e8 | FUN_800303e8 | CObj_ApplyViewport | GX viewport apply (FUN_802c72ac) |
| 0x80030424 | FUN_80030424 | CObj_LoadIntoGX | GX projection load (FUN_802c7384) |
| 0x80030460 | FUN_80030460 | CObj_SetWorldMatrix | world mtx load + dirty flag |
| 0x800304e0 | FUN_800304e0 | CObj_LinePath_Step | debug line trace point add (line buffer +0x34) |
| 0x80030960 | FUN_80030960 | CObj_ApplyScissor | scissor apply (確証中) |
| 0x8003099c | FUN_8003099c | CObj_LoadProjMatrix | proj matrix load (確証中) |
| 0x800309d8 | FUN_800309d8 | CObj_GetProjMatrix_Cached | cobj+0x88 lazy alloc |
| 0x80030a10 | FUN_80030a10 | CObj_GetViewMatrix_Cached | view mtx (FUN_802c7e30) |
| 0x80030a48 | FUN_80030a48 | CObj_GlobalProjPushFrame | projection global init |
| 0x80030a68 | FUN_80030a68 | CObj_DebugVizPathFlush | LObj update + line buffer flush |
| 0x80030aa8 | FUN_80030aa8 | CObj_RenderPass_Execute | render pass dispatcher |
| 0x80030aec | FUN_80030aec | Stub_NoOp_80030aec | empty function |
| 0x80030af0 | FUN_80030af0 | Object_DtorTree_56b8 | dtor + refcounted sub-resource release |
| 0x80030d24 | FUN_80030d24 | LinkedNode_GetField10_OrFallback | linked node fallback getter |
| 0x80030d48 | FUN_80030d48 | JObjTree_TranslateAndMul | HSD JObj tree mtx walker (PSMTXTrans + Concat) |

### Session 2 完了分 (2026-05-18、合計 22 件)

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8002bca0 | FUN_8002bca0 | Flow_TransitionTo | scene transition |
| 0x8002c554 | FUN_8002c554 | FlowDispatcher_Dtor | flow ctx dtor |
| 0x8002cb80 | FUN_8002cb80 | FlowDispatcher_Create | flow ctx singleton ctor |
| 0x8002ccd8 | FUN_8002ccd8 | ScopedTimer_End | timer/metrics accumulator |
| 0x8002cd7c | FUN_8002cd7c | WrapInRange | bidirectional clamp/wrap |
| 0x8002cd9c | FUN_8002cd9c | Scene_Draw | scene vtable slot 3 thunk |
| 0x8002cdf4 | FUN_8002cdf4 | ObjectBase_Dtor | mkgp2 object hierarchy 最上位 dtor |
| 0x8002cfb8 | FUN_8002cfb8 | (諦め) | 4-byte global setter、用途不明 |
| 0x8002cfd0 | FUN_8002cfd0 | BootConfigFlag_Get | boot config flag (DAT_806d0fa0) |
| 0x8002cfd8 | FUN_8002cfd8 | ServiceLatch_CheckTriggered | service latch trigger check |
| 0x8002d004 | FUN_8002d004 | VBlankValue_Set | VBlank latch value setter |
| 0x8002d018 | FUN_8002d018 | ServiceLatchAux97_Set | service latch aux byte (用途未特定) |
| 0x8002d020 | FUN_8002d020 | VBlankEnable_Set | VBlank latch enable setter |
| 0x8002d038 | FUN_8002d038 | VBlankValue_Get | VBlank latch value getter |
| 0x8002d048 | FUN_8002d048 | ServiceValue_Set | service latch value setter |
| 0x8002d064 | FUN_8002d064 | ServiceLatchAux96_Set | service latch aux byte (用途未特定) |
| 0x8002d06c | FUN_8002d06c | ServiceEnable_Set | service latch enable setter |
| 0x8002d084 | FUN_8002d084 | ServiceValue_Get | service latch value getter |
| 0x8002d08c | FUN_8002d08c | ServiceEnable_Get | service latch enable getter |
| 0x8002d094 | FUN_8002d094 | BootStateStruct_Get | boot state struct ptr (DAT_80594080) |
| 0x8002d0a0 | FUN_8002d0a0 | ServiceButtonExtra_Get | service button extra byte getter |
| 0x8002d0a8 | FUN_8002d0a8 | ServiceButtonExtra_Set | service button extra byte setter |
| 0x8002d760 | FUN_8002d760 | BackupBuffer_InitAsTexture | 640x480 framebuffer as GX texture |
| 0x8002d7dc | FUN_8002d7dc | Frame_UpdatePerFrameState | per-frame input + state update |
| 0x8002d978 | FUN_8002d978 | Frame_PostDrawOverlay | frame end overlay + GX done |
| 0x8002da3c | FUN_8002da3c | Frame_PostDraw_BackupBuffer | post-draw EFB copy to backup texture |
| 0x8002dc3c | FUN_8002dc3c | Frame_Begin | per-frame GX setup |
| 0x8002dc7c | FUN_8002dc7c | Game_Shutdown | top-level cleanup |
| 0x8002f640 | FUN_8002f640 | BootStateStruct_RefreshIfDue | rate-limited BootInfo reload |
| 0x8002f8d4 | FUN_8002f8d4 | DebugLog_LvIdMsg | "lv %d id %d msg %s" log helper |
| 0x8002f910 | FUN_8002f910 | (dead code) | no callers found, dead instrumentation 候補 |
| 0x8002f978 | FUN_8002f978 | Object_DtorMinimal | trivial dtor (NULL check + free) |
| 0x8002f9b4 | FUN_8002f9b4 | SeqMenuScene_DrawDebugList | debug menu overlay |
| 0x8002fa8c | FUN_8002fa8c | SeqMenuScene_HandleInput | debug menu input |
| 0x8002fc20 | FUN_8002fc20 | SeqMenuScene_Dtor | debug menu dtor |
| 0x8002fc80 | FUN_8002fc80 | SeqMenuScene_Init | debug menu ctor (0x14 byte) |
| 0x8002ff08 | FUN_8002ff08 | DeadCounter_Set_ced48 | dead instrumentation (no readers) |
| 0x8002ff10 | FUN_8002ff10 | DeadCounter_Reset_805940ec | dead instrumentation (no readers) |
| 0x8002ff20 | FUN_8002ff20 | DeadCounter_Set_806d0fb8 | dead instrumentation (no readers) |
| 0x8002ff30 | FUN_8002ff30 | DeadCounters_PostBootReset | dead counter reset |
| 0x8002ff48 | FUN_8002ff48 | (保留) | FUN_80270c30 callback register pattern、要確認 |
| 0x8002ff7c | FUN_8002ff7c | Cleanup_DtorMinimal | minimal dtor (Object_DtorMinimal の duplicate) |
| 0x8002fffc | FUN_8002fffc | Object_SetByte10_Return1 | obj+10 byte setter (return 1) |

### Session 2 副次 rename 候補 (deferred)

| 副次 addr | 候補名 | 由来 |
|---|---|---|
| DAT_806d0f80 | g_FlowDispatcher | FlowDispatcher_Create singleton |
| DAT_806d0f94 | g_ServiceEnable | service latch enable |
| DAT_806d0f95 | g_VBlankEnable | VBlank latch enable |
| DAT_806d0f98 | g_ServiceValue | service latch value |
| DAT_806d0f99 | g_VBlankValue | VBlank latch value |
| DAT_806d0fa0 | g_BootConfigFlag | BootConfigFlag_Get の対象 |
| DAT_806d2264 | g_HeapStatsTag | HeapStats_DumpForTag のターゲット |
| DAT_806d2268 | g_ZeroFloat | float 0.0 リテラル |
| DAT_806d2280 | g_TimerScale | ScopedTimer 経路の divisor |
| DAT_806d2288 | g_TimerOffsetBaseline | ScopedTimer 経路の baseline |
| DAT_80594080 | g_BootStateStruct | BootDispatcher 用 state struct |
| 0x803f5658 | g_ObjectBaseVtable | ObjectBase の vtable |
| FUN_80039cf4 | MetricsTable_Accumulate | g_metricsTable[i] += val |
| FUN_8003b2d8/2e0/2e8 | SceneTransitionCounter_* | Flow_TransitionTo の cleanup |
| FUN_8002fc80 | MainMenuScene_Init | FlowDispatcher_Create の sub-init |
| FUN_80121210 | FlowDispatcher_Cleanup | scene transition cleanup helper |
| FUN_8002dc3c | Frame_Begin | per-frame GX state setup (MainGameLoop) |
| FUN_8002da3c | Frame_PostDraw | per-frame post-process (MainGameLoop) |
| FUN_80038574 | ApplicationRunFlag_Get | DAT_806d1010 getter |

### Session 1 完了分 (2026-05-18)

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80003140 | FUN_80003140 | BootMode4Pending_Set | boot flag setter |
| 0x8000314c | FUN_8000314c | BootMode4Pending_Get | boot flag getter |
| 0x80003154 | FUN_80003154 | __start | CW boot entry |
| 0x800032b0 | FUN_800032b0 | __init_registers | CW Boot.c |
| 0x80003340 | FUN_80003340 | __init_data | CW Boot.c (.data copy + .bss clear) |
| 0x80003400 | FUN_80003400 | __init_user | libc/cpp/user init wrapper |
| 0x80003424 | FUN_80003424 | __flush_cache | dcbst/icbi loop |
| 0x80003458 | FUN_80003458 | memset_returning_dst | memset wrapper (returns dst) |
| 0x80003488 | FUN_80003488 | memcpy_bytewise | byte forward memcpy |
| 0x800053e0 | FUN_800053e0 | OSResetSystem_NoArgs | OSResetSystem(0,0,0) wrapper (debug cmd reset) |
| 0x8000543c | FUN_8000543c | memset | CW SDK 標準実装 (98 callers) |

### Session 1 副次 rename 候補 (deferred)

副次 rename はスコープ管理のため deferred。各関数の plate に注記済。後セッションで本体 rename 時に同時に処理。

| 副次 addr | 候補名 | 由来 |
|---|---|---|
| 0x8025aaf0 | OSResetSystem | s_OSResetSystem____You_can_t_speci_804f3688 error string 経由で確定 |
| 0x8026d9c4 | memset_impl_dup | FUN_8000543c (memset) と完全同コードの duplicate |
| 0x80256418 | __init_libc | __init_user 内で 1st 呼出 |
| 0x802559e0 | __init_cpp | __init_user 内で 2nd 呼出 (static ctor table?) |
| 0x80257258 | __init_user_subsystems | __init_user 内で 3rd 呼出 |
| 0x8026f82c | __OSDBExceptionHandler_Install? | __start で boot type 2/3 経路、MSR clear-EE |
| 0x8026f8c4 | __OSDBExceptionHandler_Install2? | __start で boot type 4 経路、MSR set-EE |
| 0x80273398 | __exit? | __start 末尾の post-game cleanup |
| 0x8026c060 | DebugCommand_Dispatch | "Dispatch command 0x%08x" log、switch dispatch |

## 諦めリスト (rename 不能、理由付き)

| Address | 旧名 | 理由 |
|---|---|---|
| 0x80003100 | FUN_80003100 | DAT_800030e4 の 0xEEF mask 一致時に OSResetSystem(0,0,0) を呼ぶ boot helper。caller は FUN_80003154 (boot entry)。0xEEF mask の意味 (どの boot stage flag を要求) が未特定で関数名決定不能。plate に詳細記録済。callee の FUN_8025aaf0 = OSResetSystem は副次のため deferred |
| 0x8002cfb8 | FUN_8002cfb8 | 4 byte global setter (DAT_806ced34..37)、ServiceMenu_Init で (0,0,0,0) で zero clear + 14 件の caller (card 系 / FUN_8014x など) で値を書き込まれる。4 byte の意味 (RGBA color? flag tetrad? communication header?) が確定できず |
| 0x8002f910 | FUN_8002f910 | **dead code** (caller なし)。ServiceMenu instance 用 dtor のテンプレート展開 残骸と推測 |
| 0x8002ff48 | FUN_8002ff48 | `FUN_80270c30(&DAT_806d0fb0, Cleanup_DtorMinimal, &DAT_805940e0)` callback register pattern。FUN_80270c30 の用途確定前のため暫定保留 |

## セッション単位の commit ログ

(セッション終了時に追記)
