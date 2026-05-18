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
- 最後に処理した address: 0x8002d0a8 (ServiceButtonExtra_Set rename 完)
- 次セッション開始点: 0x8002d760

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

## セッション単位の commit ログ

(セッション終了時に追記)
