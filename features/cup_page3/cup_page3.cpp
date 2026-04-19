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
