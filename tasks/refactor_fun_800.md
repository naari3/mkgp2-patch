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
- 最後に処理した address: 0x80033fe0 (Object_SetAnimBinding rename 完)
- 次セッション開始点: 0x80034100

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

## 累計 (Session 1-6)

合計 **113 件処理** (rename ~107、諦め ~6) / 1500 件 ≒ **7.5%**

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
