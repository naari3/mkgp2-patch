#include <kamek.h>
#include "patch_common.h"

// Brake-back navigation: press the brake pedal in a pre-race menu to return
// to the previous screen. Single hook inside MenuScene_Dispatch (0x801b8ec0)
// covers every clFlow* uniformly and does not collide with cup_page3's
// per-flow hooks.
//
// Design:
//   1. Track the current MenuScene_Dispatch switch id via s_curDispatchId
//      (updated on every forward transition). g_currentSceneState is the
//      scene's SetSyncTarget value, which does NOT always equal the
//      dispatch id — clFlowItem (case 0x21) calls SetSyncTarget(0x22),
//      so pushing g_currentSceneState would corrupt the back-stack.
//   2. On forward transition (rc != -1), push s_curDispatchId onto the
//      LIFO stack if backable, then set s_curDispatchId = rc.
//   3. On brake-edge while Update returned -1, pop the stack and inject
//      the popped value as the override rc (routes the dispatcher to
//      re-init the previous scene).
//   4. Hook at 0x801b8f50: right after `or r30,r3,r3` captures the Update
//      return value, replacing the immediately-following `cmpwi r30,-0x1`.
//      We rewrite r30 in place so the vanilla `beq` and switch still see
//      the (possibly overridden) value.
//   5. Stack resets when entering scene 0x02 (attract) or 0x2A (race
//      start) so brake-back never crosses session boundaries.
//
// Separate second hook on ItemSelectState_Dtor (see bottom of file) guards
// DAT_805d270c/10/14 from being overwritten with -1 when the user brakes
// out of clFlowItem without confirming a selection.
//
// Brake edge: game mask bit 0x200 is analog brake (pedal > 0.7) — rising
// edge only, and suppressed when bit 0x400 is held so the CC-select
// mode-toggle combo (0x400-held + confirm) cannot accidentally fire a
// back. See mkgp2_input_system.md for the raw→game bit table.

typedef unsigned char  u8;
typedef unsigned int   u32;

extern "C" {
    typedef struct InputObject InputObject;
    InputObject** GetInputManager();
    extern u32 g_currentSceneState;  // 0x806cf114 (SetSyncTarget write; NOT dispatch id)
}

// ItemSelectState_Dtor unconditionally persists self->selectedItemId0/1/2
// to DAT_805d270c/10/14 (and their PlayerData-resolved bytes to
// DAT_806d1264..66) at 0x80239504..0x80239554. Those globals are saved to
// memory card (card_save_trigger) and consumed by clFlowItem_Update's
// subState-1 unlock check
// (PlayerData_ResolveUnlockedIdByIndex(&g_playerData, DAT_805d270c)), so
// on a brake-back where the selection is unconfirmed (all three fields
// are -1), the Dtor taints the player's saved state and forces all
// subsequent Item-scene entries down the "skip modal" branch (subState 3).
//
// Guard by patching the Dtor itself at 0x802394f8: if selectedItemId0 is
// -1, branch past the 6-write persistence block to the start of the
// iconArray cleanup at 0x80239558. `mr r29, r3` at 0x80239508 is also
// skipped, which is safe — r29 is callee-saved (restored from stack at
// the epilogue) and is not referenced after the skipped range.

static u32 s_stack[16];
static int s_top = 0;
static u32 s_prevHeld = 0;
// MenuScene_Dispatch switch id of the currently-running scene. Updated on
// every forward transition. Initialized to 0 (sentinel = no known scene yet).
static u32 s_curDispatchId = 0;

static void ResetStack() {
    s_top = 0;
}

static int IsBackableScene(u32 dispatchId) {
    switch (dispatchId) {
    case 0x0B:  // clFlowClass
    case 0x0D:  // FrameSelection
    case 0x0F:  // clFlowKart
    case 0x15:  // clFlowCup
    case 0x1B:  // clFlowRound
    case 0x17:  // clFlowCourse
    case 0x21:  // clFlowItem (modal/grid) — was 0x22 (wrong; that's the
                //   legacy roulette scene. clFlowItem.Init sets sync target
                //   to 0x22, which is why the old code saw 0x22 via
                //   g_currentSceneState, but the dispatch id is 0x21.)
    case 0x25:  // clFlowGpPlease
    case 0x26:  // clFlowTaPlease
        return 1;
    default:
        return 0;
    }
}

static int BrakeEdgeDetected(u32 cur) {
    InputObject** mgr = GetInputManager();
    if (!mgr || !*mgr) return 0;
    u32 held = *(u32*)((u8*)*mgr + 0x0c);
    u32 edge = (held ^ s_prevHeld) & held & 0x200;
    u32 mod  = held & 0x400;
    u32 prev = s_prevHeld;
    s_prevHeld = held;
    if (edge != 0) {
        DebugPrintfSafe("MKGP2: brake-edge cur=%x held=%x prev=%x mod=%x\n",
                        cur, held, prev, mod);
    }
    return (edge != 0 && mod == 0) ? 1 : 0;
}

static void LogStackTop3(const char* tag) {
    u32 a = s_top >= 1 ? s_stack[s_top - 1] : 0;
    u32 b = s_top >= 2 ? s_stack[s_top - 2] : 0;
    u32 c = s_top >= 3 ? s_stack[s_top - 3] : 0;
    DebugPrintfSafe("MKGP2: %s top=%d [..%x,%x,%x]\n", tag, s_top, c, b, a);
}

extern "C" int MaybeOverrideWithBack(int rc) {
    EnsureDBATWidened();

    if (rc == -1) {
        u32 cur = s_curDispatchId;
        int backable = IsBackableScene(cur);
        int edge = BrakeEdgeDetected(cur);
        if (backable && edge && s_top > 0) {
            u32 backTo = s_stack[--s_top];
            DebugPrintfSafe("MKGP2: brake-back %x -> %x (sync=%x)\n",
                            cur, backTo, g_currentSceneState);
            LogStackTop3("post-pop");
            s_curDispatchId = backTo;
            return (int)backTo;
        }
        if (edge) {
            DebugPrintfSafe("MKGP2: brake-edge ignored cur=%x backable=%d top=%d\n",
                            cur, backable, s_top);
        }
        return -1;
    }

    // Forward transition. rc is the new dispatch id that
    // MenuScene_Dispatch's switch will consume. Push the outgoing
    // dispatch id before advancing.
    u32 from = s_curDispatchId;
    if (IsBackableScene(from)) {
        if (s_top < 16) {
            s_stack[s_top++] = from;
            DebugPrintfSafe("MKGP2: fwd %x->%x push (sync=%x)\n",
                            from, rc, g_currentSceneState);
            LogStackTop3("post-push");
        } else {
            DebugPrintfSafe("MKGP2: fwd %x->%x stack-full\n", from, rc);
        }
    } else {
        DebugPrintfSafe("MKGP2: fwd %x->%x skip (from non-backable)\n",
                        from, rc);
    }
    s_curDispatchId = (u32)rc;

    if (rc == 0x2A || rc == 0x02) {
        DebugPrintfSafe("MKGP2: reset on rc=%x\n", rc);
        ResetStack();
    }

    return rc;
}

// Hook at 0x801b8f50 (MenuScene_Dispatch). Replaces `cmpwi r30,-0x1`;
// re-executes it at the tail so the following `beq` still sees CR0.
asm void MenuSceneDispatchHook() {
    nofralloc
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r31, 0x1c(r1)
    mr   r3, r30
    bl   MaybeOverrideWithBack
    mr   r30, r3
    lwz  r31, 0x1c(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    cmpwi r30, -0x1
    blr
}

kmBranch(0x801b8f50, MenuSceneDispatchHook);
kmPatchExitPoint(MenuSceneDispatchHook, 0x801b8f54);

// ItemSelectState_Dtor persistence guard. Replaces `lis r3, 0x805D` at
// 0x802394f8. The Dtor's prologue has already set r26 = self and branched
// past the null-self guard, so r26 is usable here.
//
// If self->selectedItemId0 == -1, jump past the entire 6-write persistence
// block to 0x80239558 (lwz r25, 0(r26) — start of iconArray cleanup).
// Otherwise restore the clobbered `lis r3, 0x805D` and fall through via
// kmPatchExitPoint to 0x802394fc.
asm void ItemSelectStateDtorHook() {
    nofralloc
    lwz   r3, 0x164(r26)
    cmpwi r3, -0x1
    bne   normal_path
    lis   r3, 0x8023
    ori   r3, r3, 0x9558
    mtctr r3
    bctr
normal_path:
    lis   r3, 0x805D
    blr
}

kmBranch(0x802394f8, ItemSelectStateDtorHook);
kmPatchExitPoint(ItemSelectStateDtorHook, 0x802394fc);
