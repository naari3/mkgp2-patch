#include <kamek.h>
#include "patch_common.h"

// Retail main.dol gates DebugPrintf behind g_DebugPrintfEnable (0x80598a8a),
// which GameBoot_Init zeroes at boot. Forcing it to 1 unmasks 100+ in-game
// OSReport-style calls so they reach Dolphin's HLE logger. Pure data poke,
// no code flow change.
kmWrite8(0x80598a8a, 1);

typedef unsigned char u8;
typedef unsigned int  u32;

// OSContext layout confirmed via Ghidra: srr0 @ +0x198, srr1 @ +0x19c.
static const int OFF_SRR0 = 0x198;
static const int OFF_SRR1 = 0x19c;

// --------- OSUnhandledException entry hook (0x80257f00) ------------------
// Signature: (u8 exception_type, OSContext* ctx, u32 dsisr, u32 dar).
// Fires for every exception that has no registered handler (the terminal
// path before OSPanic_SpinForever). Logs all four args plus srr0/srr1 so
// a Dolphin user watching the HLE log sees where and why the CPU faulted,
// before OSDumpContext fires.
extern "C" void PanicLogUnhandled(u8 exception_type, void* ctx, u32 dsisr, u32 dar) {
    EnsureDBATWidened();
    u32 srr0 = 0, srr1 = 0;
    if (ctx) {
        srr0 = *(u32*)((u8*)ctx + OFF_SRR0);
        srr1 = *(u32*)((u8*)ctx + OFF_SRR1);
    }
    DebugPrintfSafe(
        "MKGP2 EXCEPTION #%d ctx=%p srr0=%08x srr1=%08x dsisr=%08x dar=%08x\n",
        (int)exception_type, ctx, srr0, srr1, dsisr, dar);
}

// Original first instruction at 0x80257f00 is `mflr r0` (0x7c0802a6). We
// replay it at the tail so execution continues at 0x80257f04 (the stw r0,...
// that saves LR into the caller's frame) with r0 holding the caller's LR.
asm void UnhandledExceptionHook() {
    nofralloc
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)
    stw  r31, 0x1c(r1)
    stw  r30, 0x18(r1)
    stw  r29, 0x14(r1)
    stw  r28, 0x10(r1)
    // Preserve the four incoming args; DebugPrintfSafe clobbers r3..r7.
    mr   r31, r3
    mr   r30, r4
    mr   r29, r5
    mr   r28, r6
    bl   PanicLogUnhandled
    mr   r3, r31
    mr   r4, r30
    mr   r5, r29
    mr   r6, r28
    lwz  r28, 0x10(r1)
    lwz  r29, 0x14(r1)
    lwz  r30, 0x18(r1)
    lwz  r31, 0x1c(r1)
    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    mflr r0
    blr
}

kmBranch(0x80257f00, UnhandledExceptionHook);
kmPatchExitPoint(UnhandledExceptionHook, 0x80257f04);

// --------- OSDumpContext entry hook (0x80257848) -------------------------
// Signature: (OSContext* ctx). Secondary chokepoint -- some panic paths call
// OSDumpContext directly without going through OSUnhandledException, so
// hooking both ensures a log line on every crash.
extern "C" void PanicLogDumpContext(void* ctx) {
    EnsureDBATWidened();
    u32 srr0 = 0, srr1 = 0;
    if (ctx) {
        srr0 = *(u32*)((u8*)ctx + OFF_SRR0);
        srr1 = *(u32*)((u8*)ctx + OFF_SRR1);
    }
    DebugPrintfSafe("MKGP2 CONTEXT @ %p: SRR0=%08x SRR1=%08x\n", ctx, srr0, srr1);
}

// Original first instruction at 0x80257848 is `mflr r0` (0x7c0802a6); replay
// at tail and exit to 0x8025784c (stw r0, 0x4(r1)).
asm void DumpContextHook() {
    nofralloc
    stwu r1, -0x10(r1)
    mflr r0
    stw  r0, 0x14(r1)
    stw  r31, 0x0c(r1)
    mr   r31, r3
    bl   PanicLogDumpContext
    mr   r3, r31
    lwz  r31, 0x0c(r1)
    lwz  r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    mflr r0
    blr
}

kmBranch(0x80257848, DumpContextHook);
kmPatchExitPoint(DumpContextHook, 0x8025784c);
