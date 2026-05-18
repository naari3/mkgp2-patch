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
- 最後に処理した address: 0x8005bb0c (EffectSteering_InitStandard_t3 rename 完)
- 次セッション開始点: 0x8005bbf0 以降 (= t2/t1 variants 候補)

### Session 51 完了分 (2026-05-18、7 件) — EffectSteering Init t3/t5/t6/t8 + 2 Reset + sub-state setter

| Address | 新名 | 用途 |
|---|---|---|
| 0x8005b490 | EffectSteering_InitStandard_t8 | type 8 init (+0x3c sub-state、direct float store) |
| 0x8005b628 | EffectSteering_InitStandard_t6 | type 6 init (+0x34 sub-state、3 sub-fields + ramp re-arm) |
| 0x8005b880 | EffectSteering_ResetSelfAndKartItem | full reset (+0x08/0x0c/0x24 clear) |
| 0x8005b8cc | EffectSteering_InitStandard_t5 | type 5 init (+0x30 sub-state、StrPcb Cmd2d/2e force-feedback drive) |
| 0x8005bab0 | EffectSteering_ResetSelfAndKartItem_v2 | reset variant 2 (+0x0c/0x10 のみ clear) |
| 0x8005bb00 | EffectSteering_SetSubStateField38_C | *(self+0x38)+0xc = value setter |
| 0x8005bb0c | EffectSteering_InitStandard_t3 | type 3 init (+0x2c sub-state、5 float setter) |

主要発見:
- **clEffectSteering t1-t9 family 確証**: 同じ switch table + ramp accumulator pattern を
  9 つの type 個別に展開、各 type で:
  - +0x1c に type 番号設定 (1..9)
  - sub-state slot (case 1→+0x24, 2/4→+0x28, 3→+0x2c, 5→+0x30, 6→+0x34, 7→+0x38, 8→+0x3c, 9→+0x40)
  - type 別 sub-state setup (vtbl[3] call / 直接 float store / sub-field setter)
- **EffectSteering Reset には 2 variant**: full (self+0x880) と partial (self+0xab0)。
  full は self+0x08/0x0c/0x24 全 clear、partial は self+0x0c/0x10 のみ。両方 parent
  KartItem を ResetStrPcbToIdle + +0x48 = 0 する。
- **t5 = StrPcb force-feedback 経路**: KartItem_SetStrPcbCmd2dFromFloat / Cmd2e で
  steering 力覚を更新、scale は StrPcb_GetIntensityScale (player カート設定) × 定数。
- **t6 = ramp re-arm pattern**: 完了状態の ramp なら新 target = old/2、direction reverse。
- **t3 = 5-float setup**: sub-state[+0x08/0x0c/0x10/0x14/0x18] を一度に書く。CW param
  register coalescing で v2/v3 の順序逆転 artifact あり。

副次 rename 候補:
  FLOAT_806d2984 / 2988 → STEERING_CMD2D_BASE / STEERING_CMD2E_BASE
  FLOAT_806d2980 → STEERING_RAMP_HALF_SCALE
  StrPcb_GetIntensityScale (既存 named): player カート個別の force-feedback gain

### Session 50 完了分 (2026-05-18、5 件) — EffectState / EffectSpeed / EffectSteering core API

| Address | 新名 | 用途 |
|---|---|---|
| 0x8005b118 | EffectState_ReleaseAndClear | vtbl[3] で sub-state 解放 + slot clear (9 caller in KartItem hit/fall) |
| 0x8005b168 | EffectState_HasContent | +0x1c != 0 branchless predicate |
| 0x8005b17c | EffectSpeed_TickAndGet | 0x28 byte EffectSpeed の per-frame ramp + read (CarObject_MainUpdate から) |
| 0x8005b288 | EffectSteering_InitStandard_t9 | clEffectSteering::initStandard_t (debug str 確証) — type 9 effect 起動 |
| 0x8005b43c | EffectSteering_ResetKartItemAndClear | KartItem_ResetStrPcbToIdle + state clear |

主要発見:
- **clEffectSteering クラス確定** (debug string `s_clEffectSteering__initStandard_t_802edd98`)。
  - 0x44+ byte struct、+0x04 parent、+0x08..+0x18 ramp accumulator、+0x1c effect type (1..9)
  - +0x20 = 選択された sub-state ptr、+0x24..+0x40 = 8 sub-state slot ptrs
  - InitStandard_t9 は switch table を common dispatcher として再利用 (= t1-t8 variant も
    同パターンで存在する可能性。SET +0x1c THEN switch +0x1c で具体化)
- **EffectSpeed (0x28 byte) の Tick semantics 確定**: 整数 ramp accumulator
  (+0x14 target, +0x18 current, +0x1c step) + child sub-state ptr (+0x20 vtbl-driven).
  Tick で int ramp、clamp、return float at +0x44 or source fallback at parent+0x9c。
- **EffectState の release pattern**: 9 caller (KartItem/CarObject hit) が同じ
  EffectState_ReleaseAndClear (0x8005b118) を call — generic release helper。

副次 rename 候補:
  FLOAT_806d2978 → STEERING_RAMP_SCALE (= e.g. 60.0)
  FLOAT_806d297c → STEERING_RESET_VALUE
  KartItem_ResetStrPcbToIdle (既存 named) との連携: EffectSteering 終了で SteeringPcb を idle へ
  EffectSteering の t1-t8 variant が adjacent address に存在する可能性 (要 scan)

### Session 49 完了分 (2026-05-18、12 件) — KartItemAudio Throw/HitConfirm + InputCmd gesture detector

| Address | 新名 | 用途 |
|---|---|---|
| 0x8005a314 | KartItemAudio_PlayHitConfirmSE | itemId 別 victim hit SE pair (0x63 + 0x8a 等) |
| 0x8005a638 | KartItemAudio_PlayThrowConfirmSE | itemId 別 throw SE (0x64/0xa1/0xc6/0xc7/0xa4) |
| 0x8005aa04 | InputCmd_DetectGesturePattern | ring buffer + 3-type pattern FSM (steering wheel cmd 検知) |
| 0x8005ae74 | InputCmd_TickAndDetectAndClear | 毎フレーム detect + cooldown + buffer clear |
| 0x8005af4c | InputCmd_PushSample | head に 4-float sample push (full なら tail++) |
| 0x8005afe0 | InputCmd_Dtor | buffer free (-0x10 offset = alloc header) + self free |
| 0x8005b040 | InputCmd_Init | capacity 渡しの ctor (60-entry buffer alloc + 5-arg placement-new) |
| 0x8005b0ec | InputCmd_SetCooldown | +0x2c cooldown setter |
| 0x8005b0f4 | InputCmd_GetDetectedFlag | +0x28 byte (detected this frame) getter |
| 0x8005b0fc | InputCmd_GetGlobalConfig | DAT_806cee50 (global config) getter |
| 0x8005b104 | InputCmd_SetGlobalConfig | DAT_806cee50 setter |

主要発見:
- **InputCmd (= ステアリングホイール gesture detector)**:
  - 60-entry × 0x14 byte ring buffer (head+0x24 / tail+0x20)
  - 各 entry: float[4] sample + valid byte
  - 3 pattern type (state[+0x14] = 0/1/2) で異なる "左→右" gesture を検知
  - Detect 成功で +0x28 = 1 frame flag set、buffer flush、cooldown +0x2c 設定
  - `s_cmd_cpp_806d2968` = "cmd.cpp" debug label で StrPcb 系である可能性大
- **KartItemAudio 4 dispatcher family が完成**:
  - PlaySEByItemId (0x8005982c, Session 48) — initial play
  - StopSEByItemId (0x8005a140, Session 48) — stop matching SE
  - PlayHitConfirmSE (0x8005a314) — victim hit confirm (0x63+0x8a 等)
  - PlayThrowConfirmSE (0x8005a638) — thrower throw confirm (0x64/a1/c6/c7/a4)
  全 4 つが ItemAlias_DestToSource canonicalization 経由で itemId を解決。
- **DAT_806cee50 global** は InputCmd 共有の config / state pointer。複数インスタンスが
  共有する設計。

副次 rename 候補:
  DAT_806cee50 → g_InputCmdGlobalConfig
  FUN_80270f20 → ArrayPlacementNew (5-arg = (ptr, ctor, ?, stride, count))

### Session 48 完了分 (2026-05-18、8 件) — KartAudioChannel SE 制御 + KartItemAudio dispatcher

| Address | 新名 | 用途 |
|---|---|---|
| 0x800595b4 | KartAudioChannel_Set24WithSeStop | +0x24 state 変更時の SE 0x51/0x52 stop |
| 0x80059644 | KartAudioChannel_SetIntensity | +0x10 を [0, 0.1] (= 806d2940/2954) にサチュレート |
| 0x8005967c | KartAudioChannel_PlayColorMatchSE | 2-color key で 13-entry SE 表を検索 + 3D pan |
| 0x8005982c | KartItemAudio_PlaySEByItemId | itemId 巨大 dispatcher (+0x3/0xc/0xd/0xe/0xf/0x10 offset で SE 選択) |
| 0x80059f70 | KartAudioChannel_StopSE8a | SE 0x8a 停止 + 任意 slot clear |
| 0x80059fd4 | KartAudioChannel_PlayJumpSE | jumpType 0/1/2 → SE 0x8b/0x8c/0x8a |
| 0x8005a0bc | IsAudioMutedItem | 6 muted item ID predicate (DAT_802edca4..edcb8) |
| 0x8005a140 | KartItemAudio_StopSEByItemId | itemId dispatcher for SE stop (counterpart of 0x8005982c) |

主要発見:
- **KartAudioChannel の SE 階層** (channel = (+0x8 & 0xf) << 0x1b):
  - SE 0x50/0x51/0x52: base SEs (idle/state0/state1)、Dtor で stop
  - SE 0x8a/0x8b/0x8c: jump/landing SEs (SetJump で trigger)、+0x30 で tracking
  - SE 0xa2: itemId 0x19 専用 (PlaySEByItemId / StopSEByItemId で別経路 = "interrupt with different SE")
  - SE 0xc5, 0xcf: player only (StopSEByItemId で itemId 0x21/0x98)
  - 13-entry color-match SE table at DAT_802edcc0..edd34 (PlayColorMatchSE で dispatched)
- **KartItemAudio の per-itemId SE offset system**: each kart variant has a base SE id
  at DAT_802edc70[charId * 4]、itemId category で +0x3/0xc..0x10 offset を加算して具体 SE
  を選択。Play (0x8005982c) と Stop (0x8005a140) が対称構造。
- **ItemAlias_DestToSource(itemId & 0xff, scratchBuf)** で別名 itemId を canonical に
  解決 (< 0x115 の itemId のみ)。alias 経由でも同じ SE 選択を保証する設計。

副次 rename 候補:
  FUN_8016c4cc / FUN_8016c488 → AudioSystem_StopAllSE / SuppressGlobalSE
  FUN_8016c288 → AudioSystem_Set3DPan
  DAT_802edcbc / edcc0 + 13-entry → KartColorMatchSE_Table
  DAT_802edc70 + 4-stride → KartBaseSE_PerCharIdTable

### Session 47 完了分 (2026-05-18、8 件) — TornadoEffect itemId dispatcher 3 個 + KartAudioChannel

| Address | 新名 | 用途 |
|---|---|---|
| 0x80058510 | TornadoEffect_SetField128AndMaybeClear134 | overlay mode setter (+0x128) |
| 0x80058534 | TornadoEffect_ApplyItemVisual_Primary | 巨大 if-else dispatcher (item ID → wheel mode / overlay mode) |
| 0x80058994 | TornadoEffect_ApplyItemVisual_Secondary | +0x150 tracking variant (= secondary item slot) |
| 0x80058c80 | TornadoEffect_ApplyItemVisual_Compact | switch-table variant (item ID 0xc-0x28 focused) |
| 0x80058ddc | TornadoEffect_TriggerField140 | +0x140 = 2 (2-frame pulse) |
| 0x80058de8 | AudioChannel_DtorWithSeStop | 3 base SE (0x50/51/52) + 2 dynamic stop + free |
| 0x80058ea4 | KartAudioChannel_Init | 0x38 byte audio channel ctor |
| 0x80058ef4 | KartAudioChannel_ResetSEsAndSetFlag | 3 base SE stop + isPlayer flag overwrite |

主要発見:
- **TornadoEffect の itemId → visual mode dispatch は 3 経路** (Primary/Secondary/Compact)、それぞれ +0x14c または +0x150 を tracking slot として使い分け。Primary は全 item range を if-else でカバー、Secondary は cleanup tail として呼ばれ 0x4f に position-snap branch を持つ、Compact は switch-table で 0xc..0x28 を focused 処理。
- **`countLeadingZeros(prev - new)` idiom** で itemId 変化判定 (= same:-1, different:>=0)。CW の `if (prev != new)` の典型展開。
- **KartAudioChannel (0x38 byte)** は CarObject の audio sub-object。3 base SE (0x50/51/52) + 2 dynamic slot (+0x30/+0x34)、channel id encoding = (id & 0xf) << 0x1b。

副次 rename 候補:
  FUN_8016c394 → AudioSystem_StopSE (4-bit channel + SE id cookie)
  FLOAT_806d2940 → DEFAULT_VOL_PAN baseline

### Session 46 完了分 (2026-05-18、15 件) — TornadoEffect 系 small mutator + 1 matrix composer

| Address | 新名 | 用途 |
|---|---|---|
| 0x800576d8 | TornadoEffect_TriggerFlagD2WithTimer | +0xd2=1, +0x13c=15 timer |
| 0x80057738 | TornadoEffect_SetColorPairC8CC | +0xc8/+0xcc を FUN_800a525c で convert |
| 0x8005778c | TornadoEffect_ClearFlagC0AndSetC4 | +0xc0==1 なら +0xc4=value、+0xc0=0 |
| 0x800577a8 | TornadoEffect_SetField11c | 単純 float setter |
| 0x800577b0 | TornadoEffect_SetFlagB4 | +0xb4 flag + value==0 で +0x11c clear |
| 0x800577c8 | TornadoEffect_SetFlagC0 | +0xc0=1 + FUN_800b53ec disable |
| 0x800577fc | TornadoEffect_SetScalePairB0BC | +0xb0/+0xbc を [0,1] にクランプ |
| 0x800578c4 | TornadoEffect_SetMatrixRow0_5c | 3 float to +0x5c..+0x64 |
| 0x800578d4 | TornadoEffect_SetFieldAc | extent factor setter |
| 0x800578dc | TornadoEffect_SetMatrix1c | 16 float bulk copy to +0x1c..+0x58 |
| 0x800579d8 | TornadoEffect_SetColorY | mode 1 + 6 float (RGB×2) で X/Z=1, Y=value |
| 0x80057a00 | TornadoEffect_GetField114 | +0x114 getter (cos/sin angle) |
| 0x80057a08 | TornadoEffect_SetField114 | +0x118=prev, +0x114=new |
| 0x80057a18 | TornadoEffect_TriggerWheelScaleAnim | mode 1 wheel scale lerp trigger |
| 0x80057a4c | TornadoEffect_SetColorRGBLerp | mode 3 (sync lerp) or immediate |
| 0x80057a8c | TornadoEffect_ComposeRenderMatrix | 4x4 matrix compose + drift offset bake |

主要発見:
- **TornadoEffect の external API 表面が判明**: Tick (0x80056fc0) は内部状態を読むだけ
  で、外部からの mutation は 14 つの small setter (+ 1 trigger + 1 getter) で制御される。
  ResourceHolder (+0x10) への副作用は SetColorPairC8CC, TriggerFlagD2WithTimer,
  SetFlagC0 のような状態切替系のみ。
- **3 つの sub-FSM が同居**:
  - wheel scale FSM (state[+0x104] mode 0/1/2、TriggerWheelScaleAnim で mode 1 起動)
  - color FSM (state[+0xd4] mode 0/1/2/3、SetColorY で mode 1、SetColorRGBLerp で mode 3)
  - pulse FSM (state[+0xc0]/[+0xd2]、SetFlagC0/ClearFlagC0AndSetC4/TriggerFlagD2WithTimer)
- **TornadoEffect_ComposeRenderMatrix** (0x80057a8c) は per-frame render の前段で:
  outMatrix = scale_matrix × baseMatrix × roll_matrix(drift_angle)
  + KartCarPhysics+0x30/0x34/0x38 (drift offset を XYZ translation 列に加算)。
  saturate(+0x120, FLOAT_806d2908, FLOAT_806d290c) × DOUBLE_806d2918 が drift roll angle。
- **+0x114/+0x118** は cos/sin angle の差分追跡 pair: 前 frame の値を +0x118 に snapshot、
  新値を +0x114 に書く。ComposeRenderMatrix で sin(+0x114) を見て roll boost を modulate。

副次 rename 候補:
  FUN_800a525c / FUN_800a51f8 → ColorConverter_Set / ColorConverter_Get (RGBA encoding helper pair)
  FUN_800b53cc → KartResourceHolder_SetPulseFlag
  FUN_800b53dc → KartResourceHolder_SetFlag300
  FUN_800b53ec → KartResourceHolder_SetColorPair
  FUN_800b53bc → KartResourceHolder_Pulse
  FUN_800b5400 → KartResourceHolder_SetScalePair

### Session 45 完了分 (2026-05-18、6 件) — VisualEffectHolder_Dtor + TornadoEffect 系完結

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80056c4c | FUN_80056c4c | VisualEffectHolder_Dtor | 17 caller の generic dtor (Resource[19] slot release + Object_DtorWithGXSync) |
| 0x80056cb0 | FUN_80056cb0 | TornadoEffect_InitDetails | clNormal3D("tornado.dat") + 4x4 identity + 0x154 byte state init |
| 0x80056ec0 | FUN_80056ec0 | TornadoEffect_Ctor | 0x154 byte ctor outer wrapper |
| 0x80056f1c | FUN_80056f1c | TornadoEffect_RenderCallback | Object_RenderJObjTree wrapper (FUN_8007a078 callback) |
| 0x80056f40 | FUN_80056f40 | TornadoEffect_SubmitRender | per-frame render queue submission (5 caller) |
| 0x80056fc0 | FUN_80056fc0 | TornadoEffect_Tick | 大型 per-frame Tick (timers + wheel scale 3-mode FSM + color modulation + KartDriver resource flags) |

主要発見:
- **TornadoEffect = カート被弾時のスピン visual** (clNormal3D model `s_tornado_dat_802edc60` =
  "tornado.dat" を 4x4 identity 配置で render)。0x154 byte の独立 state を持ち、
  CarObject_Init で per-kart alloc。被弾時の wheel scale (4 wheel uniform) + body color
  +tornado spin + KartDriver-side resource flag を統合管理。
- **VisualEffectHolder_Dtor (0x80056c4c)** は generic dtor: ResourceHolder@(+0x10) の
  19 slot release + mkgp2_Object_Partial@(+0xa8) dtor + TimedFree。17 caller が同じ
  layout の visual effect holder 共有 (TornadoEffect は 1 例)。
- **wheel scale animator の 3-mode FSM** (state[+0x104]):
  - mode 1: single-direction lerp to target +0x108
  - mode 0/-1: bidir lerp to 1.0 (FLOAT_806d28cc)
  - mode 2: oscillator between [FLOAT_806d28f4, FLOAT_806d28f0]
  KartDriver_SetUniformScale_4Wheels で 4 wheel に適用。
- **player vs non-player alpha**: state[+0x8] == 1 なら FLOAT_806d28d4 (player full alpha)、
  else FLOAT_806d28d8 (other transparent)。tornado overlay の visibility 制御。

副次 rename 候補:
  FUN_800af65c / FUN_800b541c → ResourceHolder_Alloc / ResourceHolder_Bind
  FUN_800b542c → ResourceSlot_ReleaseAll_x19
  FUN_800b5400/53cc/53dc/53ec/53bc → KartResourceHolder_Set{Scales, FlagD2, Flag30, FlagsC8CC, Pulse}
  FUN_800dc638 → KartVisibility_IsHidden (推定)
  FUN_8007a078 → RenderQueue_Submit_v1

### Session 44 完了分 (2026-05-18、14 件) — KartItem render callback pair table 全 7 mode 命名

KartItem_DispatchEffectRenderByState (Session 43) が dispatch する 7 つの pre/post render callback pair を全て命名。FUN_8007dc8c(postCb, preCb, ctx) の 2-phase render API パターン。

| Mode | Pre callback | Post callback | 色 source | コメント |
|---|---|---|---|---|
| 7 (escalated) | 0x800566d4 KartItem_RenderCb_Mode7_Pre | 0x800566f8 KartItem_RenderCb_Mode7_Post | DAT_806d28c0/c4 (2 color) | 2-tone gradient |
| Default (cycle mod-6<4) | 0x80056794 _DefaultCycle_Pre | 0x800567b8 _DefaultCycle_Post | PTR_DAT_806d28bc (indirect) | runtime-updated color slot |
| 3 | 0x80056838 _Mode3_Pre | 0x8005685c _Mode3_Post | DAT_806d28b8 | banana/oil hit visual |
| 5 | 0x800568dc _Mode5_Pre | 0x80056900 _Mode5_Post | DAT_806d28b4 | collision flash |
| 6 | 0x80056980 _Mode6_Pre | 0x800569a4 _Mode6_Post | DAT_806d28b0 | heavy hit |
| 2/4 | 0x80056a24 _Mode2or4_Pre | 0x80056a48 _Mode2or4_Post | DAT_806d28ac | common visual (2/4 が同一 callback を共有) |
| 1 (boost charge) | 0x80056ac8 _Mode1_Pre | 0x80056b20 _Mode1_Post | DAT_806d28a8 + sub-mode switch | 13-phase cycle (0..12 → 3 swap-table group) |

主要発見:
- **Pre callback は 6/7 で identical body** (`FUN_802c352c(0)` のみ)、Mode 1 だけ 3 段
  setup (`FUN_802c352c` + `FUN_8026949c` swap-table + `FUN_80269454` TEV stage)。
  Mode 1 = boost charge は GX swap-table の追加 setup が必要。
- **Post callback の標準テンプレート** (Mode 3/5/6/2or4):
  ```
  FUN_802695ec(target, 0xff, 0x100, 6)   // alpha/blend
  load DAT_806d28XX → color reg 1
  FUN_802690d8(target, 0, 0xf, 0xf, 2)   // TEV stage
  FUN_80269160(target, 0, 0, 0, 1, 0)    // color combine
  ```
  Mode 7 は `FUN_802690d8(target, 0, 2, 4, 0xf)` の異なる引数 + 2 color slot。
- **Mode 1 Post の 13-phase cycle**: subMode = state[+0x130] / 8 が 0..12 の範囲で 3 つの
  swap-table group ({0,5,6,10,11,12} → 0, {1,4,7} → 1, {2,3,8,9} → 2) に dispatch。
  case 13/14/15 は no-op (= dim phase)。CW switch table emission の典型。
- **色定数のレイアウト** (806d28a8..28c4): mode 1 → 28a8, mode 2/4 → 28ac, mode 6 → 28b0,
  mode 5 → 28b4, mode 3 → 28b8, default → PTR_28bc, mode 7 → 28c0/c4。アドレス順
  != mode 順なのは linker layout の都合。

副次 rename 候補:
  DAT_806d28a8..28c4 → KartEffectColors_Mode{1,2or4,6,5,3,Default,7Lo,7Hi}
  FUN_802c352c → GX_ResetTevStage_v1 (kart effect 専用 reset、推測)
  FUN_8026949c → GX_SetTevSwapTable (= GameCube GX API wrapper)
  FUN_80269454 → GX_SetTevStageColorInput
  FUN_802695ec → GX_SetBlendMode_Custom
  FUN_80269230 → GX_LoadColorReg
  FUN_802690d8 → GX_SetTevColorInput
  FUN_80269160 → GX_SetTevColorOp

### Session 43 完了分 (2026-05-18、14 件) — Orphan dtors + KartEffectFadeTransit family + 描画 callback dispatcher

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80055f10 | FUN_80055f10 | OrphanDtorWrapper_dup1 | 未参照 std::unique_ptr<T,D>::~unique_ptr() instantiation (byte 等価 4 件) |
| 0x80055fa0 | FUN_80055fa0 | OrphanDtorWrapper_dup2 | 同上 dup |
| 0x80056030 | FUN_80056030 | OrphanDtorWrapper_dup3 | 同上 dup |
| 0x800560c0 | FUN_800560c0 | OrphanDtorWrapper_dup4 | 同上 dup |
| 0x80056150 | FUN_80056150 | KartEffectFadeTransit_Tick | warp-transit fade 完了 handler |
| 0x8005623c | FUN_8005623c | KartItem_Stub_Returns0 | 空 stub (vtbl[2] placeholder か) |
| 0x80056308 | FUN_80056308 | KartEffectFadeTransit_GetActiveValue | self+0x18 raw value accessor |
| 0x80056310 | FUN_80056310 | KartEffectFadeTransit_IsActive | branchless `(-x \| x) >> 31` predicate |
| 0x80056324 | FUN_80056324 | KartEffectFadeTransit_Dtor | simple TimedFree dtor (no vtable) |
| 0x80056360 | FUN_80056360 | KartEffectFadeTransit_Init | 0x1c byte struct ctor (5-arg) |
| 0x80056388 | FUN_80056388 | KartItem_FlushPendingRender | render mode 9/10 を見て FUN_8007dc38 flush |
| 0x80056424 | FUN_80056424 | KartItem_FlushRenderIfReady | gated dispatcher (`DispatchEffectRenderByState` を condition 付きで呼ぶ) |
| 0x80056464 | FUN_80056464 | KartItem_FlushPendingRender_v2 | v1 と微妙に違う gating の variant |
| 0x800564e4 | FUN_800564e4 | KartItem_DispatchEffectRenderByState | 7-mode dispatch で render callback pair を `FUN_8007dc8c` に register |

主要発見:
- **KartEffectFadeTransit struct (0x1c byte)** 確定: CarObject_Init で alloc される warp-transit
  fade 用 state object。`+0x00 isPlayer / +0x04 kartMovement / +0x08 physState / +0x0c
  linkController / +0x10 currIntensity / +0x14 targetIntensity / +0x18 active`。
  vtable なし、Dtor は単純 TimedFree。
- **CW std::unique_ptr<T,D> 未参照 instantiation**: 0x80055f10/fa0/56030/560c0 の 4 件は
  byte-identical で参照ゼロ。template T が異なる別 instantiation だが dtor codegen は
  identical (= vtbl[4] dispatch のみ T 非依存)。linker が DCE しなかった残骸。
  4 件以外にも今後同パターンが多数現れる可能性。
- **KartItem effect render callback registry** (FUN_8007dc8c): pre/post 2 段 callback
  + ctx を register する render API。mode 1-7 にそれぞれ 2 つの callback pair が割当て
  られており、KartItem_DispatchEffectRenderByState で state[+0x124] を見て dispatch。
  mode 7 への escalation path (state[+0x140] > 0 で 0 → 7) があり、boost charge /
  collision / heavy hit 等の visual effect 切替を司る。

副次 rename 候補:
  FUN_800566d4/f8 → KartItem_RenderCb_Mode7Escalated_Pre/Post
  FUN_80056794/b8 → KartItem_RenderCb_DefaultCyclePulse_Pre/Post (mod-6 cycle)
  FUN_80056838/5c → KartItem_RenderCb_Mode3_Pre/Post (banana/oil)
  FUN_800568dc/900 → KartItem_RenderCb_Mode5_Pre/Post (collision)
  FUN_80056980/9a4 → KartItem_RenderCb_Mode6_Pre/Post (heavy hit)
  FUN_80056a24/48 → KartItem_RenderCb_Mode2or4_Pre/Post (common visual)
  FUN_80056ac8/b20 → KartItem_RenderCb_Mode1_Pre/Post (boost charge)

### Session 42 完了分 (2026-05-18、6 件) — ItemEffectComposite 完結 (5番目の sub-class Impact + Category A 経路 + Quake TickWorker)

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80055640 | FUN_80055640 | ItemEffectQuake_TickWorker | inner per-frame worker (位置 LERP + cos/sin 振動 + 4x4 mult + 衝突 snap) |
| 0x8005453c | FUN_8005453c | ItemEffectImpact_Init | 0xec sub-class vtbl[2] (3 identity 4x4 + scalars) |
| 0x80055c08 | FUN_80055c08 | ItemEffectImpact_Tick | 0xec class Tick (25 frame sub-counter + 4x4 mult + position += velocity) |
| 0x800547d8 | FUN_800547d8 | ItemEffectImpact_Dtor | CW MI dtor |
| 0x80053e8c | FUN_80053e8c | ItemEffectImpact_TryArm | Category A (26-entry DAT_802ed5b4) — vestigial r4-r6 pass-through |
| 0x80053ed0 | FUN_80053ed0 | ItemEffectImpact_TryArmInit | Rodrigues axis-angle 設定 (ItemStateSlotC_Init の Category A 版) |

主要発見 (ItemEffectComposite 全体構造の確定):
- **ItemEffectComposite (0x1c byte, 7 ptr)**: CarObject_Init 内の Alloc(0x1c) → FUN_800549a4 で 5 sub-effect を保持する。
  - composite[0] = CarObject ptr
  - composite[1] = ItemEffectImpact (0xec, Category A、26 アイテム = 大半のアイテム)
  - composite[2] = ItemEffectQuake (0xa8, Category D、1 アイテム full freeze)
  - composite[3] = ItemEffectJump (0xe4, Category C、9 アイテム heavy stun launch)
  - composite[4] = ItemEffectDamp (0x20, Category B、11 アイテム damping)
  - composite[5] = ItemEffectSpin (0xa4, Category E、1 アイテム mode 4)
  - composite[6] = active_effect_ptr (= +0x18、TryArm が promote する slot、null=idle)
- **TryArm dispatch slot pattern**: 各 Category の TryArm は `composite+0x18 = composite+N` で対応 sub-effect の ptr を active slot に copy。それぞれの N:
  - A (+4 = composite[1]) / B (+0x10 = composite[4]) / C (+0xc = composite[3]) / D (+0x8 = composite[2]) / E (+0x14 = composite[5])
- **Outer state (0x14 byte)**: CarObject_Init で別途 alloc される [CarObject, composite, itemId, ...] のラッパー。これが ItemEffect_TryStartByCategory の self。self[1] = composite。
- **Rodrigues setup の 2 経路** (Category A 用 ItemEffectImpact_TryArmInit と Category C 用 ItemStateSlotC_Init) が異なる angle (FLOAT_806d2828 vs 806d2818) で同じ axis-angle 計算を実行。state offset の違い:
  - C: state[0x25..0x2f] rot + [0x35..0x37] axis、[0x14..0x50] matrix copy
  - A: state[0x25..0x2f] rot + [0x36..0x38] axis、[0x35] = param、[0x39] = 0、velocity scaled by 60.0 at end

副次 rename 候補:
  FUN_80055ce0 → 次セッション開始点 (= ItemEffectImpact_Tick の後続)
  ItemEffectImpact_TryArmInit を ItemStateSlotA_Init に揃える case 検討 (slot 命名統一)
  KartItem_ForwardToCarMovement_8019a4e0 / 8019a6a4 の差異 — 8019a4e0 は Jump/Spin、8019a6a4 は Impact が使用

### Session 41 完了分 (2026-05-18、15 件 + 過去 plate 訂正 1 件) — ItemEffectComposite sub-class 大量 rename (4 サブクラス)

ItemEffectComposite (CarObject_Init で FUN_800549a4 が allocate する 5 sub-object container) の構造を一気に解明。各 sub-effect は独自 vtbl + CW MI 2-vtable パターン、TryArm/Init/Tick/Dtor の 4 関数組。本 session で 4 サブクラスを命名。

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800532d4 | FUN_800532d4 | ItemStateSlotC_Init | Rodrigues 軸回転設定 (cross product axis + sin/cos rotation matrix) |
| 0x8005399c | FUN_8005399c | ItemEffectJump_Init | 0xe4 byte、3 identity 4x4 + 0x22-frame timer (trick/jump air-spin) |
| 0x80055148 | FUN_80055148 | ItemEffectJump_Tick | 4x4 matrix accumulation + gravity step + landing boost arm |
| 0x80054890 | FUN_80054890 | ItemEffectJump_Dtor | CW MI dtor |
| 0x80053ae0 | FUN_80053ae0 | ItemEffectQuake_TryArm | Category D (1-entry DAT_802ed930) full-freeze TryArm |
| 0x80053c70 | FUN_80053c70 | ItemEffectQuake_Init | 0xa8 byte、2 identity 4x4 + phase counter |
| 0x80055558 | FUN_80055558 | ItemEffectQuake_Tick | phase FSM (0-2 setup / 3-6 hold 14.0 / 7+ landing check) |
| 0x80054834 | FUN_80054834 | ItemEffectQuake_Dtor | CW MI dtor |
| 0x80053d80 | FUN_80053d80 | ItemEffectDamp_TryArm | Category B (11-entry DAT_802ed7bc) damping TryArm with velocity + damp params |
| 0x80053e68 | FUN_80053e68 | ItemEffectDamp_Init | 0x20 byte minimal init (zero 6 fields) |
| 0x800550d0 | FUN_800550d0 | ItemEffectDamp_Tick | timer countdown + damp ramp + velocity push |
| 0x800548ec | FUN_800548ec | ItemEffectDamp_Dtor | CW MI dtor |
| 0x80054b74 | FUN_80054b74 | ItemEffectSpin_Tick | 0xa4 byte sub-class vtbl[3] wrapper (calls TickWorker) |
| 0x80054c00 | FUN_80054c00 | ItemEffectSpin_TickWorker | damp accumulator + per-frame Y-axis cos/sin rotation matrix mult |
| 0x80054948 | FUN_80054948 | ItemEffectSpin_Dtor | CW MI dtor |

過去 plate 訂正:
- 0x80053290 (ItemStateSlotC_TryArm) — Session 40 plate「no-arg variant」は誤り。実は r4-r6 を vestigial pass-through で 0x800532d4 (Init) に転送する。outer caller (ItemEffect_StartCategoryC) が r3-r6 すべてを設定。本 session で訂正済。
- 0x80053168 (ItemState_InitKeyframeBufferAndArm) — Session 40 plate「4 channel × 8 keyframe buffer」は実は ItemEffectSpin の 2 4x4 identity matrices (5-stride staggered diagonals)。名前は残置、Dtor plate に訂正注記。

主要発見:
- **ItemEffectComposite の 5 sub-object 構造確定**:
  | slot | size | vtbl base | 命名 | 役割 |
  |---|---|---|---|---|
  | param_1[1] | 0xec | 803f7758 | (未命名) | 最大 — 未調査 |
  | param_1[2] | 0xa8 | 803f7724 | ItemEffectQuake | Cat D 全凍結 |
  | param_1[3] | 0xe4 | 803f7704 | ItemEffectJump | Cat C? trick/jump spin |
  | param_1[4] | 0x20 | 803f76e4 | ItemEffectDamp | Cat B damping |
  | param_1[5] | 0xa4 | 803f76c4 | ItemEffectSpin | Y軸固定 spin (ItemState_InitKeyframeBufferAndArm = Init) |
- **CW MI 2-vtable layout 確証**: 各 sub-object は heap alloc 後に primary vtbl (`PTR_PTR_803f7738` = ItemEffectBase) + 派生 vtbl の 2 つを連続 store。vtbl base + 8 = vtbl[2] (Init)、+0xc = vtbl[3] (Tick)、+0x10 = vtbl[4] (Dtor)。dtor は 2 vtbl を base に巻き戻して TimedFree。
- **vestigial argument pass-through** (重要): ItemStateSlotC_TryArm (0x80053290) と ItemEffectQuake_TryArm (0x80053ae0) は r3 のみ explicit に使い r4-r6 を素通しで Init に渡す。caller 側 (StartCategoryC / StartCategoryB) で r4-r6 が完全に setup されているため動作する。decompile では 1-arg に見えるが実は 4-arg。
- **Rodrigues' rotation formula** が ItemStateSlotC_Init (0x800532d4) で完全展開: cross product → 正規化 → sin/cos*K で 3x3 rotation matrix を生成 (axis-angle by FLOAT_806d2818)。
- **boost intensity 階層** FLOAT_806d2800 (35.0, init) → 806d2838 (5.0) → 806d2820 (10.0) → 806d2830 (14.0) → 806d2834 (17.0) → 806d283c (25.0)。Init/Hold/Land の異なる段階に対応。

副次 rename 候補:
  FUN_80055640 → ItemEffectQuake_TickWorker (Quake_Tick の inner worker、未調査)
  FUN_80061954 → CarObject_SetBillboardMode (0x801ad7a0 系の visual mode setter)
  FUN_80061938 → CarObject_SetBillboardPosition
  KartItem_SetCarObjectField1c8Float, KartItem_ForwardToCarMovement_8019a4e0 — 既存命名再確認

### Session 40 完了分 (2026-05-18、6 件) — KartMovement boost visual blend + ItemStateGuard / TryArm 系

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80052dbc | FUN_80052dbc | KartMovement_UpdateBoostVisualBlend | boost counter で 3 段階 (active/fade/idle) 色 LERP + 120fr fade |
| 0x80052f20 | FUN_80052f20 | ItemStateGuard_PruneIfDeadAndReport | vtbl[3] alive check + vtbl[2] release + NULL out |
| 0x80052f9c | FUN_80052f9c | ItemStateGuard_IsActive | branchless null-check predicate (per-frame hot path) |
| 0x80052fb0 | FUN_80052fb0 | ItemState_TryArmWithDataCopy | 16-word descriptor を duplicate copy で arm (category D/E) |
| 0x80053168 | FUN_80053168 | ItemState_InitKeyframeBufferAndArm | 4 channel × 8 keyframe buffer init + CarObject+0xd4=1 arm |
| 0x80053290 | FUN_80053290 | ItemStateSlotC_TryArm | category C 用の no-arg TryArm |

主要発見:
- **ItemStateGuard pattern 確定**: 各 KartItem effect slot は (preallocated state @+0x14
  or +0xc) と (active state @+0x18) のペア構造で、`TryArm` が pre → active へ promote
  + vtbl[2] (init) を呼び、`PruneIfDead` が毎フレーム vtbl[3] (alive) で auto-cleanup。
  `IsActive` は branchless null-check で per-frame の hot path に最適化。
- **TryArm pattern** が複数 variant:
  - SlotC (no-arg) — full freeze category
  - WithDataCopy (16-word descriptor + 4-arg) — category D/E
  - 他に変種あり (要 0x800532d4 等を確認)
- **KartMovement_UpdateBoostVisualBlend** は boost effect visual の 3-mode FSM:
  - active: u32→signed-float idiom で counter [0, FLOAT_806d2744] → t、LERP
  - fade: 120-frame countdown
  - idle: 固定値
  global FLOAT_806d1080/1084 が boost ramp の現在値を保持し、別 subsystem が読む。
- **u32 → signed-float idiom** (`(uVar9 ^ 0x80000000) - DOUBLE_806d2790`) が
  KartItem_UpdateShadowBillboardAndViewport (Session 34) と KartMovement_UpdateBoostVisualBlend
  の 2 関数で再利用。CW compiler の int→double cast の典型展開。

副次 rename 候補:
  FUN_800532d4 → ItemStateSlotC_Init (category C state init、ItemStateSlotC_TryArm の callee)
  DAT_802ebe14..30 → boost color LERP keyframe table (8 floats × 2 colors)

### Session 39 完了分 (2026-05-18、16 件) — StlList primitives + 13 Wrapper dtors (KartItem sub-object holders)

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80052684 | FUN_80052684 | StlList_InsertBefore | list::insert(it, value) 4-arg ABI |
| 0x8005272c | FUN_8005272c | StlList_InitEmpty | empty list ctor with sentinel self-loop |
| 0x80052744 | FUN_80052744 | CarObjectManagerBase_Dtor | base vtbl restore (0x803f7690) + free |
| 0x8005278c | FUN_8005278c | Wrapper_FreeViaFUN_800a9414 | FUN_800a9414 で owned object destroy |
| 0x80052808 | FUN_80052808 | Wrapper_FreeOwnedHandleAndSelf | raw buffer free + self free |
| 0x8005285c | FUN_8005285c | Wrapper_FreeOwnedHandleAndSelf_dup1 | 0x80052808 と identical bytes (duplicate symbol) |
| 0x800528b0 | FUN_800528b0 | Wrapper_DestroyViaFUN_80064f58 | sound sub-system dtor wrapper |
| 0x8005292c | FUN_8005292c | Wrapper_DestroyViaFUN_8005c51c | sub-system 2 dtor wrapper |
| 0x800529a8 | FUN_800529a8 | Wrapper_DtorWithNestedSubObject | self[0] non-NULL → FUN_80054660(+4, 1) + free both |
| 0x80052a58 | FUN_80052a58 | Wrapper_DestroyViaFUN_80056324 | CarObject effect dtor wrapper |
| 0x80052ad4 | FUN_80052ad4 | Wrapper_DestroyViaFUN_800642b0 | sub-system dtor wrapper |
| 0x80052b50 | FUN_80052b50 | Wrapper_DestroyViaFUN_80056c4c | sub-system dtor wrapper |
| 0x80052bcc | FUN_80052bcc | Wrapper_DestroyViaFUN_8005afe0 | sub-system dtor wrapper |
| 0x80052c48 | FUN_80052c48 | Wrapper_DestroyKartDriver | KartDriver_Dtor wrapper (slot 0xb in KartItem) |
| 0x80052cc4 | FUN_80052cc4 | Wrapper_DestroyViaFUN_8019e1a8 | movement helper dtor (slot 0xa) |
| 0x80052d40 | FUN_80052d40 | Wrapper_DestroyViaFUN_80058de8 | audio channel dtor (slot 0x9) |

主要発見:
- **STL list complete primitive set** (Session 38 + 39 で全 4): InitEmpty / InsertBefore /
  RemoveByValueField / EraseRange。MetroTRK MSL の `std::list<T>` 実装の痕跡が
  完全に出揃った (node 12B = prev/next/value)。
- **KartItem sub-object holder cluster** (0x80052744..0x80052d3f 範囲): 13 個の
  Wrapper dtor が KartItem_Dtor (Session 33) の sub-object slot 0x9..0x17 と
  対応する。各 wrapper は `(self->[0] != NULL) ? specialized_dtor : free` のみの
  軽量 holder class で、`std::unique_ptr` 的な role を担う (蓋し RAII smart-pointer
  の inline 展開)。Wrapper_DestroyKartDriver の slot 0xb 同定で KartItem の sub-object
  対応が確定。
- **CarObjectManagerBase_Dtor (0x80052744)** が base vtbl (0x803f7690) の dtor、
  CarObjectManager_Dtor (0x80052158) の派生 dtor とペア。
- **CW compiler は inline-instantiated holder dtor を多数 emit する** ことが判明
  (FUN_80064f58/8005c51c/80056324/800642b0/80056c4c/8005afe0/8019e1a8/80058de8 等の
  各 sub-system dtor が個別 holder wrapper を持つ)。これは std::unique_ptr<T, D>
  の dtor template instantiation の典型痕跡。

副次 rename 候補:
  FUN_80064f58 → SoundChannel_Dtor (Wrapper の callee)
  FUN_8005c51c → ??Sub2_Dtor
  FUN_80054660 → NestedSubObject_Dtor (Wrapper_DtorWithNestedSubObject の inner dtor)
  FUN_80056324 / 800642b0 / 80056c4c / 8005afe0 / 8019e1a8 / 80058de8 → 各 sub-system
    の dedicated dtor

### Session 38 完了分 (2026-05-18、9 件) — CarObjectManager dtor + Bitset / StlList primitives

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80052158 | FUN_80052158 | CarObjectManager_Dtor | 2-vtable class dtor (0x803f76a8 / 0x803f7690) + STL list teardown |
| 0x80052200 | FUN_80052200 | Bitset_Dtor | self+8 buffer free + optional self free |
| 0x8005225c | FUN_8005225c | Bitset_TestBit | `(-x \| x) >> 31` branchless bit test |
| 0x80052288 | FUN_80052288 | Bitset_SetBit | bit set / clear with value param |
| 0x800522d0 | FUN_800522d0 | Bitset_Init | std::vector<bool> ctor + length-error throw path |
| 0x80052494 | FUN_80052494 | Bitset_MaxSize | UINT32_MAX 定数 |
| 0x800524cc | FUN_800524cc | NoOpDtor_FreeIfRequested | 汎用 "vtbl[+8] dtor 入口" (no fields) |
| 0x80052508 | FUN_80052508 | StlList_RemoveByValueField | STL list remove (連続 match を一括 splice + free) |
| 0x800525d4 | FUN_800525d4 | StlList_EraseRange | STL list erase(first, last) |

主要発見:
- **Bitset (std::vector<bool>) 完全 API**:
  - Init(count, init) → 32-bit word buffer alloc + initialize
  - TestBit(idx) → 0/1 with branchless extract
  - SetBit(idx, val) → set or clear
  - Dtor / MaxSize (UINT32_MAX)
  CarObjectManager_RunKartKartCollisionSweep が visited bitset で使用。
- **STL List primitives**: STL の `list::remove` / `list::erase(first, last)` が
  プロジェクト内で標準実装 (MetroTRK の MSL or 手書き)。layout: prev(+0)/next(+4)/value(+8)。
  CarObjectManager_Dtor の STL list teardown は EraseRange で実装される。
- **CarObjectManager は 2-vtable class** (`0x803f76a8` derived + `0x803f7690` base)。
  KartItem (Session 33 で発見) と同じ multiple-inheritance パターン → CW C++ で
  CarObjectManager が KartItem の親 class かまたは同じ interface を実装している可能性。
- **NoOpDtor_FreeIfRequested** は POD-only class の dtor として再利用される generic
  vtbl entry。複数 class からの参照が予想される。

副次 rename 候補:
  FUN_802791bc / FUN_80279274 / FUN_80271ef4 → std::logic_error / vector::length_error
    の throw 経路 helpers
  PTR_PTR_803f5700 / PTR_PTR_803f56f0 → typeinfo for std::vector::length_error /
    std::logic_error (vtable)
  PTR_PTR_803f76a8 / PTR_PTR_803f7690 → CarObjectManager class hierarchy vtables

### Session 37 完了分 (2026-05-18、10 件) — ItemEffect category starters + table-search helpers + kart-kart collision sweep

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80051648 | FUN_80051648 | ItemEffect_StartCategoryC | 9-entry DAT_802ed8c4 (12B/entry) で billboard mode 3 + full velocity freeze |
| 0x80051834 | FUN_80051834 | ItemEffect_StartCategoryB | 11-entry DAT_802ed7bc (24B/entry) で 前/後の impact 角度別 damp |
| 0x80051a70 | FUN_80051a70 | ItemEffect_StartCategoryA | 26-entry DAT_802ed5b4 (20B/entry) で 直線 velocity bounce |
| 0x80051cb8 | FUN_80051cb8 | ItemTable_FindEntryByIdStride16 | 16B-stride linear search |
| 0x80051cf0 | FUN_80051cf0 | ItemTable_FindEntryByIdStride12_v1 | 12B-stride (cat D) |
| 0x80051d28 | FUN_80051d28 | ItemTable_FindEntryByIdStride12_v2 | 12B-stride (cat C) |
| 0x80051d60 | FUN_80051d60 | ItemTable_FindEntryByIdStride24 | 24B-stride (cat B) |
| 0x80051d98 | FUN_80051d98 | ItemTable_FindEntryByIdStride20 | 20B-stride (cat A) |
| 0x80051dd0 | FUN_80051dd0 | CarObject_GetField304Vec3 | CarObject+0x304..+0x30c (3-float) → outVec3 |
| 0x80051dec | FUN_80051dec | CarObjectManager_RunKartKartCollisionSweep | g_carObjectList 上の O(N²) kart-kart 衝突検出 (周期実行) |

主要発見:
- **ItemEffect 5-category system 全体像** が確定:
  - Category A (DAT_802ed5b4, 26 entries × 20B): 直線 velocity bounce — banana / shell 直撃系
  - Category B (DAT_802ed7bc, 11 entries × 24B): 前/後 impact 別 damp — 衝突 lateral
  - Category C (DAT_802ed8c4, 9 entries × 12B): full freeze + billboard mode 3 — heavy stun
  - Category D (DAT_802ed930, 1 entry × 12B): inline category — quake-like full freeze
    (DAT_802ed934/938 effect ids)
  - Category E (DAT_802ed93c, 1 entry × 16B): inline category — billboard mode 4 +
    FUN_80052fb0 specialized (DAT_802ed940/944/948 effect ids)
  Each category writes to KartItem 2-lane keys (self[3]/self[4]) which feed
  KartItem_TickActiveEffectsTwoLane next frame.
- **CarObjectManager_RunKartKartCollisionSweep** が kart-on-kart 衝突の amortized
  sweep (毎フレーム N サイクル後に 1 回 full pass)。OBB bbox を CarObject mtx +
  FUN_802d6618 ext で構築 + g_objCollChecker (CollisionTest_CalcPenetration) で
  ペア判定 + vtbl[+0x3c] coupling callback。
- **table-search idiom** 5 variant: 同一アルゴリズム (linear search for entry[0] == id)
  が stride 別に 5 つの inline copy で展開 = CW template instantiation の典型痕跡。
- **rsqrt NR 2-iter idiom** が ItemEffect Start * (A/C) でも使われ、累計 8 関数で
  確認済み。

副次 rename 候補:
  FUN_80053290 / FUN_80053ae0 / FUN_80053d80 / FUN_80053e8c / FUN_80052fb0 →
    各 category の effect-bus push helpers
  FUN_80052288 / FUN_8005225c / FUN_800522d0 → bitset bit set / get / alloc
  FUN_802090f0 / FUN_802091bc / FUN_80209180 → CollisionProxy ctor / init / dtor
  FUN_802d6618 → CarObject bbox half-extent reader
  CollisionTest_CalcPenetration は既存名 (既 named)

### Session 36 完了分 (2026-05-18、8 件) — ItemEffect tick lanes + status flags + per-effect callbacks + category dispatcher

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800501b0 | FUN_800501b0 | ItemEffect_GenericHandler | 58-entry table @ DAT_802ebf0c から item id 解決 + ItemEffect_OnHit forward |
| 0x8005094c | FUN_8005094c | KartItem_TickActiveEffectsTwoLane | 2-lane × 9-way effect dispatch per frame + camera/sound/SE commit |
| 0x80050cf0 | FUN_80050cf0 | KartItem_TickStatusEffectsByFlag | KartCarPhysics flag 0x200000/0x8/0x1000/0x20000/0x20000000/0x10000000 駆動の passive effect tick |
| 0x80051100 | FUN_80051100 | ItemEffectDesc_OnApply_BoostLandingSE | desc+0x50 callback: SE 0x56 + RankLog (boost landing) |
| 0x80051184 | FUN_80051184 | ItemEffectDesc_OnApply_FreezeKartOrSlowdown | gate flag に応じて vel zero+impulse or 単に SE stop+slowdown |
| 0x8005125c | FUN_8005125c | ItemEffectDesc_OnApply_MushroomBoost | desc 経由の mushroom boost (item 0x17 と同等処理) |
| 0x800512e4 | FUN_800512e4 | ItemEffect_TryStartByCategory_Wrap | argless trampoline |
| 0x80051304 | FUN_80051304 | ItemEffect_TryStartByCategory | 5-category linear search で itemId を effect 開始経路に dispatch |

主要発見:
- **ItemEffect dispatcher hierarchy** が階層的に確定:
  - ItemEffect_Dispatch (既 named, 0x80050410) — top-level item-use entry
  - ItemEffect_SelectAndDispatch (0x80050030) — descriptor-table 経由解決 (7-entry, 24B/entry)
  - ItemEffect_GenericHandler (0x800501b0) — 58-entry generic table (100B/entry)
  - ItemEffect_TryStartByCategory (0x80051304) — 5-category linear search 経路
- **KartItem effect 2-lane システム**:
  - lane = 6-word stride、3 × 2 = 18 word per KartItem
  - 各 lane: [effectId, timer, descPtr, callbackArg, _, strength]
  - 9-way effect-type dispatch (desc+0x18 = 1..9 で FUN_8005b288..c338 のどれかを呼ぶ)
  - speed-effect (desc+0x34: 0/1/2) + camera-effect (desc+0x40: 0/1/2/3) + optional
    vtbl callback (desc+0x50) が独立 channel
- **ItemEffectDesc callback の引数規約** (FUN_8005xxxx で見える共通 signature):
  `(int desc, int self, _, _, int source, _, char gateFlag)` — 4 個目以降は型未確定だが
  desc/self/source/gateFlag は確実。callback 内で gateFlag=0 だと no-op + return desc+4
  を返す convention。
- **item 0x17 = mushroom** が確定 (FUN_800501b0 の特殊ケース + descriptor callback の
  両方で同じ SE 5/0x55 + SpeedBoost_Apply + +0xdc/+0x10c set パターン)。
- **KartCarPhysics passive flag effect** (0x80050cf0):
  - 0x14&8 = 21-frame random pitch dither
  - 0x10&0x1000 = sin oscillation pitch-bend (FUN_8027e9e8 = cos の高速変換)
  - 0x10&0x20000 = boost-landable waiter (FUN_8019a8a4 probe + SE 0x56)
  - 0x10&0x20000000 = decaying effect (FUN_8005c0fc)
  - 0x10&0x10000000 = passive camera shake (CameraEffect_Apply)
  - 0x14&0x200000 + 0x10&0x40000000 = default effect dispatch

副次 rename 候補:
  FUN_8005b288 / FUN_8005b490 / FUN_8005b628 / FUN_8005b8cc / FUN_8005bb0c /
    FUN_8005bd04 / FUN_8005bec4 / FUN_8005c0fc / FUN_8005c338 → 9 effect type handlers
  FUN_8027e9e8 → sin/cos lookup
  FUN_802dca04 → random (small range)
  FUN_8005bb00 → sound pitch dither
  FUN_80057a18 / FUN_80057a4c → engine SE adjust
  FUN_80058994 → CarObject SE commit
  FUN_80052f9c → ItemState_IsBlocked / ItemEffectGuard_IsActive
  FUN_80051d28 / FUN_80051d60 / FUN_80051d98 / FUN_80051cb8 / FUN_80051cf0 → 5
    category-specific table-scan helpers

### Session 35 完了分 (2026-05-18、12 件) — KartItem cancel paths + velocity I/O + ItemEffect dispatcher

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8004f340 | FUN_8004f340 | KartItem_CancelActiveEffect | 2-lane teardown + 全 phys flag clear + SE 0x64 |
| 0x8004f50c | FUN_8004f50c | KartItem_GetCarVelocityVec3 | CarObject+0x17c..0x184 → outVec3 |
| 0x8004f540 | FUN_8004f540 | KartItem_SetCarVelocityVec3 | inVec3 → CarObject+0x17c..0x184 |
| 0x8004f560 | FUN_8004f560 | KartItem_StopCarObjectSE | FUN_800579d8 wrapper |
| 0x8004f584 | FUN_8004f584 | KartItem_TryArmBoostOnLanding | FUN_8019a8a4 probe + SE 0x56 + state 0x18 family begin |
| 0x8004f628 | FUN_8004f628 | KartItem_SetCarObjectField2d4Float | CarObject+0x2d4 float setter |
| 0x8004f634 | FUN_8004f634 | KartItem_SetCarObjectField1c8Float | CarObject+0x1c8 float setter |
| 0x8004f668 | FUN_8004f668 | KartItem_ForwardToCarMovement_8019a4e0 | 1-line forwarder (callee 未確証) |
| 0x8004f68c | FUN_8004f68c | KartItem_ForwardToCarMovement_8019a6a4 | 同 |
| 0x8004f6b0 | FUN_8004f6b0 | KartItem_OnFallOffOrDeath | full cancel + 0xfc=1 flag + StrPcb idle (CancelActiveEffect の上位版) |
| 0x80050010 | FUN_80050010 | ItemEffect_SelectAndDispatch_Wrap | argless trampoline |
| 0x80050030 | FUN_80050030 | ItemEffect_SelectAndDispatch | 7-entry テーブル @ DAT_802ebe64 から effect id 解決 + dispatch |

主要発見:
- **KartItem cancel hierarchy** (3 段階):
  1. KartItem_TryCancelIfDropAllowed — 通常 cancel (drop-handler 確認付き)
  2. KartItem_CancelActiveEffect — 2-lane teardown + flag clear (アクティブ effect 中断)
  3. KartItem_OnFallOffOrDeath — 完全 reset + StrPcb idle + +0xfc flag (落下/死亡)
- **ItemEffect_SelectAndDispatch** が item effect 解決の central dispatcher。
  DAT_802ebe64 の 7-entry テーブル (6-int stride、24 byte/entry) を逆順に検索、
  type tag (1 = sourceCtx index, -1 = FUN_802dca5c index, 0 = fixed) で sub-id 選択。
  result が FLOAT_806d26e4 の単一 sentinel と一致したら self vtbl[0x34] 直接呼び出し、
  そうでなければ FUN_800501b0 (generic handler) に forward。
- **CarObject velocity I/O accessors**: GetVec3 / SetVec3 で +0x17c..0x184 vector を
  word-stride で raw copy する API が個別公開されている (= AI scripting で
  cart 物理を直接書き換える経路の存在を示唆)。

副次 rename 候補:
  FUN_800579d8 → CarObject_StopAllSE
  FUN_8019a8a4 → CarObject_IsBoostEligibleLanding
  FUN_8019a4e0 / FUN_8019a6a4 → KartMovement の op (内容未確証)
  FUN_800dd840 → ItemObject_GetVariantSlot? (返り値 byte が ItemEffect dispatch index)
  FUN_802dca5c → KartCharacter_GetEffectIndex?
  FUN_800501b0 → ItemEffect_GenericHandler (forward 先)

### Session 34 完了分 (2026-05-18、13 件) — KartItem shadow update + accessors + StrPcb command setters

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8004edd4 | FUN_8004edd4 | KartItem_UpdateShadowBillboardAndViewport | 速度 saturation → shadow billboard + SceneRender viewport set |
| 0x8004efd8 | FUN_8004efd8 | KartItem_AdvanceAnim3c | self+0x3c の anim 1-frame advance |
| 0x8004f040 | FUN_8004f040 | KartItem_GetMaxSpeedWithBonus | speed table + bonus multiplier (+0x2e0) で max-speed 計算 |
| 0x8004f0f8 | FUN_8004f0f8 | KartItem_GetCurrentSpeedWithBonus | KartMovement_CalcSpeedWithCoinBonus(self+0x28, 0) wrapper |
| 0x8004f134 | FUN_8004f134 | KartItem_GetCarObjectSoundCh | self+0x34 getter |
| 0x8004f144 | FUN_8004f144 | KartItem_GetByte_f4 | self+0xf4 byte getter |
| 0x8004f14c | FUN_8004f14c | KartItem_GetBoostArmedAndTimer | self+0xb1 byte + self+0xb4 float via out-ref |
| 0x8004f168 | FUN_8004f168 | KartItem_GetCarObjectState_2bc | CarObject+0x2bc byte getter |
| 0x8004f174 | FUN_8004f174 | KartItem_ResetStrPcbToIdle | StrPcb 0x28/0x1e/0/0 idle preset (KartItem_Dtor の preset と同等) |
| 0x8004f1cc | FUN_8004f1cc | KartItem_SetStrPcbIntensityFromSpeed | saturate × FLOAT_806d27e4 × FLOAT_806d2700 → StrPcb counter |
| 0x8004f238 | FUN_8004f238 | KartItem_SetStrPcbCmd2fFromFloat | float × FLOAT_806d27e8 → StrPcb Cmd2f byte |
| 0x8004f290 | FUN_8004f290 | KartItem_SetStrPcbCmd2eFromFloat | 同 Cmd2e |
| 0x8004f2e8 | FUN_8004f2e8 | KartItem_SetStrPcbCmd2dFromFloat | 同 Cmd2d (3-byte preset の 3 setter family) |

主要発見:
- **KartItem accessor cluster** が 0x8004f000-0x8004f2ff 範囲に集中。inline 化されず
  individual function として外部に export されている = `KartItem` class の public API。
  StrPcb setter 3-family (Cmd2d/2e/2f) + intensity setter は全て `self+0x20 == 1` で
  local-control gated → "remote/AI のときは steering wheel に書かない" 安全策。
- **bonus multiplier**: CarObject+0x2e0 が "速度ボーナス" の蓄積 field。
  KartItem_GetMaxSpeedWithBonus が `base × (1 + bonus)` で適用する基準。
- **KartItem_AdvanceAnim3c** の存在から self+0x3c は "AdvanceableAnim" interface
  (per-frame tick 必要な anim handle)。これは KartItem_PerFrameStep の末尾
  FUN_80056150(self+0x3c) と整合 (= commit 後の advance)。
- **KartItem_UpdateShadowBillboardAndViewport** の signed-float idiom が判明:
  `(u32 ^ 0x80000000) → DOUBLE_806d2790 を引く` は u32→signed-float 高速変換
  (CW compiler の signed int → double cast の lookup pattern)。

副次 rename 候補:
  FUN_80056308 → AdvanceableAnim_AdvanceOneFrame
  FUN_80056150 → AdvanceableAnim_Commit
  FUN_8006389c → KartItem_UpdateShadowBillboard_AnimPath
  FUN_80061a88 → KartItem_UpdateBillboardMtx
  FUN_80063e50 → KartItem_CommitShadowBillboard

### Session 33 完了分 (2026-05-18、3 件、大型関数のみ) — KartItem master Tick / Step / Dtor

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8004baac | FUN_8004baac | KartItem_Tick | per-frame Update: view-space UV scroll + audio + rumble + saturation-based vol |
| 0x8004c320 | FUN_8004c320 | KartItem_PerFrameStep | per-frame physics + boost/bonk state + 4-wheel joint export |
| 0x8004e2b0 | FUN_8004e2b0 | KartItem_Dtor | KartItem teardown (g_carObjectList unlink + 14 sub-object dispose + 2 vtable restore) |

主要発見:
- **KartItem は 2-vtable class** (multiple inheritance):
  - primary vtable @ 0x803f75f8
  - secondary vtable @ 0x803f7608
  Dtor は両方 restore してから両方の base-class dtor (FUN_800aa888 + FUN_80060024) を呼ぶ。
- **KartItem は g_carObjectList linked list の要素**: g_carObjectCount を保持、最後の
  要素が破棄されるとリスト自体を free (vtbl[2] virtual call) する singleton 管理。
- **KartItem の sub-object slot map** (Dtor reverse order で確証、計 15 slot):
  - self[0x9] FUN_80058de8 (audio channel dtor)
  - self[0xa] FUN_8019e1a8 (movement helper)
  - self[0xb] KartDriver (KartDriver_Dtor — 副次確証!)
  - self[0xc..0xf] CarObject 系 effect (FUN_8005afe0/56c4c/642b0/56324)
  - self[0x10] 0x4-byte sub-record w/ +4 nested
  - self[0x11] FUN_8005c51c, self[0x12] FUN_80064f58 (sound)
  - self[0x13/0x14] raw buf (MemoryManager_TimedFree 直接)
  - self[0x15] FUN_800a9414, self[0x16/0x17] FUN_80209180 (HSD scene obj?)
- **KartItem_Tick / KartItem_PerFrameStep** は同一 self struct を分担:
  - Tick: 音響 + StrPcb force feedback + Saturate_Double 音量曲線
  - Step: 物理積分 + boost/bonk state machine + 4 joint Y push + render commit
  恐らく per-frame で Tick → Step (or Step → Tick) を順次呼ぶ。
- **rsqrt Newton-Raphson 2-iter idiom** が 3 箇所 (今 session) で再利用、合計 6 関数の
  共通 idiom。GameCube SDK の標準 fast inv-sqrt + 2 refinement pattern が
  mkgp2 全体に広く分散。
- **Bonk / Boost release の item-id switch**: 11 個の specific item ids (0xc..0x11,
  0x12, 0x17, 0x19..0x1d, 0x21, 0x23, 0x29 系, 0xd5..0xd6, 0xdc, 0xe2, 0xe8, 0xee,
  0xf4, 0xfa, 0x101, 0x107) が FUN_80058c80 + FUN_8005a638 + FUN_80091b9c (+ 一部
  FUN_80091f0c) の trio dispatch を受ける。これらは "stunning" な item ID のセット
  (banana, shell, mushroom 派生品?)。

副次 rename 候補:
  FUN_80052508 → linked-list remove
  FUN_8019cc2c → KartMovement_PhysicsStep idle/AI variant
  FUN_8019a850 → CarObject wheel-Y read
  FUN_80057a00 → CarObject steering Euler-Z read
  FUN_80057a08 / FUN_80057a8c → CarObject world-mtx export
  FUN_80058c80 / FUN_8005a638 / FUN_80091b9c / FUN_80091f0c → stun-item dispatch trio
  FUN_800913f8 → KartCarPhysics hit-position write
  FUN_80091e40 / FUN_80091e78 → KartCarPhysics flag manip (0x40 / 0x30000000 系)
  FUN_8005623c → boost-release condition probe
  FUN_8005967c / FUN_800595b4 / FUN_80059644 → engine SE family

### Session 32 完了分 (2026-05-18、9 件) — KartItem core collision + apply-effect + render pipeline

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x8004a238 | FUN_8004a238 | KartItem_OnKartHit | kart-kart 衝突反応 (cooldown, SE 0x58/0x11/0xc6, KartReaction_Front/Side dispatch, RankLog) |
| 0x8004a8b8 | FUN_8004a8b8 | KartItem_PlayHitSE_DifferentVictim | victim != owner なら SE 0xb 再生 |
| 0x8004a918 | FUN_8004a918 | KartItem_ApplyEffectToVictim | central "apply effect" — RankLog/coin spend/2-lane teardown loop/effect commit trio |
| 0x8004b118 | FUN_8004b118 | KartItem_PlaySE_0x09 | SE 9 単発再生 |
| 0x8004b140 | FUN_8004b140 | KartItem_ApplyImpactReflectAndDampVelocity | rsqrt Newton-Raphson 2 iter で velocity damp + drop side event |
| 0x8004b394 | FUN_8004b394 | KartItem_TryDropCoinsAndPlaySE | count/force gate + FUN_800601c0 drop + SE 9 |
| 0x8004b430 | FUN_8004b430 | KartItem_TryCancelIfDropAllowed | cancel item + drop handler 確認 |
| 0x8004b49c | FUN_8004b49c | KartItem_ApplyImpactImpulseAndRumble | impact 力学計算 + StrPcb force-feedback (front/side で 2 種 timer) |
| 0x8004b9bc | FUN_8004b9bc | KartItem_RenderPipelinedWithEffects | multi-pass render + CarObject effects + 条件 post-FX blit |

主要発見:
- **KartItem subsystem の central API surface** がほぼ pin できた:
  - **collision side**: KartItem_OnKartHit → KartItem_ApplyImpactImpulseAndRumble (physics) +
    KartItem_ApplyImpactReflectAndDampVelocity (velocity damp + drop) を呼ぶ
  - **effect side**: KartItem_ApplyEffectToVictim が central applicator。
    RankLog/CoinSystem_RemoveCoins/MediaBoard_SendAndCheck を coordinate
  - **drop API**: KartItem_TryDropCoinsAndPlaySE, KartItem_TryCancelIfDropAllowed
  - **rendering**: KartItem_RenderPipelinedWithEffects が KartDriver_RenderTimed を
    pass 3→4→2 順で driving、間に CarObject effect (FUN_80056388/424/464) を挟む
- **iframe システム**: 衝突したら self+0xd0 = 0x2d (45 frames) で armed。
  KartItem_OnKartHit 側は self+0x31 = 0x3c (60 frames) で異なる timer (event-specific 違い)。
- **rumble timer mapping**: StrPcb_SetTimer3c40 で 2 種の duration:
  - front impact (dot > 0) → FLOAT_806d2734 (short pulse)
  - side/rear impact → FLOAT_806d2738 (long pulse)
- **CarObject 内部 layout** の追加判明:
  +0x1ac/+0x1b0/+0x1b4 = linear velocity, +0x1b8 = max velocity
  +0x310..+0x318 = torque/angular impulse
  +0x58/+0x5c/+0x60 = forward axis (3-float), +0x78..+0x80 = secondary axis
  +0x88/+0x8c/+0x90 = world position
  +0xb8..+0xc0 = rest/sleep velocity (snap target)
- **event type 4** が特別: KartItem_ApplyEffectToVictim も KartItem_RenderPipelinedWithEffects
  も `self+0x10 == 4` で異なる branch を取る。これは "drift" / "boost" の特殊 event type。
- **rsqrt Newton-Raphson 2-iter idiom** が 2 関数で再利用される (B140 と B49C):
    `dVar11 = 1.0 / sqrt(N); dVar11 = 0.5 * dVar11 * -(N * dVar11 * dVar11 - 3); ...×2`
  GameCube SDK で標準的な fast inv-sqrt + 2 refinement。

副次 rename 候補:
  FUN_8005a0bc → SoundObj_IsInRange?
  FUN_80091e40 / FUN_80091ac4 / FUN_80091438 / FUN_8009185c → KartCarPhysics の effect API
  FUN_80050010 / FUN_800512e4 / FUN_80052f9c → item state guard chain
  FUN_8005a140 / FUN_8005a314 / FUN_80058534 → CarObject SE channel API
  FUN_80056388 / FUN_80056424 / FUN_80056464 / FUN_800564e4 / FUN_80056f40 → CarObject effect render hooks
  FUN_802bd6ac / FUN_802bd6c0 → post-FX (bloom?) begin/end
  FUN_8005b168 / FUN_80061918 / FUN_80061920 → effect state probes
  FUN_8005b118 → state-row apply

### Struct Application Pass (2026-05-18) — Session 1-22 audit 結果を一括 struct 化

Session 1-22 で観察した struct access pattern を 5 並列 subagent で網羅 audit (227 関数全件)、
12 件の struct 候補を Ghidra に新規作成し関連関数 70+ 件に prototype 適用。

#### 新規作成 struct
| name | size | apply 対象 |
|---|---|---|
| StrPcb | 0x70 | strpcb subsystem 30 関数 (Session 9-13) + 9 grobal apply at DAT_806d1010 |
| HSD_JObj_Partial | 0x74 | JObj_GetNext/Child, JObj_SetScale, JObj_SetRotationQuat (Session 4/8) |
| HSD_DObj_Partial | 0xc | DObj_GetNext (Session 8) |
| mkgp2_Object_Partial | 0x5c | Object_* 29 関数 (Session 4-8) |
| CObj_Partial | 0x3084 | CObj_* 14 関数 (Session 3) |
| CObjProj_Partial | 0x84 | CObj_SetWorldMatrix sub-struct 参照のみ (まだ apply 未) |
| PathManager_Partial | 0x4dc | Race_CompareKartProgress, Path_ResetCursorForKart, PathParticipantArray_Dtor (Session 22) |
| PathCursor (stride 0x98) | 0x98 | PathManager_Partial.cursors[8] の要素型 |
| FlowDispatcher | 0x3c | Flow_TransitionTo, FlowDispatcher_Dtor/Create (Session 2) + global at DAT_806d0f80 |
| SeqMenuScene | 0x14 | SeqMenuScene_Init/Dtor/DrawDebugList/HandleInput (Session 2) |
| VolumeCalibration | 0x28 | VolumeCalibration_Tick/Dtor/Ctor/DrawOverlay (Session 16-17) |
| SceneRender_Partial | 0x2c | SceneRender_* 6 関数 (Session 19) |
| ScopedTimer | 0x8 | ScopedTimer_End (Session 2) |
| SharedPtr | 0x8 | SharedPtr_Init/Dtor (Session 14) |
| ClRomTableEntry | 0x8 | array[40] apply at DAT_80598678 |

#### 主要 verification
- StrPcb_OutputTick: 全 byte offset アクセスが struct field name に変換 (`self->counter_current`,
  `self->timerA_duration`, `self->dirty_flag` 等)
- Object_DriveAnimAndSkin: `obj->primary_jobj`, `obj->jobj_array`, `obj->anim_chain_descriptor`
  等が clean に。inner JObj access (`*(int *)(iVar12 + 0x1c)`) は local var 型未設定で残存
- Race_CompareKartProgress: `pathMgr->cursors[kartA].waypoint_index/lap_count/terminal_zone_marker`
  で 8-kart progress 比較が完全に struct field 経由に
- VolumeCalibration_Tick: `self->steering_min/max`, `self->strpcb_pos` 等で読みやすく

#### audit で見つけた訂正
- LoaderEntry_Partial の path field は **+0x04 ptr** (タスクファイル原文「+0x18+: path string」は誤り)
- PathCursor offset: 「offset 5 (= +0x14)」「offset 0x18 (= +0x60)」は **PathManager 起点絶対値**、
  cursor 内部 offset では +0x00 waypoint / +0x4C lap が正
- StrPcb +0x44 は **u32** (Session 12 メモ u16 は誤り、ParseResponse の `*(uint*)(self+0x44) = (uint)*(ushort*)(...)` で確証)
- StrPcb +0x10/+0x28 は u32 counter (RGBA byte[4] ではない、Init `0,0,1,0xFF` は big-endian 0x000001FF = 0x1FF neutral)
- Object_RenderJObjEx (0x80034220) **vestigial signature**: r3 (obj ptr) 未使用、r4=jobj のみ実質使用
- mkgp2_Object_Partial +0x10 は float/Mtx* で union 衝突可能性、現状 float 採用

#### struct 化を見送ったもの (理由付き)
| 対象 | 理由 |
|---|---|
| ServiceLatch / VBlankLatch globals | 独立 setter/getter、隣接配置の偶然 |
| MetricsTable | float[0x30] array で十分、per-slot struct 化 ROI 低 |
| STLContainer (CW MSL rb-tree) | template per-instantiation 展開で汎用化逆効果 |
| ClStrPcb vtable hierarchy | signature 不明、instance も SharedPtr 以外未確定 |
| clRom GlobalState (4 globals) | ペア access ない、global rename で十分 |
| MemoryManager globals | g_mainHeap は GC SDK HeapHandle、別 cluster |
| SceneFlow cleanup list | singleton 24 byte sentinel、struct より 4 named global |
| BootStateStruct (DAT_80594080) | size 0x60 だが field 意味未知、struct 後手 |
| MJObj / MObj | 親 HSD_JObj 完成後にやるべき (cluster 4 deferred) |

#### 既知の残課題
- inner JObj access (Object_DriveAnimAndSkin 等の local var iVar12 = `*(int *)(obj->jobj_array + N)`)
  は local var 型未設定で raw offset 残存。set_local_variable_type で個別 fix 可
- Object_DriveAnimAndSkin の 4 引数 signature (`Object*, double, double, Object*`) は guess、
  caller scan で確証要
- 派生 class (MJObj/MObj) は cluster 4 deferred、将来 HSD_JObj を base にした inheritance 整理時に


### Struct Application Pass Phase 2 (2026-05-18) — Session 23-42 audit 結果を一括 struct 化

Session 23-42 で観察した struct access pattern を 5 並列 subagent (cluster A-E) で網羅 audit
(198 関数全件)、20 件の struct 候補を Ghidra に新規作成し関連関数 140+ 件に prototype 適用、
DAT_806d1070 を `NamCam_Partial *` で global apply。

#### 新規作成 struct
| name | size | apply 対象 |
|---|---|---|
| KartItem | 0x380 | KartItem 主要 50+ 関数 (Session 23-26, 32-35) |
| ItemEffectComposite | 0x1c | composite 5 sub-class 経路の container struct |
| ItemEffectImpact | 0xec | Cat A、Impact_TryArm/Init/Tick/Dtor (Session 41-42) |
| ItemEffectQuake | 0xa8 | Cat D、Quake_TryArm/Init/Tick/Dtor + TickWorker |
| ItemEffectJump | 0xe4 | Cat C、Jump_Init/Tick/Dtor (Session 41) |
| ItemEffectDamp | 0x20 | Cat B、Damp_TryArm/Init/Tick/Dtor |
| ItemEffectSpin | 0xa4 | Slot C、Spin_Init/Tick/Dtor + TickWorker (Session 40 plate 訂正済) |
| KartItemEffectLane | 0x18 | KartItem self+0x28..+0x58 の 2-lane stride (Session 36 dispatch) |
| ItemEffectDesc | 0x64 | DAT_802ebf0c の 100B/entry 58 件 (Session 36 generic handler 経路) |
| ISESlot | 0x24 | KartDriver iseSlots[10]、Session 31 lifecycle 4 関数 |
| CourseEnvironment_Partial | 0x380 | Session 30 の Update/Dtor/ZeroInit/RenderObjects 計 6 関数 |
| NamCam_Partial | 0x80034 | Session 30-31、global apply at DAT_806d1070 (NamCam_Partial * 適用済) |
| ItemObject_Partial | 0x190 | Session 31 副次 (SetField168/SetPair180/SetByte18c) |
| CoinJumpFlasher_Partial | 0x10c | Session 31 (Toggle/SetArmed/PlayResultSE/Alternating/SE_0x13) |
| Bitset | 0xc | std::vector<bool>、Init/TestBit/SetBit/Dtor (Session 38) |
| StlListNode | 0xc | std::list<T> node、prev/next/value (Session 38-39) |
| CarObjectManager_Partial | 0x10 | 2-vtable、Dtor + Base_Dtor + RunKartKartCollisionSweep (Session 38-39) |
| KartItemSlotHolder | 0x4 | 12 個の Wrapper dtor 共通 1-ptr holder (Session 39) |
| PathIndex | 0x8 | int[2] = {count, cursor}、AdvanceCircular / GetCurrentIndex (Session 29) |
| EnvRenderEntry | 0x1c | CourseEnv 0x1c-stride chain record (Session 29 ObjectChain_RenderInZRange) |

#### prototype 適用範囲 (140+ 関数)
- KartItem cluster (Session 23-26, 32-35): 50+ 件 (Tick/PerFrameStep/Dtor + state machine + accessor)
- ItemEffect sub-class trio (Init/Tick/Dtor): 5 × 3 = 15 件 + TryArm 5 件 + composite 周辺 7 件
- ItemStateGuard + IsActive + TryArmWithDataCopy + ItemEffectDesc callback 3 件
- KartDriver Render/InitFull/Dtor/Construct + 7 TickAction + joint accessor (Session 27-29): 25+ 件
- CourseEnvironment + Subsystem36c + ObjectChain 計 7 件 (Session 29-30)
- NamCam Init/End/Tick 3 件 + Asset/JObj util 4 件 (Session 30)
- ISESlot 4 件 + CoinJumpFlasher/CoinEvent 5 件 + ItemObject 3 件 + FinalLapCoinJump 1 件 (Session 31)
- Bitset 5 件 + StlList 4 件 + CarObjectManager 3 件 + 12 Wrapper dtor (Session 38-39)
- ItemTable_FindEntryByIdStride* 5 件 + ItemCategoryBudget + CarObject_GetField304Vec3 (Session 37)

#### 主要 verification
- **KartItem (size 0x380)**: self+0x28 carObject (CarObject *), +0x2c ownerDriver (KartDriver_Partial *)、
  +0x174 holdingItem byte、+0x1f0 currentState、+0x290 timerRemaining、+0x2dc..+0x303 iseSlots[10]
  (byte[40] で apply 中)、+0x344 StrPcb embed plate に注記 (本 cluster 範囲では直接 access 未観察)
- **ItemEffectComposite**: 7 ptr container (car + 5 sub-effect + active slot)、各 sub-class への
  pointer 型を直接 field type に設定済 (ItemEffectImpact * 等)
- **5 ItemEffect sub-class**: 全て先頭 +0x00 vtbl + +0x04 parent (ItemEffectComposite **) の共通 prefix、
  audit corrigendum で CW MI base class ではなく **単一 vtable + dtor swap で兼ねている** と確定
- **ISESlot**: KartDriver_Partial +0x2dc..+0x300 の 10 個並び、armedFlag +0x04 / effectTypeId +0x14 /
  boundItem +0x18 (ItemObject *) で Tick/Dtor 経路の field 全 named
- **NamCam_Partial (size 0x80034)**: 0..0x7ffff = imageBuffer (= 512KB JPEG/cam scratch)、
  +0x80001 以降が protocolVersion/port/IP/socketHandle/state 等の networking header

#### audit で見つけた訂正
- **ItemStateSlotC_TryArm** (0x80053290): **4-arg vestigial pass-through** (Session 40 plate「no-arg」は誤り)、
  caller (StartCategoryC) が r4-r6 を完全設定して通過させる
- **ItemEffectSpin_Init** = 0x80053168 = ItemState_InitKeyframeBufferAndArm (Session 40 plate
  「4 channel × 8 keyframe buffer」は誤り、実は 2 4x4 identity matrices の staggered diagonal)
- **ItemEffectImpact +0xd4** は **timer/param のフェーズ別 union** (TryArmInit が param で書き、
  Tick が timer で読む) — struct 上は int 単一フィールド `timerOrParam` で表現、plate に注記
- **CourseEnvironment_ResolveJointInSlot0** (0x800476a0): plate「scene+0x14 (primary clN)」は誤り、
  実は **+0x50** (animObjects[0])
- **DAT_802ebf0c** は **100 byte/entry × 58 件 = ItemEffectDesc** (24B/entry 表記は別の Session 35
  DAT_802ebe64 と混同していた)
- **KartItem +0x2c0 currentItemId** は **int** (`cmp -1` / `cmp 0x6c..0x6f`)、+0x2c4 itemSubType は
  **byte** (Phase 1 教訓: byte[4] vs u32 警戒の踏襲)
- **KartItem +0x174 holdingItem** は **byte** (`*(char*)(self+0x174)` write 0、self[0x5d] int* access は
  同 byte を 32-bit clear している扱い、struct 上は byte で apply)

#### 既存 struct 拡張は保留 (理由付き)
| 対象 | 理由 |
|---|---|
| CarObject 拡張 (256 → 0x320) | Session 32 で +0x88/+0x178/+0x1ac/+0x310 access 確認。delete-recreate は参照 prototype を int reset するリスク (Phase 1 の delete_data_type 教訓)。次フェーズで専用 audit + carefully sequenced apply |
| KartDriver_Partial 拡張 | bazookaClnA/B (+0x04/+0x08)、bazookaJoint cache (+0x13c..+0x14c)、+0x29c objFun80173db4、+0x2dc..+0x303 iseSlot[10] ptr array 等。既存 named field 多数で add_struct_field の INSERT 動作で衝突リスク。次フェーズで modify_struct_field 経由か delete-recreate で sequence する |
| KartItem の StrPcb embed | self+0x344 周辺の StrPcb 内蔵フィールドは本 cluster 範囲で直接 access 未観察、Phase 3 で別調査 |
| Subsystem36c_Partial | vtbl ptr のみ (slot 4 dispatch)、struct 化より plate 注記で十分 |
| ItemCategoryBudget | int[5] array で十分、struct 化 ROI 低 |

#### 既知の残課題
- KartItem.iseSlots[10] は **byte[40] で apply 中**。10 個の ISESlot * pointer array として型適用するには
  pointer array typedef 化が必要 (`ISESlot *[10]` 直接指定の挙動未検証)
- **CarObject 拡張は次フェーズの最優先** (Session 32 関連の decompile を更に readable にする最大レバー)
- NamCam_Partial global apply (DAT_806d1070 → `NamCam_Partial *`) 適用済、Session 30 NamCam helper 経路で
  decompile が自動 struct 化されているはず (verify 未実施)
- ItemEffectComposite と KartItem のリンク (KartItem +0x40 stateGuardSlot は ItemEffectComposite ** という
  説あり、要 caller 経路の確認)
- ItemEffectDesc OnApply callback signature の 7-arg 規約 (`(desc, self, _, sndMtx, source, _, gateFlag)`)
  は推定、3 個適用済 callback の caller 側で確証要

#### 副次成果
- `add_struct_field` INSERT 動作の事故性は再確認 (Phase 1 教訓を Phase 2 で踏襲、ad-hoc 拡張回避)
- vestigial signature (Session 41 で Quake/SlotC TryArm が 4-arg pass-through) は Phase 2 でも頻出、
  decompile prototype を信じず disassembly の register touch を確認すべし


### Session 31 完了分 (2026-05-18、12 件 + 4 副次) — NamCam_Init + ISESlot lifecycle + CoinEvent SE family

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80049860 | FUN_80049860 | NamCam_Init | g_namCamSystem alloc + IP/port (HTTP 80) 設定 |
| 0x80049940 | FUN_80049940 | ItemSpawner_TimedRemapAndSpawn | timer + 13-way id remap → ItemObject_SpawnWithAlias |
| 0x80049a90 | FUN_80049a90 | ISESlot_TickIfActiveNotBattle6 | BATTLE mode 6 を除外したスロット tick |
| 0x80049cc4 | FUN_80049cc4 | ISESlot_SetByte18c | armed/item gate 付きで ItemObject+0x18c に書き込み |
| 0x80049e40 | FUN_80049e40 | ISESlot_Dtor | KartDriver 10-slot を tear-down する destructor |
| 0x80049ec4 | FUN_80049ec4 | ISESlot_Construct | 0x24 byte ISESlot の zero-init |
| 0x80049edc | FUN_80049edc | CoinJumpFlasher_Toggle | self+0x20 が armed の間 +0xff を toggle、g_finalLapCoinJumpEnabled に伝搬 |
| 0x80049f34 | FUN_80049f34 | CoinJumpFlasher_SetArmed | self+0x20 armed gate setter |
| 0x80049f60 | FUN_80049f60 | CoinEvent_PlayResultSE | character 0xc (Mametch) で SE 4、その他 0xf or 0x13 を分岐再生 |
| 0x80049fdc | FUN_80049fdc | CoinEvent_PlayAlternatingSE | SE 7/8 を ping-pong 再生 |
| 0x8004a04c | FUN_8004a04c | CoinEvent_PlaySE_0x13 | SE 0x13 単発再生 |
| 0x8004a074 | FUN_8004a074 | DebugOverlay_KartPhysicsForLocalPlayer | g_playerCarObject 非 NULL なら DebugOverlay_KartPhysics(+0x28) |

副次 renames (FUN_80049xxx が依存していて意味判明したもの):
| 0x800dd89c | FUN_800dd89c | ItemObject_SetField168 | item+0x168 u32 setter |
| 0x800dd8ac | FUN_800dd8ac | ItemObject_SetPair180 | item+0x180/+0x184 pair setter |
| 0x800dd930 | FUN_800dd930 | ItemObject_SetByte18c | item+0x18c byte setter |
| 0x80061804 | FUN_80061804 | FinalLapCoinJump_SetEnable | coinSys+0x18 enable + g_finalLapCoinJumpEnabled mirror |

主要発見:
- **ISESlot lifecycle 確定** — KartDriver は driver[0xb7..0xc0] (= driver+0x2dc..+0x300) で
  10 個の ISESlot を所有。各 ISESlot は 0x24 byte で:
    +0x04 byte = armed flag
    +0x10/+0x1c = scratch
    +0x14 = effect type id (0x51 だけ Dtor で end-signal を skip)
    +0x18 = bound ItemObject handle
  Construct → Tick (variants) → SetByte18c → Dtor の cycle。
- **NamCam = network camera client** が確定: HTTP (port 80) で acInetAddr 経由の
  IP に接続。Triforce arcade のオンライン写真サービス互換の挙動。
  FUN_8007413c(0) が NamCam サーバの hostname/IP 文字列を返す getter。
- **CoinJumpFlasher**: g_finalLapCoinJumpEnabled を per-frame で 0/1 toggle する
  blink subsystem。CoinSystem (FinalLapCoinJump_SetEnable) と連動。
- **CoinEvent SE family** (4 関数): 13 / 7 / 8 / 4 / 0xf の SE を変則的に再生する 4 つの
  trigger。character (Mametch=0xc) で SE 4 への分岐あり、ping-pong (7↔8)、固定 0x13
  の 3 variant。

副次 rename 候補:
  FUN_8007413c → NamCam_GetServerHost / NamCam_GetServerAddress?
  WrapInRange は既存名 → ping-pong index helper
  SoundObj_PlaySE は既存名

### Session 30 完了分 (2026-05-18、14 件) — CourseEnvironment update/dtor + JObj eval helpers + NamCam shutdown

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x800471b0 | FUN_800471b0 | CourseEnvironment_UpdateAndCullZones | env per-frame update + zone-based jobj Show/Hide |
| 0x80047608 | FUN_80047608 | CourseEnvironment_UpdateAndCullZones_Timed | timed wrapper、metrics slot 0x1f |
| 0x800476a0 | FUN_800476a0 | CourseEnvironment_ResolveJointInSlot0 | scene+0x50 (primary clN) で joint name lookup |
| 0x80048378 | FUN_80048378 | CourseEnvironment_ZeroInit | scene root の zero-init (4 × 0x28-word block loop 含む) |
| 0x800484c0 | FUN_800484c0 | CourseEnvironment_Dtor | scene tear-down (8 anim-jobj + 6 singleton subsystem + 0x20-slot table 全 free) |
| 0x80048798 | FUN_80048798 | JObj_EvalIfDirtyAndGetMtxPtr | assert + cond FUN_802d1e34 + return jobj+0x44 (local mtx) |
| 0x80048820 | FUN_80048820 | JObj_EvalIfDirty | side-effect only variant |
| 0x80048890 | FUN_80048890 | JObj_NeedsEval | predicate `(flags & 0x800000)==0 && (flags & 0x40)!=0` |
| 0x800488f4 | FUN_800488f4 | Class803f7494_Dtor | vtable @ 0x803f7494 の class virtual dtor (class identity unconfirmed) |
| 0x8004893c | FUN_8004893c | FUN_8004893c_TrampolineTo_801ba110 | FUN_801ba110 への 1-line forwarder (placeholder 名) |
| 0x8004895c | FUN_8004895c | AcError_LogIfError | acGetLastError 経由のエラーコード分岐 DebugPrintf |
| 0x80048c34 | FUN_80048c34 | Asset_LoadAndDecompressByType | path + decoderType(0..3) dispatch で asset load+decompress |
| 0x80049628 | FUN_80049628 | NamCam_TickShutdownCountdown | 120-frame countdown による NamCam 段階的 free |
| 0x80049718 | FUN_80049718 | NamCam_End | namcam_end 文字列、2-phase 120fr spin + 同期 free |

主要発見:
- **CourseEnvironment system** の lifecycle 4 関数確定:
  - ZeroInit → CourseScene_Load (既 named) → UpdateAndCullZones (毎フレーム) → Dtor
  - scene root の field map:
    +0x14/+0x1c = primary/alt jobj、+0x4c = clN driver table、+0x50..+0x6c = 8 anim jobj、
    +0x74 = view mtx scratch、+0xb0/+0xb4/+0xb8 = decal/billboard/clRom、
    +0xbc = zone-mask table、+0xc0..+0x340 = 0x20-slot × 0x14 visibility counter、
    +0x340..+0x35c = 8-slot env-zone、+0x360..+0x374 = 6 singleton subsystems
- **JObj eval helper triplet** が ObjectChain_RenderInZRange / KartDriver_TickAction で
  使われている idiom の primitive。`(flags & 0x800000)==0 && (flags & 0x40)!=0` で
  "needs eval" 判定、FUN_802d1e34 で評価 cascade。
- **NamCam system** (DAT_806d1070): namcam-end 文字列確認、120-frame countdown 付き
  shutdown sequence。同期版 NamCam_End と tick 版 NamCam_TickShutdownCountdown のペア。
- **Class803f7494_Dtor**: 0x803f7494 の vtable を持つ小型 class の virtual dtor。
  xref tool 復活後に class identity を pin して proper name に置き換えるべき。

副次 rename 候補:
  FUN_802d1e34 → JObj_EvalIfDirtyAndUpdate (recurring caller from 3 helpers above)
  FUN_8025d1b8 → store view mtx into ctx scratch
  FUN_80174c30 / FUN_80174090 / FUN_8017a614 / FUN_8017acf4 → env effect dtors
  FUN_802406a4 → HUD-light dtor
  acGetLastError は既存名 → acIPC error layer
  FUN_8029ab64 / aeec / af5c / b3e4 / b54c / ba2c / bcf0 → asset decompressor backend (4 types)

deferred:
  0x8004893c は raw forwarder; FUN_801ba110 の意味 (callee の文脈) に依存する。
  xref tool 復活後に決定。プレースホルダ名で残す。
  0x800488f4 (Class803f7494_Dtor) も同様、xref で class identity を pin する必要あり。

### Session 29 完了分 (2026-05-18、14 件 + 1 deferred) — KartDriver new/dtor chain + course env renderer

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80044d50 | FUN_80044d50 | KartDriver_InitFull | full kart driver init (robo-mario / bazooka / local-player extras 込み) |
| 0x80045950 | FUN_80045950 | KartDriver_Dtor | kart driver tear-down (10-slot aux + 0x31 secondary clN free 含む) |
| 0x80045b50 | FUN_80045b50 | KartDriver_Construct | zero-init + 10 × 0x24 aux struct allocate |
| 0x80045e20 | FUN_80045e20 | KartDriver_New_Full | Construct → InitFull ctor chain |
| 0x80045e88 | FUN_80045e88 | KartDriver_New_Empty | Construct のみの bare ctor |
| 0x80045eb8 | FUN_80045eb8 | GetItemDataEntry_Bounded | itemId < 0x115 ガード付き GetItemDataEntry |
| 0x80046050 | FUN_80046050 | ItemCategoryBudget_Decrement | item_data[5] カテゴリで budget[1..4] を decrement + 0 clamp |
| 0x800462e8 | FUN_800462e8 | PathIndex_AdvanceCircular | (path[1] + delta) mod path[0] (loop add/sub) |
| 0x8004632c | FUN_8004632c | Path_GetCurrentIndex | path[1] getter (Path_GetTotalCount のペア) |
| 0x80046960 | FUN_80046960 | ObjectChain_RenderInZRange | 4-level unrolled chain + 末端 recursion で z-cull render |
| 0x80046d84 | FUN_80046d84 | Subsystem36c_DispatchPass4_Timed | parent[0x36c].vtbl[4](self,4) + metrics slot 0x21 |
| 0x80046e38 | FUN_80046e38 | Subsystem36c_DispatchPass2_Timed | 同 arg=2 variant |
| 0x80046eec | FUN_80046eec | CourseEnvironment_RenderObjects | scene 0xd0/0x12/0x13/0x2f 系の object table を render |
| 0x80047118 | FUN_80047118 | CourseEnvironment_RenderObjects_Timed | 上記の timed wrapper、metrics slot 0x20 |

主要発見:
- **KartDriver constructor chain** 確定:
  - `KartDriver_New_Full = KartDriver_Construct + KartDriver_InitFull`
  - `KartDriver_New_Empty = KartDriver_Construct` のみ
  - `KartDriver_Construct` は driver[0xb7..0xc0] に 10 × 0x24 aux 確保 (`KartDriver_Dtor` の
    10-slot free 対応) + 初期値 (-1 sentinels, 999 marker, FLOAT_806d2540=1.0 等) を流し込む
- **CourseEnvironment_RenderObjects** は course の per-frame 環境 object renderer。8-slot
  pre-render array + 0x1c-stride 可変長 table + 0x20 × 0x14-stride 固定 table の 3 種類の
  chained-object structure を持つ。`ObjectChain_RenderInZRange` を呼び出す。
- **path index API**: `Path_GetTotalCount(@+0) / Path_GetCurrentIndex(@+4) /
  PathIndex_AdvanceCircular` の triplet が AI waypoint cursor の primitive。

副次 rename 候補:
  FUN_801746a8 / FUN_8017a52c / FUN_8017aa80 → env object 系 pre/effect/post hook
  FUN_802d07d4 / FUN_802d0b48 → JObj layer mask manip (HSD?)
  FUN_8016b0a0 / FUN_8016b0c4 → blend setup save/restore?
  FUN_8023ff18 → HUD/light per-slot update
  FUN_80048820 / FUN_80048890 / FUN_80048798 → ObjectChain サブ helper
  FUN_8025df40 → likely PSMTXMultVec (view xform)

諦めリスト追加:
- 0x80045ff8: `(self[0x15] == 1) ? self[6] : -1` 形の 1-line accessor。命名のために
  必要な構造体型 / xref tool が未利用 (mcp__ghidra__get_xrefs_to 等が disconnected)、
  flag-byte の意味が code-only からは確定不能。後続セッションで xref tool が
  復活したら再着手。

### Session 28 完了分 (2026-05-18、10 件) — KartDriver TickAction variants + Render dispatcher

| Address | 旧名 | 新名 | カテゴリ |
|---|---|---|---|
| 0x80042030 | FUN_80042030 | KartDriver_TickAction_79234 | slot 0xc3 ガード付き FUN_80079234 forwarder |
| 0x80042080 | FUN_80042080 | KartDriver_TickAction_79244 | 同 FUN_80079244 forwarder |
| 0x800420d0 | FUN_800420d0 | KartDriver_TickAction_79260 | 同 FUN_80079260 forwarder |
| 0x80042120 | FUN_80042120 | KartDriver_TickAction_79268_AndSetSlot5aEulerZ | FUN_80079268 forwarder + slot 0x5a JObj Euler Z setter |
| 0x80042238 | FUN_80042238 | KartDriver_TickAction_78778 | slot 0xc3 ガード付き FUN_80078778 forwarder |
| 0x80042288 | FUN_80042288 | KartDriver_TickAction_78a3c | 同 FUN_80078a3c forwarder |
| 0x800422d8 | FUN_800422d8 | KartDriver_TickAction_78d00 | 同 FUN_80078d00 forwarder |
| 0x80042328 | FUN_80042328 | KartDriver_GetJObjRootIfReady | (self[0]!=0 && self[3]!=0) ? self[0] : 0 |
| 0x80042350 | FUN_80042350 | KartDriver_Render | per-pass renderer: frustum cull + LOD + helper JObj + secondary draws |
| 0x80042610 | FUN_80042610 | KartDriver_RenderTimed | KartDriver_Render + MetricsTable slot 0x17 |

主要発見:
- **TickAction forwarder family**: 7 個の near-identical 関数 (0x80042030/80/d0/120/238/288/2d8) が
  すべて `if (loaded) { if (self[0xc3] != 0) FUN_80079xxx(); return 1; } else return 0;` の
  パターン。各々が異なる FUN_80079xxx 系 action (item slot 系処理?) に dispatch。
  特に 0x80042120 だけ slot 0x5a の JObj Euler Z を `self[0x5c] + deltaZ` で更新する
  side-effect 付き (dirty-mark idiom も含む)。
- **KartDriver_Render** (0x80042350) は per-pass renderer dispatcher:
  - passMask bit semantic: <2 || ==4 で 不透明系 LOD mesh、<2 || ==3 で helper、
    pre-race 中は LOD なし。
  - LOD distance gate: FLOAT_806d25d0 (cull dist) → 25d4 (LOD0/1) → 25d8 (LOD1/2) → 25dc。
  - JObj slot map: [0]=primary, [1]=LOD1, [2]=LOD2, [3]=helper(必須), [7]=optional helper
    (self[0x5d] gate), [8]=second optional (self[0x54] gate)。
  - 副次 dispatch: self[0xd7] → FUN_80231d4c、self[0xd8] → FUN_80245700。
- **KartDriver_RenderTimed** (0x80042610) は MetricsTable slot 0x17 = "per-render ms" 計測。
  これで KartDriver の per-frame profiling 経路の一端が見える。

副次 rename 候補:
  FUN_80079234/244/260/268 / FUN_80078778/8a3c/8d00 → ISE action handlers (slot 0xc3 系)
  FUN_8025e3c4 → frustum cull テスト
  FUN_801375d8 → 距離ベース vis test
  FUN_80231d4c / FUN_80245700 → KartDriver helper renderer (d7/d8 系)

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

## 累計 (Session 1-51)

合計 **515 件処理** (rename ~502、諦め ~9、プレースホルダ rename 6) / 1500 件 ≒ **34.3%**

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
