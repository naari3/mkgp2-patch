#include <kamek.h>
#include "patch_common.h"

// Race restart: press a hotkey mid-race to go back to the 3-2-1-GO countdown.
//
// Strategy (see mkgp2_race_restart.md section "full re-init path"):
//   RaceScene_Dtor(scene, 0) tears down every sub-system while keeping the
//   0x40-byte scene struct alive, then RaceScene_Init(scene) re-initializes
//   in place. This is the exact boot path, so the lakitu countdown replays
//   and every global flag / timer / path-entry is reset cleanly.
//
// Hook: RaceMode_FrameUpdate (0x800a0ef4) entry via kmBranch.
//   Running the restart before this frame's update begins avoids dangling
//   references to about-to-be-freed sub-objects on the caller's stack.
//
// Hotkey: internal-button-mask bit 0x2000, rising edge.
//   Input_IsItemButtonPressed skips this bit in race scene state 0x2a, so it
//   does not collide with item / versus buttons. (See mkgp2_input_system.md.)

typedef unsigned char  u8;
typedef unsigned int   u32;

extern "C" {
    typedef struct InputObject InputObject;
    InputObject** GetInputManager();
    void RaceScene_Dtor(void* scene, short freeSelf);
    void RaceScene_Init(void* scene);
}

static u32 s_prevHeld = 0;

extern "C" void TryRaceRestart(void* scene) {
    EnsureDBATWidened();
    if (!scene) return;

    InputObject** mgr = GetInputManager();
    if (!mgr || !*mgr) return;

    u32 held = *(u32*)((u8*)*mgr + 0x0c);
    u32 pressed = held & ~s_prevHeld;
    s_prevHeld = held;

    if (pressed & 0x2000) {
        DebugPrintfSafe("MKGP2: race restart (scene=%p)\n", scene);
        RaceScene_Dtor(scene, 0);
        RaceScene_Init(scene);
    }
}

// Hook at 0x800a0ef4. Original first instruction: stwu r1, -0x90(r1).
// Save regs, call C handler (which may restart in-place), restore regs,
// execute the replaced prologue, then exit to 0x800a0ef8 via kmPatchExitPoint.
asm void RaceRestartHook() {
    nofralloc
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r31, 0x1c(r1)
    mr   r31, r3
    bl   TryRaceRestart
    mr   r3, r31
    lwz  r31, 0x1c(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    stwu r1, -0x90(r1)
    blr
}

kmBranch(0x800a0ef4, RaceRestartHook);
kmPatchExitPoint(RaceRestartHook, 0x800a0ef8);
