// Round-select scene support for custom cups.
//
// Vanilla clFlowRound_Init reads `iVar12 = DAT_8049af8c[g_cupId]` to translate
// g_cupId into a sub_index (display order). DAT_8049af8c values:
//   [g_cupId 0..8] = [-1, 1, 3, 4, 5, 6, 7, 0, 2]
// Then sub_index is used to fan out into cup-indexed resource tables
// (DAT_8049afa0, aea0/aea2, af78, 8039b308). With g_cupId >= 17 (custom cup)
// the af8c lookup OOB-reads adjacent memory, returning garbage sub_index
// (huge) which then OOB-reads the secondary tables → garbage sprite IDs.
//
// Strategy: while clFlowRound is active, swap g_cupId to the custom cup's
// `display_alias_cup` (e.g. test_cup -> g_cupId=7 = Yoshi via sub_index=0).
// Crucially this must be the *g_cupId* whose DAT_8049af8c entry is a valid
// sub_index, NOT the sub_index itself (g_cupId=0 maps to sub_index=-1 = test
// slot, which causes secondary OOB). Bindings still fire because
// custom_assets' ApplyBinding consults `g_customCupScope` (= our real custom
// cupId) instead of g_cupId when scope is set.
//
// Three hooks:
//   1. clFlowRound_Init entry  (0x801caf34) — set scope + swap g_cupId
//   2. clFlowRound_Dtor entry  (0x801cadbc) — restore g_cupId, clear scope
//   3. SetCourseParams call    (0x801ca590) — substitute the cupId arg so
//        race scene gets the *real* custom cupId, then re-swap g_cupId for
//        the rest of clFlowRound_Update's outro.
//
// Driven by features/cups.yaml `display_alias_cup` field via
// generated kCupAliasMap[]. Adding a new custom cup = one line in yaml.

#include <kamek.h>
#include "patch_common.h"
#include "../custom_assets/custom_assets.h"

extern "C" {
    extern volatile unsigned int g_cupId;                       // 0x806cf108
    int  SetCourseParams(int cupId, int longRound, int variantIdx, int reverseRound);  // 0x8009cbfc

    // Save-data lookups used by clFlowRound_Init for the "is round N
    // unlocked / cup completed" decoration. Both index g_playerData by
    // cup_subindex (= g_cupId-1 or g_cupId-9). Under our alias swap they'd
    // read whichever vanilla cup we aliased to (Yoshi=7), leaking that cup's
    // progress onto our custom cup. Wrapped below to short-circuit while
    // g_customCupScope is active.
    char RoundIsUnlocked(void* playerData, int round);                       // 0x801d5b04
    char RoundCupIsCompleted(void* playerData, int ccClass, int cup, int longRound);  // 0x801d5c0c

    int PreloadResource(int resourceId);   // 0x80120d80
}

// --- Round-thumb slot injection -------------------------------------------
//
// Vanilla per-cup 16-byte slot in DAT_8049aea0 holds 8 u16 thumb resource ids
// (FUN_801c9288 reads `&DAT_8049aea0 + sub_index*16 + roundIdx*4` for square
// and `+2` for road). Yoshi only has 2 distinct values duplicated across all
// 4 rounds. To get per-round thumbs for our custom cup, we save the alias's
// 16-byte slot on PreInit, write our 8 custom-id u16s, and restore on PreDtor.
//
// The 8 injected ids (0x4006..0x4009 + duplicates) flow through vanilla
// PreloadResource → custom_assets getter hooks → kCustomPathTable → our TPLs.
// No custom_assets binding entry is needed for round thumbs (the inject
// supplies the custom id directly into the table).

// 0x8049af8c[g_cupId] -> sub_index. Same table read as vanilla clFlowRound_Init.
static const short* const kCupSubIndexTable = (const short*)0x8049af8c;
// Cup-slot table base (16 bytes per cup, 4 rounds × 4 bytes).
static u16* const kCupSlotTableBase = (u16*)0x8049aea0;

static u16  s_savedSlot[8];          // 16 bytes of vanilla slot
static int  s_savedSlotIdx = -1;     // sub_index of saved slot, -1 = nothing saved

// Look up our 8 inject ids for a custom cup_id. Returns NULL if no entry.
static const RoundThumbInject* FindThumbInject(int customCupId) {
    for (unsigned int i = 0; i < kRoundThumbInjectCount; ++i) {
        if ((int)kRoundThumbInjects[i].customCupId == customCupId)
            return &kRoundThumbInjects[i];
    }
    return 0;
}

// Manually preload our inject ids so the per-frame UV refresh path finds
// registered slots. vanilla clFlowRound_Init does call PreloadResource on
// the table values it reads (= our injects), but only for round 0/1 offsets
// — and empirically the slot registry shows the custom ids never make it in
// without an explicit pre-touch (probably because PreloadResource's first
// step is a vanilla resource-table lookup that misses for custom ids and
// short-circuits before reaching our getters).
static void PreloadInjectIds(const RoundThumbInject* inj) {
    for (int i = 0; i < 8; ++i) {
        unsigned int id = (unsigned int)inj->thumbIds[i];
        if (id < 0x4000) continue;
        // Dedup: same id appears 4 times for 2-round case (round 0/2 dup,
        // round 1/3 dup). Skip if any earlier slot had the same id.
        bool dup = false;
        for (int j = 0; j < i; ++j) {
            if (inj->thumbIds[j] == inj->thumbIds[i]) { dup = true; break; }
        }
        if (dup) continue;
        int ret = PreloadResource((int)id);
        DebugPrintfSafe("MKGP2:   PreloadResource(0x%04x) = %d\n", (int)id, ret);
    }
}

static void InjectRoundThumbs(int customCupId, int aliasCupId) {
    if (s_savedSlotIdx >= 0) {
        // Already injected (re-entry?). Don't overwrite saved.
        DebugPrintfSafe("MKGP2: round-thumb inject skipped (already active idx=%d)\n",
                        s_savedSlotIdx);
        return;
    }
    const RoundThumbInject* inj = FindThumbInject(customCupId);
    if (!inj) return;
    if (aliasCupId < 0 || aliasCupId > 8) return;
    int subIdx = (int)kCupSubIndexTable[aliasCupId];
    if (subIdx < 0 || subIdx >= 8) return;     // sentinel / OOB safety

    u16* slot = kCupSlotTableBase + subIdx * 8;
    // Save vanilla 8 u16 then overwrite with our injects.
    for (int i = 0; i < 8; ++i) s_savedSlot[i] = slot[i];
    for (int i = 0; i < 8; ++i) slot[i] = inj->thumbIds[i];
    s_savedSlotIdx = subIdx;
    // Split the dump into two short DebugPrintfSafe calls — DebugPrintfSafe's
    // r3-shift only covers r3..r10, so packing 11+ varargs causes the tail
    // values to be misaligned (read from stack[0..] which holds the next args).
    DebugPrintfSafe("MKGP2: round-thumb inject cup=%d alias=%d sub=%d nRounds=%d\n",
                    customCupId, aliasCupId, subIdx, (int)inj->nRounds);
    DebugPrintfSafe("MKGP2:   saved=[%04x %04x %04x %04x]\n",
                    (unsigned)s_savedSlot[0], (unsigned)s_savedSlot[1],
                    (unsigned)s_savedSlot[2], (unsigned)s_savedSlot[3]);
    DebugPrintfSafe("MKGP2:   new  =[%04x %04x %04x %04x]\n",
                    (unsigned)slot[0], (unsigned)slot[1],
                    (unsigned)slot[2], (unsigned)slot[3]);

    PreloadInjectIds(inj);
}

static void RestoreRoundThumbs() {
    if (s_savedSlotIdx < 0) return;
    u16* slot = kCupSlotTableBase + s_savedSlotIdx * 8;
    for (int i = 0; i < 8; ++i) slot[i] = s_savedSlot[i];
    DebugPrintfSafe("MKGP2: round-thumb restore sub=%d -> [%04x %04x %04x %04x]\n",
                    s_savedSlotIdx,
                    (unsigned)slot[0], (unsigned)slot[1],
                    (unsigned)slot[2], (unsigned)slot[3]);
    s_savedSlotIdx = -1;
}

// --- C dispatchers called from asm wrappers ---------------------------------

// Round-select resource IDs we expect to be intercepted by our bindings.
// Used for diagnostic slot-registry dumps to verify the preload state.
static const unsigned short kRoundSelectIds[] = {
    0x16ED,  // cup name
    0x19E0,  // course1 thumb road
    0x19E1,  // course2 thumb road
    0x1A66,  // course1 thumb
    0x1A67,  // course2 thumb
};

static void DumpSlotRegistryFor(const char* tag) {
    int* slots = (int*)0x806573e8;
    DebugPrintfSafe("MKGP2: round-select slot dump @ %s\n", tag);
    for (int s = 0; s < 600; ++s) {
        int* slot = slots + s * 7;
        int resId = slot[0];
        int gk = slot[1];
        if (gk == -1) continue;
        // Show only slots whose resId matches our target list.
        for (unsigned i = 0; i < sizeof(kRoundSelectIds)/sizeof(kRoundSelectIds[0]); ++i) {
            if ((unsigned)resId == kRoundSelectIds[i]) {
                DebugPrintfSafe("MKGP2:   slot[%d] resId=0x%04x gk=0x%08x dataPtr=%p\n",
                                s, resId, gk, (void*)slot[2]);
                break;
            }
        }
    }
}

extern "C" void RoundSelect_PreInit() {
    EnsureDBATWidened();
    int cup = (int)g_cupId;
    int alias = CustomCup_LookupAlias(cup);
    if (alias < 0) {
        g_customCupScope = 0;
        return;
    }
    g_customCupScope = cup;
    g_cupId = (unsigned int)alias;
    // Direct-insert: write our 8 custom-ID u16s into the alias cup's 16-byte
    // slot. vanilla code reads them as resource ids, calls PreloadResource,
    // and the custom_assets ResourceSlotLoadBranchHook reroutes the loader to
    // the filename path so our TPLs end up registered in the slot registry.
    InjectRoundThumbs(cup, alias);
    DebugPrintfSafe("MKGP2: round-select swap cupId %d -> %d (scope=%d)\n",
                    cup, alias, cup);
    DumpSlotRegistryFor("PreInit (after swap)");
}

extern "C" void RoundSelect_PreDtor() {
    EnsureDBATWidened();
    if (g_customCupScope > 0) {
        RestoreRoundThumbs();   // no-op when nothing was injected
        DebugPrintfSafe("MKGP2: round-select restore cupId %d -> %d\n",
                        (int)g_cupId, g_customCupScope);
        g_cupId = (unsigned int)g_customCupScope;
        g_customCupScope = 0;
    }
}

// SetCourseParams kmCall wrapper. Vanilla clFlowRound_Update calls this on
// confirm to commit (cupId, course_id, variant, reverse) into globals before
// scene transition. We pass the *real* custom cupId so the race scene sees
// it, but immediately re-apply the alias swap because clFlowRound_Update's
// outro keeps reading g_cupId for table lookups for ~45 more frames.
extern "C" int SetCourseParams_RoundWrapper(int cupId, int longRound,
                                            int variantIdx, int reverseRound) {
    EnsureDBATWidened();
    int actualCup = cupId;
    if (g_customCupScope > 0) {
        actualCup = g_customCupScope;
    }
    int rv = SetCourseParams(actualCup, longRound, variantIdx, reverseRound);
    if (g_customCupScope > 0) {
        // SetCourseParams writes g_cupId = actualCup. Re-swap to keep
        // outro's table reads in-bounds. Dtor will restore for next scene.
        int alias = CustomCup_LookupAlias(g_customCupScope);
        if (alias >= 0) {
            g_cupId = (unsigned int)alias;
        }
    }
    return rv;
}

// Round-unlock save data wrappers.
//
// Without these, our alias swap (g_cupId 17 -> 7 for Yoshi atlas) makes
// vanilla read Yoshi's progress byte for our custom cup, leaking Yoshi's
// per-round unlock state onto test_cup. Real per-cup persistent progress is
// future work (would need a patch-side shadow byte table).
//
// Note: the externals-name "RoundIsUnlocked" is misleading — the function
// actually answers "is round N *cleared*?" (returns 1 iff round_index+1 ≤
// progress byte). vanilla clFlowRound_Init walks rounds 0..3 and assigns:
//   cleared (return 1)              → state 0, counts toward "available"
//   first uncleared (return 0)       → state 1 (current/highlighted)
//   subsequent uncleared (return 0)  → state 2 (locked, cursor skips)
//
// Policy: every yaml-defined round should be selectable. Mark round
// 0..(nRounds-2) as "cleared" so they're playable, mark round (nRounds-1)
// as current, and round nRounds.. as locked. This makes all defined rounds
// reachable while still letting vanilla's bVar1 transition handle the
// "current" highlight on the last defined round.
extern "C" char RoundIsUnlocked_Wrapper(void* playerData, int round) {
    EnsureDBATWidened();
    if (g_customCupScope > 0) {
        const RoundThumbInject* inj = FindThumbInject(g_customCupScope);
        int nRounds = (inj != 0) ? (int)inj->nRounds : 1;
        if (nRounds < 1) nRounds = 1;
        if (round >= 0 && round < nRounds - 1) return 1;   // cleared → playable
        return 0;                                          // current or locked
    }
    return RoundIsUnlocked(playerData, round);
}

extern "C" char RoundCupIsCompleted_Wrapper(void* playerData, int ccClass,
                                            int cup, int longRound) {
    EnsureDBATWidened();
    if (g_customCupScope > 0) {
        return 0;
    }
    return RoundCupIsCompleted(playerData, ccClass, cup, longRound);
}

// --- ASM wrappers -----------------------------------------------------------

// Hook at clFlowRound_Init (0x801caf34). Vanilla prologue (5 insts) replayed,
// bctr to 0x801caf48 (the `or r31, r3, r3` that captures param_1).
asm void clFlowRound_Init_Hook() {
    nofralloc
    // Our wrapper frame
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r3, 0x10(r1)        // save param_1 (scene ptr)

    bl   RoundSelect_PreInit

    lwz  r3, 0x10(r1)        // restore param_1
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20

    // Replay vanilla prologue 0x801caf34..0x801caf44 (5 insts)
    stwu r1, -0x30(r1)
    mflr r0
    lis  r4, -0x7fb6
    stw  r0, 0x34(r1)
    stmw r25, 0x14(r1)

    // bctr to 0x801caf48 (next vanilla instruction)
    lis  r12, 0x801c
    ori  r12, r12, 0xaf48
    mtctr r12
    bctr
}

// Hook at clFlowRound_Dtor (0x801cadbc). Prologue is 4 insts, bctr target
// is 0x801cadcc (the `or. r31, r3, r3` that captures param_1).
asm void clFlowRound_Dtor_Hook() {
    nofralloc
    // Wrapper frame (save r3 = param_1, r4 = second arg)
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r3, 0x10(r1)
    stw  r4, 0x14(r1)

    bl   RoundSelect_PreDtor

    lwz  r3, 0x10(r1)
    lwz  r4, 0x14(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20

    // Replay vanilla prologue 0x801cadbc..0x801cadc8 (4 insts)
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r31, 0x1c(r1)

    // bctr to 0x801cadcc
    lis  r12, 0x801c
    ori  r12, r12, 0xadcc
    mtctr r12
    bctr
}

// --- Patch installation -----------------------------------------------------

kmBranch(0x801caf34, clFlowRound_Init_Hook);
kmBranch(0x801cadbc, clFlowRound_Dtor_Hook);
kmCall  (0x801ca590, SetCourseParams_RoundWrapper);

// Round-unlock leak guards. Four call sites for RoundIsUnlocked and one
// for RoundCupIsCompleted, all inside clFlowRound_Init's per-round loop.
kmCall  (0x801cb114, RoundIsUnlocked_Wrapper);
kmCall  (0x801cb228, RoundIsUnlocked_Wrapper);
kmCall  (0x801cb278, RoundIsUnlocked_Wrapper);
kmCall  (0x801cb330, RoundIsUnlocked_Wrapper);
kmCall  (0x801cb2ac, RoundCupIsCompleted_Wrapper);
