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
    DebugPrintfSafe("MKGP2: round-select swap cupId %d -> %d (scope=%d)\n",
                    cup, alias, cup);
    DumpSlotRegistryFor("PreInit (after swap)");
}

extern "C" void RoundSelect_PreDtor() {
    EnsureDBATWidened();
    if (g_customCupScope > 0) {
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
// per-round unlock state onto test_cup. PoC policy for any custom cup:
// "nothing cleared yet" — round 1 is current/playable, rest locked.
// Real per-cup persistent progress is future work (would need a patch-side
// shadow byte table).
//
// Note: the externals-name "RoundIsUnlocked" is misleading — the function
// actually answers "is round N *cleared*?" (returns 1 iff round_index+1 ≤
// progress byte). Returning 0 for all rounds means nothing cleared, which
// vanilla then turns into "round 0 = current, round 1..3 = locked" UI state.
extern "C" char RoundIsUnlocked_Wrapper(void* playerData, int round) {
    EnsureDBATWidened();
    if (g_customCupScope > 0) {
        return 0;  // nothing cleared → round 0 becomes current/playable
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
