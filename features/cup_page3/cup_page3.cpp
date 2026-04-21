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

    // Vanilla data tables (resolved via externals.txt).
    extern const unsigned char kRaceParamsTable[];        // RaceParamsEntry[48] base
    extern const float kAIBaseSpeedTable_Race[];          // CupSpeedEntry[192] as flat floats
    extern const float kAIBaseSpeedTable_BattleTimeAttack[];
    extern const unsigned char kBgmIdList_Cup1[];         // BgmIdList per-cup, stride 8
    extern const unsigned char kBgmIdList_CupsExtra[];    // BgmIdList shared for cup 9..16
    extern const unsigned char kBgmIdList_DefaultFallback[];
    extern const float kWeatherInitFloatA;                // 18.0f (rain/wind/thunder init)
    extern const float kWeatherInitFloatB;                // 0.0f  (fade sentinel init)

    // Internal vanilla helpers used by CourseDataLoadCustom (kept unrenamed).
    void* FUN_8003b120(unsigned int size);                // MemoryManager alloc
    int   FUN_8007e344(const char* filename);             // DVD file-size query
    int   FUN_8007dfe4(const char* filename, void* buf,
                       unsigned int size, int p4, int p5);  // DVD load
    int   Path_CountWaypointsAndSnapGround(int* waypointArray);
    void  FUN_8007c56c(void* header, int zero);              // CourseSetupCallback dispatcher
}

// ---------- Custom track lookup helpers ----------
// kCustomTracks[] and its cupId/field plumbing live in
// generated_cup_courses.h (emitted from cup_courses.yaml).
static const struct CustomTrack* FindCustomTrack(unsigned int cupId) {
    for (unsigned int i = 0; i < kCustomTrackCount; ++i) {
        if (kCustomTracks[i].cupId == cupId) return &kCustomTracks[i];
    }
    return 0;
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

// --------- Widen cupId range in SetCourseParams ------------------------
// SetCourseParams (0x8009cbfc) validates `0 <= cupId <= 16` via:
//   0x8009cc20: cmpwi r3, 0x11    // 17
//   0x8009cc24: blt  0x8009cc30   // accept if < 17
// On failure it returns 0 and LEAVES g_cupId UNCHANGED. Our CupSelectDispatch
// passes cupId=17 for custom tracks, which silently no-ops and lets
// whatever stale g_cupId from the previous scene bleed into the round
// select / race path (observed: scene replays Yoshi round 4).
//
// Widen the cap to 0x80 (128) so cupId 17+ passes through and g_cupId
// is updated correctly.
//   0x8009cc20: cmpwi r3, 0x11  (0x2C030011) -> cmpwi r3, 0x80 (0x2C030080)
kmWrite32(0x8009CC20, 0x2C030080);

// --------- Widen cupId range in PathData_GetByContext ------------------
// Vanilla enforces `1 <= cupId <= 8` via two adjacent checks:
//   0x801dc528: cmpwi r0, 1  + blt dummy  (at 0x801dc530)
//   0x801dc534: cmpwi r0, 9  + blt accept (at 0x801dc538)
// Out-of-range -> returns kDummyOnePointPath, which pathFollower reads
// as "goal already reached" -> instant race finish. We widen both bounds
// so custom cupIds (17+) pass through. Accepted range becomes 0..127.
//
// PathData_GetByContext itself doesn't index by cupId — it just reads
// g_courseData and calls CourseData_GetPath. Our custom CourseData_LoadPathTable
// hook populates g_courseData for custom cupIds, so widening the gate
// here is all that's needed; no per-track data bleed.
//
//   0x801dc528: cmpwi r0, 1   (0x2C000001) -> cmpwi r0, 0   (0x2C000000)
//   0x801dc534: cmpwi r0, 9   (0x2C000009) -> cmpwi r0, 128 (0x2C000080)
kmWrite32(0x801dc528, 0x2C000000);
kmWrite32(0x801dc534, 0x2C000080);

// --------- Widen cupId range in CourseScene_Load -----------------------
// CourseScene_Load (0x800476d4) carries the same 0x10 upper-bound guard as
// SetCourseParams:
//   0x80047700: cmpwi r4, 0x11   (0x2C040011)
//   0x80047704: blt   continue
// Out-of-range (cupId >= 17) early-returns, skipping the mainArchive /
// collision / WarpDashMgr_GetOrCreate pipeline. The skipped
// WarpDashMgr_GetOrCreate leaves PTR_806cf238 NULL, so the next frame
// WarpDashMgr_GetInstance -> WarpZone_FindContaining reads 0x18(NULL)
// and traps (observed: Invalid read 0x00000018 @ PC 0x800a91a0).
//
// Widen to 0x80 so custom cupIds fall through to the normal load path.
// CourseScene_Load doesn't index vanilla tables by cupId directly — the
// per-asset pickers do, and we already hook all of those.
//   0x80047700: cmpwi r4, 0x11  (0x2C040011) -> cmpwi r4, 0x80 (0x2C040080)
kmWrite32(0x80047700, 0x2C040080);

// --------- Skip PTR_DAT_80324b10 dispatcher for custom cupIds ----------
// CourseScene_Load does an inline table lookup at 0x800480e0..0x800480e8:
//   lwzx r3, r3, r0              ; r3 = (&PTR_DAT_80324b10)[cupId*2 + longRound]
//   bl FUN_8007c56c              ; calls with (table_entry, 0)
// The table has 34 entries (cupId 0..16 × longRound 0..1). Immediately after
// sits rodata float constants (0x80324BA0 = 1.0f, 0x80324BA4 = 0.7f). For
// cupId=17, the lookup reads 0x80324B98 and hits 0x3F800000 (1.0f), then
// passes that as a pointer to FUN_8007c56c -> FUN_8007b878 which
// dereferences `param_2[1]` and crashes (observed: Invalid read from
// 0x3f800004 @ PC 0x8007b8ac).
//
// The table entries are course-setup callback headers of the form
// (data_ptr, count) at 0x806cf1c0..0x806cf1e8. We have no equivalent data
// for custom tracks, and the default vanilla entry at 0x806cf1e0 is a
// "no-op" struct (used by most cups), so the cleanest fix is to skip the
// FUN_8007c56c call entirely when cupId is in our custom list.
//
// Intercept the bl at 0x800480e8 via kmCall; the wrapper re-checks cupId
// and either forwards or returns. Note the OOB read still happens (lwzx at
// 0x800480e4) — but lwzx into r3 with r3=0x80324B98 just reads a valid
// rodata word (1.0f). The value is harmless as long as we don't pass it
// downstream, which is what this guard prevents.
extern "C" void CourseSetupCallbackGuard(void* header, int zero) {
    EnsureDBATWidened();
    unsigned int cupId = *(unsigned int*)0x806cf108u;
    if (FindCustomTrack(cupId)) return;   // skip OOB'd header for custom
    FUN_8007c56c(header, zero);
}
kmCall(0x800480e8, CourseSetupCallbackGuard);

// --------- WarpDashMgr_Init: skip cupId-indexed table reads for custom ----
// WarpDashMgr_Init (0x800a8d34) looks up two 17-entry cupId-indexed tables:
//   &DAT_80328310[cupId*8 + uVar4]   (warp table)
//   &DAT_80328398[cupId*8 + uVar4]   (dash table, 0x88 bytes = 17 cups later)
// Each table entry points at a (warpEntry*, count) pair; the function
// Reserves + populates the output vector from those entries.
//
// For cupId=17 the second read (0x80328398 + 0x88 + uVar4) lands at
// 0x80328420 — the rodata string "create warp offset...", which starts
// with bytes `63 72 65 61` = 0x63726561 ("crea"). The code then treats
// that string as an (entry*, count) pointer and dereferences — observed:
//   Invalid read from 0x63726561, PC = 0x800a8e94.   (first table loop)
//   Invalid read from 0x63726565, PC = 0x800a8e9c.
//
// NULL-returning the manager would re-introduce the earlier
// WarpDashMgr_GetInstance -> WarpZone_FindContaining NULL deref crash.
// Instead we substitute an EMPTY init: zero the warp/dash vectors and set
// the init-done flag. All downstream queries see "no warps, no dashes".
//
// Hook flow:
//   1. Wrapper saves args (r3..r7), calls WarpDashMgrInit_MaybeCustom.
//   2. If custom -> C did the empty init, wrapper returns.
//   3. If vanilla -> replay displaced stwu and bctr to 0x800a8d38.
extern "C" int WarpDashMgrInit_MaybeCustom(unsigned int* param_1, int param_2) {
    EnsureDBATWidened();
    if (!FindCustomTrack((unsigned int)param_2)) return 0;
    // Zero-init WarpDashMgr (same first 7 words as vanilla prologue).
    param_1[0] = 0;
    param_1[1] = 0;
    param_1[2] = 0;
    param_1[3] = 0;
    param_1[4] = 0;
    param_1[5] = 0;
    *(unsigned char*)(param_1 + 6) = 1;   // init-done sentinel
    return 1;
}

asm void WarpDashMgrInitHook() {
    nofralloc
    stwu r1, -0x30(r1)
    mflr r0
    stw  r0, 0x34(r1)
    stw  r3, 0x10(r1)
    stw  r4, 0x14(r1)
    stw  r5, 0x18(r1)
    stw  r6, 0x1c(r1)
    stw  r7, 0x20(r1)
    // r3 = param_1 (out manager), r4 = cupId — both already in place.
    bl   WarpDashMgrInit_MaybeCustom
    cmpwi r3, 0
    bne  custom_handled
    // vanilla path: restore args, tear down wrapper, replay prologue, bctr.
    lwz  r3, 0x10(r1)
    lwz  r4, 0x14(r1)
    lwz  r5, 0x18(r1)
    lwz  r6, 0x1c(r1)
    lwz  r7, 0x20(r1)
    lwz  r0, 0x34(r1)
    mtlr r0
    addi r1, r1, 0x30
    stwu r1, -0xe0(r1)            // vanilla's displaced first instruction
    lis  r12, 0x800a
    ori  r12, r12, 0x8d38         // vanilla + 4
    mtctr r12
    bctr
custom_handled:
    // Return param_1 as vanilla does.
    lwz  r3, 0x10(r1)
    lwz  r0, 0x34(r1)
    mtlr r0
    addi r1, r1, 0x30
    blr
}

kmBranch(0x800a8d34, WarpDashMgrInitHook);

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

    const unsigned char* vp =
        kRaceParamsTable + cupId * 0x48u + ccClass * 0x18u + longRound * 0xcu - 0x48u;
    *outLap   = (signed char)vp[0];
    *outTime  = *(const float*)(vp + 4);
    *outBonus = *(const float*)(vp + 8);
}

// Vanilla RaceScene_Init sets r3/r4/r5 BEFORE this block and reuses them
// afterwards: r3 = ProcessSystemTick string arg (0x800a24a4), r5 = -2 that
// 0x800a24d0 stores into g_raceResultCode. Our hook clobbers those regs to
// pass stack pointers into ResolveRaceParams, so we must save/restore them
// or the vanilla `stw r5, -0x7bb4(r13)` writes a stack address into
// g_raceResultCode and breaks the race-end gate (`g_raceResultCode == -2`).
asm void RaceParamsHook() {
    nofralloc
    stwu r1, -0x40(r1)
    mflr r11
    stw  r11, 0x44(r1)
    stw  r3,  0x30(r1)
    stw  r4,  0x34(r1)
    stw  r5,  0x38(r1)
    addi r3, r1, 0x20
    addi r4, r1, 0x24
    addi r5, r1, 0x28
    bl   ResolveRaceParams
    lwz  r11, 0x44(r1)
    mtlr r11
    lwz  r0,  0x20(r1)
    lfs  f1,  0x24(r1)
    lfs  f0,  0x28(r1)
    lwz  r3,  0x30(r1)
    lwz  r4,  0x34(r1)
    lwz  r5,  0x38(r1)
    addi r1,  r1, 0x40
    blr
}

kmBranch(0x800a24ac, RaceParamsHook);
kmPatchExitPoint(RaceParamsHook, 0x800a24b8);

// --------- AI lap-speed bonus override ---------------------------------
// AI_CalcLapSpeedBonus (0x801dd480) walks a per-cup AILapBonusRule table
// indexed by (cupId-1). For unmapped cupIds (0=test_course, 9+) it
// dereferences (&kAILapBonusRuleTable_RaceCourse)[-1] = adjacent rodata
// as a rule-entry pointer and walks 0x14-byte strides until hitting
// sentinel -100. In practice it never finds the sentinel and floods
// PC=0x801dd5c8 with invalid reads (~thousands per frame on test_course).
//
// Hook flow at function entry (0x801dd480):
//   1. If g_cupId is in kCustomTracks AND the entry has lapBonusRules:
//      walk the per-track rules with the same matching logic as vanilla
//      and write the matched bonus to *outValue. No match -> 0.0.
//   2. If miss (cupId not in our list): re-execute the displaced
//      `stwu r1,-0x20(r1)` and bctr to vanilla 0x801dd484 so the vanilla
//      walk still runs for cupId 1..8.
//
// The walker's match conditions mirror vanilla AI_CalcLapSpeedBonus:
//   ccClass / subMode / kartIdx / position fields take -1 as wildcard;
//   `position` is matched against (remainingLaps + 1), so position=1
//   targets the final lap (remaining == 0). lapDiff must lie in
//   [lapDiffMin, lapDiffMax] inclusive. excludePosition (when not -1)
//   skips the rule if (race position == excludePosition - 1).
extern "C" int AILapBonusLookup(int kartIdx, int remainingLaps,
                                 int lapDiff, int excludePos,
                                 double* outValue) {
    EnsureDBATWidened();
    u32 cupId   = *(u32*)0x806cf108;  // g_cupId
    int ccClass = (int)*(u32*)0x806d12cc;
    int subMode = (int)(signed char)*(u32*)0x806d1298;  // (char)g_roundIndex

    for (unsigned int i = 0; i < kCustomTrackCount; ++i) {
        if (kCustomTracks[i].cupId != cupId) continue;
        const AILapBonusRule* rules = kCustomTracks[i].lapBonusRules;
        if (!rules) return 0;  // no override -> defer to vanilla
        for (; rules->ccClass != -100; ++rules) {
            if (rules->ccClass != -1 &&
                (signed char)ccClass != rules->ccClass) continue;
            if (rules->subMode != -1 &&
                (signed char)subMode != rules->subMode) continue;
            if (rules->kartIdx != -1 && kartIdx != -1 &&
                (signed char)kartIdx != rules->kartIdx) continue;
            if (rules->position != -1 &&
                remainingLaps != rules->position - 1) continue;
            if (lapDiff < rules->lapDiffMin) continue;
            if (lapDiff > rules->lapDiffMax) continue;
            if (rules->excludePosition != -1 &&
                excludePos == rules->excludePosition - 1) continue;
            *outValue = (double)rules->bonusValue;
            return 1;
        }
        // No rule matched: vanilla returns FLOAT_806da2a4 = 0.0.
        *outValue = 0.0;
        return 1;
    }
    return 0;  // cupId not in our list -> fall through to vanilla.
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
    // r3..r6 already hold (kartIdx, remainingLaps, lapDiff, excludePos);
    // pass &outValue as the 5th arg in r7.
    addi r7, r1, 0x8
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

// --------- AI base target speed override -------------------------------
// GetBaseSpeedMax (0x801de664) and GetBaseSpeedMin (0x801de5e8) read
// kAIBaseSpeedTable_Race @ 0x803a01e8 (or BattleTimeAttack @ 0x803a07e8)
// indexed by (cupId-1)*0x18 + ccClass*8 + roundIndex (RACE) or
// (cupId-1) + ccClass*8 (Battle/TA). cupId<1 or >8 forces idx=0, which
// returns Mario 50cc round0 (145/160 km/h) and ignores both ccClass and
// roundIndex — that is why test_course AI feels stuck around 50cc speed
// regardless of the player's selected ccClass.
//
// We replace the function entry with a kmBranch to a C function that
// looks up our per-track override (kCustomTracks[i].baseSpeedTable, a
// CupSpeedEntry[ccClass*8 + round]) for RACE mode. On miss (not in our
// list, or no table for that track, or non-RACE mode) we replicate the
// vanilla logic inline so cupId 1..8 still resolve correctly.
extern "C" const CupSpeedEntry* CustomBaseSpeedLookup(int cupId,
                                                      int roundIndex) {
    u32 ccClass  = *(u32*)0x806d12cc;
    u32 gameMode = *(u32*)0x806d1294;
    if (gameMode != 0) return 0;       // only override RACE mode for now
    if (roundIndex < 0 || roundIndex >= 8) return 0;
    if (ccClass >= 3) return 0;
    for (unsigned int i = 0; i < kCustomTrackCount; ++i) {
        if (kCustomTracks[i].cupId != (u32)cupId) continue;
        const CupSpeedEntry* tbl = kCustomTracks[i].baseSpeedTable;
        if (!tbl) return 0;
        return &tbl[ccClass * 8 + roundIndex];
    }
    return 0;
}

static inline int VanillaBaseSpeedIndex(int cupId, int roundIndex,
                                        u32 ccClass, u32 gameMode) {
    if (cupId < 1 || cupId > 8) return 0;
    if (gameMode == 0) {
        return (cupId - 1) * 0x18 + (int)ccClass * 8 + roundIndex;
    }
    return cupId + (int)ccClass * 8 - 1;
}

extern "C" double CustomGetBaseSpeedMax(void* enemyParam, int cupId,
                                         int roundIndex) {
    EnsureDBATWidened();
    const CupSpeedEntry* e = CustomBaseSpeedLookup(cupId, roundIndex);
    if (e) return (double)e->lo;
    u32 ccClass  = *(u32*)0x806d12cc;
    u32 gameMode = *(u32*)0x806d1294;
    int idx = VanillaBaseSpeedIndex(cupId, roundIndex, ccClass, gameMode);
    const float* tbl = (gameMode == 0)
        ? kAIBaseSpeedTable_Race
        : kAIBaseSpeedTable_BattleTimeAttack;
    return (double)tbl[idx * 2];
}

extern "C" double CustomGetBaseSpeedMin(void* enemyParam, int cupId,
                                         int roundIndex) {
    EnsureDBATWidened();
    const CupSpeedEntry* e = CustomBaseSpeedLookup(cupId, roundIndex);
    if (e) return (double)e->hi;
    u32 ccClass  = *(u32*)0x806d12cc;
    u32 gameMode = *(u32*)0x806d1294;
    int idx = VanillaBaseSpeedIndex(cupId, roundIndex, ccClass, gameMode);
    const float* tbl = (gameMode == 0)
        ? kAIBaseSpeedTable_Race
        : kAIBaseSpeedTable_BattleTimeAttack;
    return (double)tbl[idx * 2 + 1];
}

kmBranch(0x801de664, CustomGetBaseSpeedMax);
kmBranch(0x801de5e8, CustomGetBaseSpeedMin);

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
            bgmPair = (const void*)(kBgmIdList_Cup1 + (cupId - 1) * 8);
        } else if (cupId >= 9 && cupId <= 16) {
            bgmPair = (const void*)kBgmIdList_CupsExtra;
        } else {
            bgmPair = (const void*)kBgmIdList_DefaultFallback;
        }
    }
    *(const void**)((u8*)state + 4) = bgmPair;
    // NOTE: do NOT touch DAT_806d175d here. That byte is the voice system's
    // "init-done" sentinel read by FUN_801b6c94 (called later from
    // RaceScene_Init); writing 1 prematurely makes that initializer skip
    // queue reset / per-player state / DAT_806cfd38=-1 / per-cup DAT_806d1754
    // setup, which silently kills FUN_801ad534's race event voices
    // (early returns on DAT_806cfd38 == -1).

    // Vanilla numeric init (FloatA @0x806d8ad4 = 18.0f, FloatB @0x806d8ad8 = 0.0f).
    // Historical naming `one`/`zero` is retained for readability — vanilla code
    // uses FloatA as the rain/wind/thunder scale and FloatB as the fade sentinel.
    float one  = kWeatherInitFloatA;
    float zero = kWeatherInitFloatB;
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

// --------- CourseData_LoadPathTable hook: custom line_bin path ----------
// Vanilla (0x800abae8) reads line-bin filenames from kCup0LineBinTable
// @ 0x8032890c, which has 9 cup slots before the embedded-line-data table
// (DAT_8032899c) takes over at 0x8032899c. Writing kmWritePointer to
// cupId>=9 would corrupt vanilla embedded data, so custom tracks are
// served entirely by this hook instead.
//
// Hook flow:
//   1. If courseId is in kCustomTracks AND the entry has a non-null lineBin,
//      perform the custom load (skip embedded-table lookup entirely — vanilla
//      DAT_8032899c would OOB-read into adjacent rodata for cupId>=9) and
//      return. g_courseData + vtable init mirrors vanilla's prologue.
//   2. Else fall through to vanilla by re-executing the displaced
//      `stwu r1, -0x30(r1)` and bctr'ing to 0x800abaec, so cupId 0..8
//      keep their embedded-or-file behaviour intact.
//
// Kamek note: the "bctr to vanilla+4" pattern is used here (not a simple
// blr) because our hook replaces vanilla's first instruction, so we must
// replay it after deciding not to handle the call.
//
// Vtable constant:
//   0x80419db0 = &PTR_PTR_80419db0 (CourseData method table; first `stw`
//   in vanilla stores it into *courseData, immediately overwriting a
//   prior write of 0x80419dbc that appears dead to us).
extern "C" int CourseDataLoadCustom(
    int* courseData, int courseId, int ccClass, int isUraCourse
) {
    EnsureDBATWidened();

    const struct CustomTrack* track = FindCustomTrack((unsigned int)courseId);
    if (track == 0 || track->lineBin == 0) {
        return 0;  // not a custom cup -> defer to vanilla
    }

    // Replay vanilla prologue init (0x800abb1c..0x800abb30 block).
    *(u32*)courseData        = 0x80419db0u;           // courseData vtable
    *(u32*)0x806d1058u       = (u32)courseData;       // g_courseData = self
    courseData[1]            = 0;                     // data buffer slot clear

    // Load the line_bin file. Mirrors vanilla's "usedEmbedded == false"
    // branch at 0x800abc98..0x800abd64 (file size + alloc + DVD read +
    // per-path offset fixup + waypoint count).
    const char* filename = track->lineBin;
    int rawSize = FUN_8007e344(filename);
    if (rawSize < 0) {
        DebugPrintfSafe("MKGP2: line_bin '%s' DVDOpen failed (cupId=%d)\n",
                        filename, courseId);
        return 1;  // still treat as handled; pathFollower will fail gracefully
    }
    unsigned int padSize = ((unsigned int)rawSize + 0x1fu) & ~0x1fu;
    void* buf = FUN_8003b120(padSize);
    courseData[1] = (int)buf;
    FUN_8007dfe4(filename, buf, padSize, 0, 0);

    // Count path-offset slots (list terminated by 0).
    int* ptr = (int*)buf;
    int count = 0;
    while (*ptr != 0) { ++ptr; ++count; }

    // Allocate per-path waypoint-count array (4 bytes per path).
    int* waypointCounts = (int*)FUN_8003b120((unsigned int)(count << 2));
    courseData[2] = (int)waypointCounts;

    // Fix up path offsets (relative -> absolute) and count waypoints per path.
    int* pathSlot = (int*)buf;
    for (int i = 0; i < count; ++i) {
        *pathSlot = (int)buf + *pathSlot;
        int wpCount = Path_CountWaypointsAndSnapGround((int*)*pathSlot);
        waypointCounts[i] = wpCount;
        ++pathSlot;
    }

    DebugPrintfSafe("MKGP2: custom line_bin '%s' loaded (cupId=%d, paths=%d)\n",
                    filename, courseId, count);
    return 1;  // handled
}

asm void CourseDataLoadPathTableHook() {
    nofralloc
    // Wrapper frame: save args + LR.
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r3, 0x10(r1)          // courseData
    stw  r4, 0x14(r1)          // courseId
    stw  r5, 0x18(r1)          // ccClass
    stw  r6, 0x1c(r1)          // isUraCourse
    bl   CourseDataLoadCustom
    cmpwi r3, 0
    bne  handled
    // Not custom: restore args, tear down wrapper frame, replay vanilla prologue.
    lwz  r3, 0x10(r1)
    lwz  r4, 0x14(r1)
    lwz  r5, 0x18(r1)
    lwz  r6, 0x1c(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    stwu r1, -0x30(r1)         // vanilla's displaced first instruction
    lis  r12, 0x800a
    ori  r12, r12, 0xbaec      // vanilla + 4 (past the displaced insn)
    mtctr r12
    bctr
handled:
    // Custom handled. Return courseData (arg 1) as vanilla does.
    lwz  r3, 0x10(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    blr
}

kmBranch(0x800abae8, CourseDataLoadPathTableHook);

// --------- GetCollisionFilename hook (0x8009c51c) -----------------------
// Vanilla reads `(&PTR_DAT_8040b990)[variantIdx + reverse*0x1c + long*0x45
// + cupId*0x8a]`. The base is slot[cupId=0, long=0, reverse=0, variant=0]
// and the array packs 138 pointers (0x8a) per cupId. Custom cupIds >= 17
// index OOB of the vanilla allocation (8 cups populated), so we fully
// replace the function.
//
// Custom track's CustomTrack has one short + one long collision filename;
// variantIdx and reverseRoundFlag are ignored (custom tracks don't yet
// carry per-variant collision meshes). For non-custom cupIds we replay the
// vanilla lookup, preserving cup 1..8 exactly.
extern "C" const char* GetCollisionFilenameHook() {
    EnsureDBATWidened();
    unsigned int cupId    = *(unsigned int*)0x806cf108u;   // g_cupId
    int longRound         = *(int*)0x806d1268u;            // g_longRoundFlag
    int reverseRound      = *(int*)0x806d1270u;            // g_reverseRoundFlag
    int variantIdx        = *(int*)0x806d126cu;            // g_courseVariantIdx

    const struct CustomTrack* t = FindCustomTrack(cupId);
    if (t != 0) {
        const char* fn = (longRound == 1) ? t->collisionLong : t->collisionShort;
        if (fn != 0) return fn;
        // Fall through to vanilla with cupId aliased to 0 (test_course slot).
        cupId = 0;
    }

    if ((int)cupId < 0) return 0;
    if (longRound < 0) return 0;
    return ((const char**)0x8040b990u)[
        variantIdx + reverseRound * 0x1c + longRound * 0x45 + (int)cupId * 0x8a
    ];
}

kmBranch(0x8009c51c, GetCollisionFilenameHook);

// --------- FUN_8009c238: coin spawn table lookup -----------------------
// Base 0x8040b218, stride **8 pointers (32 bytes)** per cupId indexed by
// `(cupId - 1) * 8 + longRound * 4 + reverseRound * 2`. This does NOT
// match the 0x228 / 0x8a pattern the other 12 getters share, which is
// why the earlier automated scan missed it. CoinSystem_Init calls
// FUN_8009c238() to get a coin/item spawn table pointer and then walks
// it with IsSpawnTableTerminator — with cupId=17 (-1 = 16) the index
// lands 512 bytes past the 8-cup table and returns garbage, producing
// the observed `Invalid read from 0x5000000c @ PC 0x8005f5cc`.
//
// Time-attack mode adds a ccClass-derived bit (uVar1 >> 5) to the index;
// we mirror that. Other callers: CoinSystem_Init, any caller that
// passes NULL as spawnTable to CoinSystem_Init, etc.
extern "C" void* FUN_8009c238_Hook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    int ccClass      = *(int*)0x806d12ccu;
    unsigned int gameMode = *(unsigned int*)0x806d1294u;

    // Custom cupIds: return NULL (no coin/item spawn table). This mirrors
    // the vanilla cupId=0 behaviour (iVar2 = -1 failed the `< 0` guard and
    // returned NULL, leaving test_course without coins). Aliasing to cupId=1
    // would spawn Yoshi cup coins at test_course positions — misleading.
    if (FindCustomTrack((unsigned int)cupId)) return 0;

    int iVar2 = cupId - 1;
    if (iVar2 < 0) return 0;
    if (longRound < 0) return 0;
    if (reverseRound < 0) return 0;
    int idx = iVar2 * 8 + longRound * 4 + reverseRound * 2;
    if (gameMode == 1u /* TIME_ATTACK */) {
        // Replicate vanilla `countLeadingZeros(2 - ccClass) >> 5`:
        // for ccClass == 2, (2-cc) == 0, cntlzw = 32, >> 5 = 1 -> +1
        // for ccClass 0..1, cntlzw < 32, >> 5 = 0 -> +0
        if (ccClass >= 2) idx += 1;
    }
    return ((void**)0x8040b218u)[idx];
}
kmBranch(0x8009c238, FUN_8009c238_Hook);

// --------- Remaining cup-indexed asset getters (10 functions) ----------
// Each hook fully replaces the vanilla function. For custom cupIds, the
// cupId is aliased to 0 (vanilla test_course slot) so the getter returns
// the cupId=0 rodata entry — which for every listed getter is either
// safe default data or the vanilla test_course asset (since cupId=0 is
// where MKGP2 kept the dev-leftover test_course material).
//
// The 6 Group-A getters (stride 0x8a pointer arrays) and 5 Group-B getters
// (stride 0x228 byte-indexed + sub-flags) all share the prologue:
//   cupId remap -> null-guards on cupId/longRound -> vanilla index math.
//
// None of these hooks add CustomTrack fields yet. When a future custom
// track needs distinct model / joint / object data, extend CustomTrack and
// prefer the struct field here before falling back to the cupId=0 alias.
//
// Hook addresses: taken from the 12-function family at 0x8009c1d0..0x8009c688
// (see patch_map / the stride-0x228 Ghidra scan that originally mapped them).

// Group A: stride 0x8a pointer arrays -------------------------------------

// FUN_8009c3c4: stride 0x8a pointer array, base 0x8040b930, no variantIdx.
extern "C" const char* FUN_8009c3c4_Hook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    return ((const char**)0x8040b930u)[reverseRound * 0x1c + longRound * 0x45 + cupId * 0x8a];
}
kmBranch(0x8009c3c4, FUN_8009c3c4_Hook);

// GetCourseModelFilename: stride 0x8a, 2 alternate bases selected by
// DAT_806d127c (0 = road @ 0x8040b940, nonzero = mesh @ 0x8040b950).
extern "C" const char* GetCourseModelFilenameHook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    int variantIdx   = *(int*)0x806d126cu;
    unsigned int meshFlag = *(unsigned int*)0x806d127cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    int idx = variantIdx + reverseRound * 0x1c + longRound * 0x45 + cupId * 0x8a;
    return ((const char**)(meshFlag == 0 ? 0x8040b940u : 0x8040b950u))[idx];
}
kmBranch(0x8009c418, GetCourseModelFilenameHook);

// GetJointNameTable: stride 0x8a, base 0x8040b910, no reverseRoundFlag.
extern "C" void* GetJointNameTableHook() {
    EnsureDBATWidened();
    int cupId      = (int)*(unsigned int*)0x806cf108u;
    int longRound  = *(int*)0x806d1268u;
    int variantIdx = *(int*)0x806d126cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    return ((void**)0x8040b910u)[variantIdx + longRound * 0x45 + cupId * 0x8a];
}
kmBranch(0x8009c57c, GetJointNameTableHook);

// GetCollisionBinFilename: stride 0x8a, base 0x8040b920, no reverseRoundFlag.
extern "C" const char* GetCollisionBinFilenameHook() {
    EnsureDBATWidened();
    int cupId      = (int)*(unsigned int*)0x806cf108u;
    int longRound  = *(int*)0x806d1268u;
    int variantIdx = *(int*)0x806d126cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    if (variantIdx < 0) return 0;
    return ((const char**)0x8040b920u)[variantIdx + longRound * 0x45 + cupId * 0x8a];
}
kmBranch(0x8009c5d0, GetCollisionBinFilenameHook);

// GetStartPosition: (slot, *x, *y, *z) -> int. Base 0x8040b934, stride 0x8a,
// no variantIdx. Each slot is a 12-byte vec3.
extern "C" int GetStartPositionHook(int slot, int* outX, int* outY, int* outZ) {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    int* base = 0;
    if (slot >= 0 && cupId >= 0 && longRound >= 0) {
        base = ((int**)0x8040b934u)
            [reverseRound * 0x1c + longRound * 0x45 + cupId * 0x8a];
    }
    if (base == 0) return 0;
    int* pos = (int*)((unsigned char*)base + slot * 0xc);
    if (outX) *outX = pos[0];
    if (outY) *outY = pos[1];
    if (outZ) *outZ = pos[2];
    return 1;
}
kmBranch(0x8009c688, GetStartPositionHook);

// Group B: stride 0x228 raw-byte layout (with sub-indexing) --------------
// Shared macro-style body inlined per function since each has a distinct
// (base, reverse-present, variant-present) signature.

// FUN_8009c1d0: base 0x8040b93c, reverse yes, variant no.
extern "C" unsigned int FUN_8009c1d0_Hook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    int variantIdx   = *(int*)0x806d126cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    if (variantIdx < 0) return 0;
    return *(unsigned int*)(cupId * 0x228 + longRound * 0x114
                            + reverseRound * 0x70 + 0x8040b93cu);
}
kmBranch(0x8009c1d0, FUN_8009c1d0_Hook);

// GetCourseBgmEntry: base 0x8040b970, reverse yes, variant yes (*4 stride).
extern "C" unsigned int GetCourseBgmEntryHook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    int variantIdx   = *(int*)0x806d126cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    if (variantIdx < 0) return 0;
    return *(unsigned int*)(cupId * 0x228 + longRound * 0x114
                            + reverseRound * 0x70 + variantIdx * 4 + 0x8040b970u);
}
kmBranch(0x8009c2f0, GetCourseBgmEntryHook);

// FUN_8009c360: base 0x8040ba10, reverse no, variant yes.
extern "C" unsigned int FUN_8009c360_Hook() {
    EnsureDBATWidened();
    int cupId      = (int)*(unsigned int*)0x806cf108u;
    int longRound  = *(int*)0x806d1268u;
    int variantIdx = *(int*)0x806d126cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    if (variantIdx < 0) return 0;
    return *(unsigned int*)(cupId * 0x228 + longRound * 0x114
                            + variantIdx * 4 + 0x8040ba10u);
}
kmBranch(0x8009c360, FUN_8009c360_Hook);

// GetCourseObjectTable: base 0x8040b960, reverse yes, variant yes.
extern "C" unsigned int GetCourseObjectTableHook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    int variantIdx   = *(int*)0x806d126cu;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return 0;
    if (longRound < 0) return 0;
    return *(unsigned int*)(cupId * 0x228 + longRound * 0x114
                            + reverseRound * 0x70 + variantIdx * 4 + 0x8040b960u);
}
kmBranch(0x8009c4bc, GetCourseObjectTableHook);

// GetCourseStartYaw: base 0x8040b938, reverse yes, variant no. Float return.
// Fallback is FLOAT_806d4790 (the "missing context" default yaw).
extern "C" float GetCourseStartYawHook() {
    EnsureDBATWidened();
    int cupId        = (int)*(unsigned int*)0x806cf108u;
    int longRound    = *(int*)0x806d1268u;
    int reverseRound = *(int*)0x806d1270u;
    if (FindCustomTrack((unsigned int)cupId)) cupId = 0;
    if (cupId < 0) return *(float*)0x806d4790u;
    if (longRound < 0) return *(float*)0x806d4790u;
    return *(float*)(cupId * 0x228 + longRound * 0x114
                     + reverseRound * 0x70 + 0x8040b938u);
}
kmBranch(0x8009c634, GetCourseStartYawHook);

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

// --------- Root-cause fix: OOB voice_id push in FUN_801b0af4 -----------
// FUN_801b0af4 is the race-start voice intro pusher. After emitting three
// fixed "welcome" voices (0x280, 0x189, 0x280), it computes a round-intro
// voice id:
//
//     sVar6 = g_longRoundFlag + (g_cupId - 1) * 2
//
// Vanilla predicate: `if (sVar6 != -1) push(sVar6, meta=0xFFFF)`. The
// formula maps cupId 1..8 × longRoundFlag 0..1 onto voice ids 0..15 (the
// round-intro bank). Breakage modes we fix:
//
//   - cupId=0 (test_course) -> sVar6 = -2, NOT -1, slips through the
//     `!= -1` filter. Downstream dequeue reads live_A01_dsp[-2 * 12] =
//     garbage (original PC=0x80294F00 ISR).
//   - cupId >= 17 (custom tracks) -> sVar6 = 32..33+, past the 16-entry
//     voice bank. Dequeue plays garbage (different ISR address).
//
// Tighten the predicate to `0 <= sVar6 <= 15`:
//   0x801b0c08: cmplwi r3, 15   (0x2803000F) — unsigned, negative becomes huge
//   0x801b0c0c: bgt   +0xA8     (0x418100A8) — skip when > 15
//
// Unsigned compare catches both ends: negative sVar6 wraps to a large
// unsigned value (still > 15 -> skipped), and cupId>8 values fall off
// the top (> 15 -> skipped). The VoiceDequeueGuard above still runs as
// a defensive safety net.
kmWrite32(0x801B0C08, 0x2803000F);   // cmplwi r3, 15
kmWrite32(0x801B0C0C, 0x418100A8);   // bgt cr0, +0xA8
