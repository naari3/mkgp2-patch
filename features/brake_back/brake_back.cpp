#include <kamek.h>
#include "patch_common.h"

// Brake-back navigation: press the brake pedal in a pre-race menu to return
// to the previous screen. Implemented as a single hook inside
// MenuScene_Dispatch (0x801b8ec0), so it covers every clFlow* uniformly and
// does not collide with cup_page3's per-flow hooks.
//
// Design (see conversation notes):
//   1. LIFO stack of backable scene states. On every forward transition
//      (Update returning a non-(-1) target state from a backable scene),
//      push the current state. On brake-edge while Update returned -1,
//      pop and inject the popped value as the Update result.
//   2. Hook point: right after `or r30,r3,r3` captures the Update return
//      value, replacing the immediately-following `cmpwi r30,-0x1`. We
//      rewrite r30 in place so the vanilla `beq` and switch still see
//      the (possibly overridden) value.
//   3. Stack resets when entering scene 0x02 (attract) or 0x2A (race
//      start) so brake-back never crosses session boundaries.
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
    extern u32 g_currentSceneState;  // 0x806cf114
}

static u32 s_stack[16];
static int s_top = 0;
static u32 s_prevHeld = 0;

static void ResetStack() {
    s_top = 0;
}

static int IsBackableScene(u32 scene) {
    switch (scene) {
    case 0x0B:  // clFlowClass
    case 0x0D:  // clFlowChara
    case 0x0F:  // clFlowKart
    case 0x15:  // clFlowCup
    case 0x1B:  // clFlowRound
    case 0x17:  // clFlowCourse
    case 0x22:  // clFlowItemSelect
    case 0x25:  // clFlowGpPlease
    case 0x26:  // clFlowTaPlease
        return 1;
    default:
        return 0;
    }
}

static u32 s_lastHeldLogged = 0xFFFFFFFF;

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
    u32 cur = g_currentSceneState;

    if (rc == -1) {
        int backable = IsBackableScene(cur);
        int edge = BrakeEdgeDetected(cur);
        if (backable && edge && s_top > 0) {
            u32 backTo = s_stack[--s_top];
            DebugPrintfSafe("MKGP2: brake-back %x -> %x\n", cur, backTo);
            LogStackTop3("post-pop");
            return (int)backTo;
        }
        if (edge) {
            DebugPrintfSafe("MKGP2: brake-edge ignored cur=%x backable=%d top=%d\n",
                            cur, backable, s_top);
        }
        return -1;
    }

    // Forward transition: push 'from' so brake can return.
    // Back transitions don't need special handling — we returned the
    // back-target directly from the `rc == -1` branch, never reaching here
    // for the same hook invocation.
    if (IsBackableScene(cur)) {
        if (s_top < 16) {
            s_stack[s_top++] = cur;
            DebugPrintfSafe("MKGP2: fwd %x->%x push\n", cur, rc);
            LogStackTop3("post-push");
        } else {
            DebugPrintfSafe("MKGP2: fwd %x->%x stack-full\n", cur, rc);
        }
    } else {
        DebugPrintfSafe("MKGP2: fwd %x->%x skip (cur non-backable)\n", cur, rc);
    }

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
