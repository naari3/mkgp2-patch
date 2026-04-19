#include <kamek.h>
#include "patch_common.h"
#include "generated_cup_courses.h"

// clFlowCup (GP cup selection, scene state 0x15) has a tri-page experiment:
//   page 0: omote (8 cups)
//   page 1: ura   (same 8 cups with ウラ UI sprites) — exists in vanilla
//   page 2: "page 3" added here, reuses page 1's sprite state (placeholder)
//
// The page state is a single byte at struct offset +0x118 (0/1/2). Original
// code only handles 0<->1 transitions through two dedicated blocks in
// clFlowCup_Update (0x801c7cfc-0x801c7d8c forward, 0x801c7d90-0x801c7e14
// backward). We replace both blocks with Kamek branches and extend the state
// machine.
//
// Cursor-7 unlock gates at +0x154 (omote page) and +0x155 (ura page) are
// forced to 1 every frame via an Update entry hook, so the test cursor can
// traverse 0..7 on all pages regardless of save data state.

typedef unsigned char u8;
typedef unsigned int  u32;
typedef signed int    i32;

extern "C" {
    // Game funcs reused for forward/backward sprite swaps (page 0<->1 only).
    void Sprite_SetupAnim(void* sprite, int animId, int a, int b);
    int  Sprite_SetAnimParam(void* sprite, short paramId, short value);
    // Vanilla signature: writes (cupId, longRoundFlag, courseVariantIdx, reverseRoundFlag)
    // to globals (g_cupId, g_longRoundFlag, g_courseVariantIdx, g_reverseRoundFlag).
    int  SetCourseParams(int cupId, int longRound, int variantIdx, int reverseRound);
}

// Scene field offsets (clFlowCup struct, 0x158 bytes total).
static const int OFF_CURSOR     = 0x004;
static const int OFF_SUBSTATE   = 0x00c;
static const int OFF_FRAME_CTR  = 0x014;
static const int OFF_PAGE_FLAG  = 0x118;
static const int OFF_S148       = 0x148;  // sprite pointer used in page swap
static const int OFF_S14C       = 0x14c;
static const int OFF_S150       = 0x150;
static const int OFF_GATE_FLAG0 = 0x154;  // enables cursor>6 on page 0 (omote)
static const int OFF_GATE_FLAG1 = 0x155;  // enables cursor>6 on page 1 (ura)

static inline u8&  U8(void* p, int o)  { return *(u8*)((u8*)p + o); }
static inline i32& I32(void* p, int o) { return *(i32*)((u8*)p + o); }
static inline void*& PTR(void* p, int o) { return *(void**)((u8*)p + o); }

// Note: custom _line.bin filenames are installed into DAT_8032890c at boot
// time via kmWritePointer records emitted by gen_cup_courses_header.py
// (see generated_cup_courses.h). No runtime install is needed; the previous
// scene-hooked installer was a silent no-op in practice (write happened but
// did not persist into the race scene — root cause unclear, but compile-time
// records sidestep the question entirely).

// --------- Update entry: force both gate flags open every frame ----------
extern "C" void CupForceGates(void* scene) {
    EnsureDBATWidened();
    if (!scene) return;
    U8(scene, OFF_GATE_FLAG0) = 1;
    U8(scene, OFF_GATE_FLAG1) = 1;
}

// Replaces the original instruction at 0x801c772c (stwu r1, -0x30(r1)).
asm void CupUpdateEntryHook() {
    nofralloc
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r31, 0x1c(r1)
    mr   r31, r3
    bl   CupForceGates
    mr   r3, r31
    lwz  r31, 0x1c(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    stwu r1, -0x30(r1)
    blr
}

kmBranch(0x801c772c, CupUpdateEntryHook);
kmPatchExitPoint(CupUpdateEntryHook, 0x801c7730);

// --------- Forward transition: cursor>7 handling ------------------------
// Replaces 0x801c7cfc..0x801c7d8c (inclusive) and exits to 0x801c7d90.
extern "C" void CupForwardTransition(void* scene) {
    i32 cursor = I32(scene, OFF_CURSOR);
    if (cursor <= 7) return;

    I32(scene, OFF_CURSOR) = 7;  // clamp
    u8 flag = U8(scene, OFF_PAGE_FLAG);

    if (flag == 0) {
        // page 0 (omote) -> page 1 (ura): swap label sprites to ウラ IDs.
        Sprite_SetupAnim(PTR(scene, OFF_S148), 0x18f, 1, 0);
        Sprite_SetupAnim(PTR(scene, OFF_S14C), 0x18e, 1, 0);
        Sprite_SetupAnim(PTR(scene, OFF_S150), 0x1c7, 1, 0);
        U8(scene, OFF_PAGE_FLAG) = 1;
        I32(scene, OFF_CURSOR)    = 0;
        I32(scene, OFF_SUBSTATE)  = 4;
        I32(scene, OFF_FRAME_CTR) = 0;
    } else if (flag == 1) {
        // page 1 (ura) -> page 2 (placeholder page 3): reuse ウラ sprites as-is.
        U8(scene, OFF_PAGE_FLAG) = 2;
        I32(scene, OFF_CURSOR)    = 0;
        I32(scene, OFF_SUBSTATE)  = 4;
        I32(scene, OFF_FRAME_CTR) = 0;
        DebugPrintfSafe("MKGP2: cup page 1 -> 2 (new page)\n");
    }
    // flag == 2: terminal, cursor already clamped above.
}

asm void CupForwardHook() {
    nofralloc
    stwu r1, -0x10(r1)
    mflr r0
    stw  r0, 0x14(r1)
    mr   r3, r30
    bl   CupForwardTransition
    lwz  r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    blr
}

kmBranch(0x801c7cfc, CupForwardHook);
kmPatchExitPoint(CupForwardHook, 0x801c7d90);

// --------- Backward transition: cursor<0 handling -----------------------
// Replaces 0x801c7d90..0x801c7e14 (inclusive) and exits to 0x801c7e18.
extern "C" void CupBackwardTransition(void* scene) {
    i32 cursor = I32(scene, OFF_CURSOR);
    if (cursor >= 0) return;

    I32(scene, OFF_CURSOR) = 0;
    u8 flag = U8(scene, OFF_PAGE_FLAG);

    if (flag == 1) {
        // page 1 (ura) -> page 0 (omote): swap label sprites back to オモテ IDs.
        Sprite_SetupAnim(PTR(scene, OFF_S148), 0x185, 1, 0);
        Sprite_SetupAnim(PTR(scene, OFF_S14C), 0x184, 1, 0);
        Sprite_SetupAnim(PTR(scene, OFF_S150), 0x1dd, 1, 0);
        Sprite_SetAnimParam(PTR(scene, OFF_S150), 0x1ad4, 0x1ae3);
        U8(scene, OFF_PAGE_FLAG) = 0;
        I32(scene, OFF_CURSOR)    = 7;
        I32(scene, OFF_SUBSTATE)  = 4;
        I32(scene, OFF_FRAME_CTR) = 0;
    } else if (flag == 2) {
        // page 2 -> page 1 (ura): sprites stay on ウラ IDs, no swap.
        U8(scene, OFF_PAGE_FLAG) = 1;
        I32(scene, OFF_CURSOR)    = 7;
        I32(scene, OFF_SUBSTATE)  = 4;
        I32(scene, OFF_FRAME_CTR) = 0;
        DebugPrintfSafe("MKGP2: cup page 2 -> 1\n");
    }
}

asm void CupBackwardHook() {
    nofralloc
    stwu r1, -0x10(r1)
    mflr r0
    stw  r0, 0x14(r1)
    mr   r3, r30
    bl   CupBackwardTransition
    lwz  r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    blr
}

kmBranch(0x801c7d90, CupBackwardHook);
kmPatchExitPoint(CupBackwardHook, 0x801c7e18);

// --------- Course selection: page-aware SetCourseParams dispatch --------
// Replaces the 8-way cursor dispatch at 0x801c80f4..0x801c81f0 (each cursor
// slot has an identical `li r3,N / bl SetCourseParams` block). We fold them
// into a table lookup, and when page==2 we override with course 0 (test_course).
// Exits to 0x801c81f4 which continues with state = 0x1b (next scene).
static const int CUP_COURSE_BY_CURSOR[8] = { 7, 1, 8, 2, 3, 4, 5, 6 };

extern "C" void CupSelectDispatch(void* scene) {
    i32 cursor = I32(scene, OFF_CURSOR);
    u8  flag   = U8(scene, OFF_PAGE_FLAG);

    int cupId;
    if (cursor < 0 || cursor >= 8) return;
    if (flag == 2) {
        cupId = kCupPage2Courses[cursor];
        DebugPrintfSafe("MKGP2: page2 cursor=%d -> cup %d\n", cursor, cupId);
    } else {
        cupId = CUP_COURSE_BY_CURSOR[cursor];
    }

    SetCourseParams(cupId, 0, 0, 0);
}

asm void CupSelectHook() {
    nofralloc
    stwu r1, -0x10(r1)
    mflr r0
    stw  r0, 0x14(r1)
    mr   r3, r30
    bl   CupSelectDispatch
    lwz  r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    blr
}

kmBranch(0x801c80f4, CupSelectHook);
kmPatchExitPoint(CupSelectHook, 0x801c81f4);

// --------- Allow cupId=0 (test_course) in PathData_GetByContext --------
// Vanilla guard at 0x801dc528 is `cmpwi r0, 1` followed by `blt dummy` at
// 0x801dc530, which rejects cupId < 1 and returns a 1-point dummy path.
// For test_course (cupId=0) this causes every kart's pathFollower to
// read the dummy, marking progress as already complete -> immediate goal.
// Relax the bound to `cmpwi r0, 0` so 0..8 are all accepted.
//   old: 0x2C000001  (cmpwi r0, 1)
//   new: 0x2C000000  (cmpwi r0, 0)
kmWrite32(0x801dc528, 0x2c000000);

// --------- Race-params hook: override RaceScene_Init's table load ---------
// Vanilla reads three fields from the 0x8040E7D0-based table:
//   0x800a24ac: lbz  r0, -0x48(r4)   ; lap byte
//   0x800a24b0: lfs  f1, -0x44(r4)   ; race time
//   0x800a24b4: lfs  f0, -0x40(r4)   ; lap bonus
//   0x800a24b8: extsb r0, r0         ; sign-extend to int
// cupId=0's slots are occupied by g_GameModeBaseVtable (0x8040E7BC),
// so writing lap=3 into 0x8040E7C4 corrupts vtable[2] -> ISI at 0x030A07EC.
// We replace the three loads with a call that synthesizes r0/f1/f0 from
// kRaceParamOverrides[], falling back to the vanilla table for unlisted
// cupIds. extsb at 0x800a24b8 then runs on our already-positive lap
// byte as a no-op; the subsequent stores at 0x800a24dc/e0/e4 commit into
// g_totalLaps/g_raceTimeRemaining/g_lapBonusTimeDelta unchanged.
extern "C" void ResolveRaceParams(int* outLap, float* outTime, float* outBonus) {
    EnsureDBATWidened();
    u32 cupId        = *(u32*)0x806cf108;  // g_cupId
    u32 ccClass      = *(u32*)0x806d12cc;  // g_ccClass
    u32 longRound    = *(u32*)0x806d1268;  // g_longRoundFlag (round picks short(0)/long(1) variant)

    for (int i = 0; i < kRaceParamOverrideCount; ++i) {
        const RaceParamOverride& p = kRaceParamOverrides[i];
        if ((u32)p.cupId == cupId) {
            *outLap   = p.laps;
            *outTime  = p.time;
            *outBonus = p.bonus;
            return;
        }
    }

    const unsigned char* vp = (const unsigned char*)(
        0x8040E7D0u + cupId * 0x48u + ccClass * 0x18u + longRound * 0xcu - 0x48u
    );
    *outLap   = (signed char)vp[0];
    *outTime  = *(const float*)(vp + 4);
    *outBonus = *(const float*)(vp + 8);
}

asm void RaceParamsHook() {
    nofralloc
    stwu r1, -0x30(r1)
    mflr r11
    stw  r11, 0x34(r1)
    addi r3, r1, 0x20
    addi r4, r1, 0x24
    addi r5, r1, 0x28
    bl   ResolveRaceParams
    lwz  r11, 0x34(r1)
    mtlr r11
    lwz  r0,  0x20(r1)
    lfs  f1,  0x24(r1)
    lfs  f0,  0x28(r1)
    addi r1,  r1, 0x30
    blr
}

kmBranch(0x800a24ac, RaceParamsHook);
kmPatchExitPoint(RaceParamsHook, 0x800a24b8);

// --------- AI lap-speed bonus override ---------------------------------
// AI_CalcLapSpeedBonus (0x801dd480) walks a per-cup rule table indexed
// by (cupId-1). For unmapped cupIds (0=test_course, 9+) it
// dereferences (&PTR_DAT_803bcbac)[-1] = adjacent rodata as a rule-entry
// pointer and walks 0x14-byte strides until hitting sentinel -100. In
// practice it never finds the sentinel and floods PC=0x801dd5c8 with
// invalid reads (~thousands per frame on test_course).
//
// Hook flow at function entry (0x801dd480):
//   1. Lookup g_cupId in kAILapBonusOverrides[]
//   2. If hit: return the override (double) directly to the caller
//   3. If miss: execute the original `stwu r1,-0x20(r1)` and bctr to
//      the function body at 0x801dd484 so the vanilla walk still runs
extern "C" int AILapBonusLookup(double* outValue) {
    EnsureDBATWidened();
    u32 cupId = *(u32*)0x806cf108;  // g_cupId
    for (int i = 0; i < kAILapBonusOverrideCount; ++i) {
        if ((u32)kAILapBonusOverrides[i].cupId == cupId) {
            *outValue = kAILapBonusOverrides[i].value;
            return 1;
        }
    }
    return 0;
}

asm void AICalcLapBonusHook() {
    nofralloc
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r3, 0x10(r1)
    stw  r4, 0x14(r1)
    stw  r5, 0x18(r1)
    stw  r6, 0x1C(r1)
    addi r3, r1, 0x8
    bl   AILapBonusLookup
    cmpwi r3, 0
    bne  override_path
    lwz  r3, 0x10(r1)
    lwz  r4, 0x14(r1)
    lwz  r5, 0x18(r1)
    lwz  r6, 0x1C(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    stwu r1, -0x20(r1)
    lis  r12, 0x801D
    ori  r12, r12, 0xD484
    mtctr r12
    bctr
override_path:
    lfd  f1, 0x8(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    blr
}

kmBranch(0x801dd480, AICalcLapBonusHook);

// --------- WeatherSystem_Init: full replacement for custom-track BGM ----
// Vanilla WeatherSystem_Init (0x8016c730) uses a jump table at 0x804910BC
// keyed on g_cupId to populate the weather state's bgmIdList pointer
// (state + 0x04). Slot 0 is unassigned and the follow-up `== 0` fallback
// fails to fire because the heap garbage left there is rarely zero, so
// cupId=0 races (test_course / any custom track) feed ClSound_PlayBgm-
// Stream a junk pointer every frame.
//
// We branch the function entry to WeatherInitCustom, which:
//   1. Replays the original prologue effect (state[0]=cupType,
//      state[1]=WeatherSystem_PickVariant(-1)).
//   2. Looks up our custom track table; if cupId matches, points
//      state+4 at the per-track CustomBgmPair (kCustomBgmPairs).
//   3. Otherwise falls back to the vanilla DAT_806d8a80..ac0 tables
//      (so cupId 1..16 still play the right BGM under our hook).
//   4. Replays the rest of vanilla state init (rain/wind/thunder
//      fade floats, sentinels, DAT_806cfa28 = 1).
// Exit point is 0x8016c84c (the trailing blr) so vanilla's epilogue is
// skipped — our asm hook does its own frame teardown.
extern "C" int WeatherSystem_PickVariant(int forcedId);

extern "C" void* WeatherInitCustom(void* state, int cupType) {
    EnsureDBATWidened();

    *(u8*)state                   = (u8)cupType;
    *((u8*)state + 1)             = (u8)WeatherSystem_PickVariant(-1);

    u32 cupId = *(u32*)0x806cf108;
    const void* bgmPair = 0;
    for (u32 i = 0; i < kCustomTrackCount; ++i) {
        if (kCustomTracks[i].cupId == cupId) {
            bgmPair = (const void*)kCustomTracks[i].bgmPair;
            break;
        }
    }
    if (bgmPair == 0) {
        // Vanilla fallback: cup 1..8 use per-cup tables at DAT_806d8a80+,
        // cup 9..16 share DAT_806d8ac0, anything else gets the generic
        // DAT_806d8ac8 (matches the original `== 0` fallback's intent).
        if (cupId >= 1 && cupId <= 8) {
            bgmPair = (const void*)(0x806d8a80u + (cupId - 1) * 8);
        } else if (cupId >= 9 && cupId <= 16) {
            bgmPair = (const void*)0x806d8ac0u;
        } else {
            bgmPair = (const void*)0x806d8ac8u;
        }
    }
    *(const void**)((u8*)state + 4) = bgmPair;
    // NOTE: do NOT touch DAT_806d175d here. That byte is the voice system's
    // "init-done" sentinel read by FUN_801b6c94 (called later from
    // RaceScene_Init); writing 1 prematurely makes that initializer skip
    // queue reset / per-player state / DAT_806cfd38=-1 / per-cup DAT_806d1754
    // setup, which silently kills FUN_801ad534's race event voices
    // (early returns on DAT_806cfd38 == -1).

    // Vanilla numeric init (FLOAT_806d8ad4 = 1.0, FLOAT_806d8ad8 = 0.0).
    float one  = *(float*)0x806d8ad4u;
    float zero = *(float*)0x806d8ad8u;
    *(u32*)((u8*)state + 0x08)   = 0;
    *(u32*)((u8*)state + 0x0C)   = 0;
    *((u8*)state + 0x10)         = 0;
    *(float*)((u8*)state + 0x14) = one;
    *((u8*)state + 0x18)         = 0;
    *(float*)((u8*)state + 0x1C) = one;
    *(u32*)((u8*)state + 0x20)   = 0;
    *(float*)((u8*)state + 0x24) = one;
    *(float*)((u8*)state + 0x28) = zero;
    *((u8*)state + 0x2C)         = 0;
    *(u32*)((u8*)state + 0x30)   = 0xFFFFFFFFu;
    *(u8*)0x806cfa28u            = 1;
    return state;
}

asm void WeatherInitHook() {
    nofralloc
    stwu r1, -0x10(r1)
    mflr r0
    stw  r0, 0x14(r1)
    bl   WeatherInitCustom
    lwz  r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    blr
}

kmBranch(0x8016c730, WeatherInitHook);
kmPatchExitPoint(WeatherInitHook, 0x8016c84c);

// --------- ClSound_PlayBgmStream: relocate the dsp pointer table -------
// Vanilla reads `(&PTR_s_bgm01_demoL_dsp_8037ce1c)[bgm_id*2]` at
// instruction sequence 0x80190c50..0x80190c5c:
//   addi   r5, r31, 0x3d1c    ; r5 = 0x8037CE1C  (vanilla table base)
//   rlwinm r0, r28, 3, 0, 28  ; r0 = bgm_id * 8
//   add    r3, r5, r0         ; r3 = &table[bgm_id*2]
//   lwz    r4, 4(r3)          ; r4 = R-channel pointer
// We branch this 4-instruction span to BgmTableLookupHook, which loads
// the patch's relocated kCustomBgmTable instead. Exit at 0x80190c60
// (the `cmplwi r4, 0` immediately after) so the rest of the function
// runs untouched, with r3/r4/r5/r0 all set up the same way.
//
// The vanilla 0x8037CE1C array sits 8 bytes below "clStream::setSpeed("
// debug strings — extending it in place would clobber rodata, so we
// instead hardcode all 21 vanilla entries into kCustomBgmTable plus the
// per-track new entries. ClSound_PlayBgmStream's `cmplwi r28, 0x15`
// upper bound is raised by a kmWrite32 emitted from generated_*.h.
asm void BgmTableLookupHook() {
    nofralloc
    lis    r5, kCustomBgmTable@h
    ori    r5, r5, kCustomBgmTable@l
    rlwinm r0, r28, 3, 0, 28
    add    r3, r5, r0
    lwz    r4, 0x4(r3)
    blr
}

kmBranch(0x80190c50, BgmTableLookupHook);
kmPatchExitPoint(BgmTableLookupHook, 0x80190c60);

// --------- ClStream_PlayMono: NULL-path defensive guard -----------------
// Vanilla ClStream_PlayMono (0x80195050) trusts its `path` argument and
// passes it straight to DVDOpen/DVDConvertPathToEntrynum. Several voice/
// announcer drivers (FUN_801b5e18 etc.) iterate stream tables that, on
// cupId=0, are never properly populated; they end up calling here with
// a NULL or tiny garbage path (observed values: 0x00000000, 0x00010101)
// once per frame, causing PC=0x80294F00 invalid reads.
//
// Hook replays the original prologue (frame allocate, save LR, save r27..
// r31, copy args into r28/r29/r30) so the rest of the function stays
// frame-correct, then bails out early when path (= r28) is NULL by
// jumping into vanilla's epilogue at 0x801951a0 with r3 = -1 (matching
// the function's "stream slot allocation failed" return convention).
//
// Kamek's PatchExit verifier rejects functions that contain a non-tail
// blr/blrl, so the early exit uses bctr (branch via CTR) to reach the
// epilogue — distinct opcode from blr, slips past the check.
asm void ClStreamPlayMonoGuard() {
    nofralloc
    // Vanilla prologue (0x80195050..0x80195068)
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stmw r27, 0xc(r1)
    or   r28, r3, r3
    or   r29, r4, r4
    or   r30, r5, r5

    cmplwi r28, 0
    bne   normal
    li    r3, -1
    lis   r12, 0x8019
    ori   r12, r12, 0x51a0     // vanilla epilogue: lmw/lwz/mtlr/addi/blr
    mtctr r12
    bctr
normal:
    blr                          // exit -> 0x8019506c
}

kmBranch(0x80195050, ClStreamPlayMonoGuard);
kmPatchExitPoint(ClStreamPlayMonoGuard, 0x8019506c);

// --------- Voice queue dequeue guard (FUN_801b5e18) ---------------------
// The announcer queue at DAT_80678d90 holds 6-byte entries with a 16-bit
// voice_id at +0. Vanilla dequeue at 0x801b6a98 handles voice_id == -1 by
// jumping to skip_block (0x801b6b74) without advancing tail — correct for
// the "queue empty" sentinel (tail==head path via `li r0,-1; b +0x10`)
// but wrong for a -2 (or any other negative) value sitting at tail<head.
// Observed: a -2 pushed into slot 3 while valid voices sit at slots 4..11;
// vanilla code loops on slot 3 forever, never plays the rest.
//
// The first attempt (simple cmpwi/blt rewrite) skipped -2 without
// advancing tail, leaving the queue permanently stuck.
//
// This hook replaces the vanilla cmpwi/beq pair with branching logic:
//   * voice_id >= 0  -> fall through to 0x801b6aa0 (normal play path)
//   * voice_id <  0  -> advance tail (with reset when tail>=head), then
//                       bctr to 0x801b6b74 (skip_block); last_voice=-1 as
//                       before. Tail advancement is the critical fix.
//
// Reset logic mirrors vanilla's 0x801b6b50..0x801b6b60: when the advanced
// tail catches up to head, both are zeroed so the next push starts at
// slot 0. This keeps the queue compact under normal operation.
extern "C" void VoiceDequeueAdvanceTail() {
    EnsureDBATWidened();
    u32 tail = *(u32*)0x806d1740u;
    u32 head = *(u32*)0x806d173cu;
    if (tail >= head) return;      // empty queue, nothing to dequeue
    tail += 1;
    if (tail >= head) {
        *(u32*)0x806d1740u = 0;
        *(u32*)0x806d173cu = 0;
    } else {
        *(u32*)0x806d1740u = tail;
    }
}

asm void VoiceDequeueGuard() {
    nofralloc
    cmpwi r27, 0
    bge   continue_play
    // negative voice_id: advance tail, then jump to skip_block
    stwu r1, -0x10(r1)
    mflr r0
    stw  r0, 0x14(r1)
    bl   VoiceDequeueAdvanceTail
    lwz  r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    lis  r12, 0x801b
    ori  r12, r12, 0x6b74
    mtctr r12
    bctr
continue_play:
    blr                              // -> 0x801b6aa0 via kmPatchExitPoint
}

kmBranch(0x801b6a98, VoiceDequeueGuard);
kmPatchExitPoint(VoiceDequeueGuard, 0x801b6aa0);

// --------- Root-cause fix: -2 push in FUN_801b0af4 ---------------------
// FUN_801b0af4 is the race-start voice intro pusher. After emitting three
// fixed "welcome" voices (0x280, 0x189, 0x280), it computes a round-intro
// voice id:
//
//     sVar6 = g_longRoundFlag + (g_cupId - 1) * 2
//
// The guard is `if (sVar6 != -1) push(sVar6, meta=0xFFFF)`. The formula
// maps cupId 1..8 × longRoundFlag 0..1 onto voice ids 0..15 (round-intro
// bank). For cupId=0 (test_course) the formula evaluates to -2, which is
// NOT -1, so it slips past the filter and gets pushed. Downstream voice
// dequeue then tries to play live_A01_dsp[-2 * 12] = garbage — the
// original PC=0x80294F00 invalid read and the stuck-queue symptom.
//
// Two-instruction kmWrite32 changes the predicate from `!= -1` to
// `>= 0`, eliminating the bogus push at its source. This is the proper
// structural fix; the VoiceDequeueGuard above now only runs as a
// defense-in-depth safety net (negative voice_ids should never reach the
// queue once this predicate is tightened).
//
//   0x801b0c08: cmpwi r3, -1  (0x2C03FFFF) -> cmpwi r3, 0  (0x2C030000)
//   0x801b0c0c: beq  +0xA8    (0x418200A8) -> blt  +0xA8    (0x418000A8)
kmWrite32(0x801B0C08, 0x2C030000);
kmWrite32(0x801B0C0C, 0x418000A8);
