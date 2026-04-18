#include <kamek.h>
#include "generated_joints.h"

extern "C" {

void DebugPrintf(const char* fmt, ...);
void DebugPrintfSafe(const char* fmt, ...);  // wrapper, see asm below
int  ResolveJointByName(int archive, const char* name);
void JObj_Show(int archive, int jobjIdx, int flags);
void JObj_Hide(int archive, int jobjIdx, int flags);
void clNormal3D_SetFlags(int archive, int flags);
const char** GetJointNameTable();

extern int g_courseId;
extern int g_isUraCourse;
extern int g_mirrorMode;

}

// Widen DBAT0 to cover 0x80000000-0x8FFFFFFF (256MB) so HLE/MMU can read
// data from our patch region (0x806EDxxx). MKGP2's __start installs a narrow
// DBAT0 (~32MB) and wider IBAT0, so code runs but data reads from our region
// fail via PPC MMU. One-shot: invalidates JIT cache on DBAT update.
extern "C" asm void WidenDBAT0_256M() {
    nofralloc
    // BATU = BEPI(0x80000000) | BL(0x7FF << 2) | VS(1<<1)
    lis r3, 0x8000
    ori r3, r3, 0x1FFE
    // BATL = BRPN(0) | WIMG=M(0x2 << 3) | PP=rw(0x2)
    li  r4, 0x12
    sync
    mtspr 537, r4       // DBAT0L
    mtspr 536, r3       // DBAT0U
    sync
    isync
    blr
}

// Dolphin's HLE_GeneralDebugPrint uses a heuristic: if r3 is a valid RAM address
// AND *r3 also looks like a valid RAM address, it assumes r3 is a C++ `this`
// pointer and reads the format string from r4. Otherwise it reads from r3.
//
// For our format strings placed in the Kamek patch region, the first 4 bytes
// ("MKGP" = 0x4D4B4750) happen to resolve to a "valid RAM" address via PPC
// segment-register translation in MKGP2. HLE then mistakenly treats r3 as
// `this` and reads garbage from r4/r5.
//
// Workaround: shift all integer varargs registers by one and set r3 = 0.
// Dolphin's HLE will take the `this`-style path (r3=null), see r4 as a valid
// RAM pointer (our format string), and print correctly. The real DebugPrintf
// runs after HLE with r3=0 but won't crash (it reads harmless low-memory bytes).
extern "C" asm void DebugPrintfSafe(const char* fmt, ...) {
    nofralloc
    // Shift int varargs regs up one slot: r10->r11, r9->r10, ... r3->r4
    mr   r11, r10
    mr   r10, r9
    mr   r9, r8
    mr   r8, r7
    mr   r7, r6
    mr   r6, r5
    mr   r5, r4
    mr   r4, r3
    li   r3, 0
    b    DebugPrintf
}

enum Visibility {
    VIS_ALWAYS,
    VIS_SHORT_ONLY,
    VIS_LONG_ONLY,
    VIS_NORMAL_ONLY,
    VIS_REVERSE_ONLY,
};

static int strcmp(const char* a, const char* b) {
    while (*a && *a == *b) { a++; b++; }
    return *a - *b;
}

static int endsWith(const char* str, const char* suffix) {
    const char* s = str;
    const char* p = suffix;
    int slen = 0, plen = 0;
    while (*s) { slen++; s++; }
    while (*p) { plen++; p++; }
    if (plen > slen) return 0;
    return strcmp(str + slen - plen, suffix) == 0;
}

static Visibility ClassifyJoint(const char* name) {
    if (endsWith(name, "_normal_joint"))  return VIS_NORMAL_ONLY;
    if (endsWith(name, "_reverse_joint")) return VIS_REVERSE_ONLY;

    const char* p = name;
    while (*p) {
        if (p[0] == 's' && p[1] == 'h' && p[2] == 'o' && p[3] == 'r' && p[4] == 't' && p[5] == '_')
            return VIS_SHORT_ONLY;
        if (p[0] == 'l' && p[1] == 'o' && p[2] == 'n' && p[3] == 'g' && p[4] == '_')
            return VIS_LONG_ONLY;
        p++;
    }
    return VIS_ALWAYS;
}

static int ShouldShow(Visibility vis, int isUra, int isMirror) {
    int isShort = !isUra;
    switch (vis) {
    case VIS_ALWAYS:       return 1;
    case VIS_SHORT_ONLY:   return isShort;
    case VIS_LONG_ONLY:    return !isShort;
    case VIS_NORMAL_ONLY:  return isMirror == 0;
    case VIS_REVERSE_ONLY: return isMirror != 0;
    }
    return 1;
}

static const char** GetJointsForCourse(int courseId) {
    for (int i = 0; sCourseJointDefs[i].courseId != -1; i++) {
        if (sCourseJointDefs[i].courseId == courseId)
            return sCourseJointDefs[i].joints;
    }
    return 0;
}

// Replicate original CourseScene_Load body (0x80047BB0-0x80048080):
//   - 18 ResolveJointByName calls storing results in state[0..0x12]
//   - uVar2 (short_occlusion) / uVar7 (long_occlusion) returned via outShort/outLong
//     (assembler wrapper places them into r28/r29 for code after exit point)
//   - Show/Hide dispatch per isUra × isMirror
//   - Appends YAML custom joints with suffix-based visibility
extern "C" void CourseJointLoadImpl(int* state, int* outShort, int* outLong) {
    static int s_dbat_widened = 0;
    if (!s_dbat_widened) {
        WidenDBAT0_256M();
        s_dbat_widened = 1;
    }

    DebugPrintfSafe("MKGP2 hello\n");

    *outShort = 0;
    *outLong  = 0;

    int archive = state[0x14]; // byte offset 0x50
    if (archive == 0) return;

    const char** nameTable = GetJointNameTable();

    // Hardcoded 18-slot joint resolution (preserve original semantics)
    state[0]    = ResolveJointByName(archive, nameTable[0]);     // share_road
    state[1]    = ResolveJointByName(archive, nameTable[1]);     // short_road
    state[2]    = ResolveJointByName(archive, nameTable[2]);     // long_road
    state[6]    = ResolveJointByName(archive, nameTable[3]);     // short_branch
    state[7]    = ResolveJointByName(archive, nameTable[4]);     // long_branch
    *outShort   = ResolveJointByName(archive, nameTable[5]);     // short_occlusion
    *outLong    = ResolveJointByName(archive, nameTable[6]);     // long_occlusion
    state[0xa]  = ResolveJointByName(archive, nameTable[9]);     // opac
    state[0xb]  = ResolveJointByName(archive, nameTable[10]);    // alpha
    state[0xc]  = ResolveJointByName(archive, nameTable[11]);    // short_alpha
    state[0xd]  = ResolveJointByName(archive, nameTable[12]);    // long_alpha
    state[0xe]  = ResolveJointByName(archive, nameTable[14]);    // short_normal
    state[0xf]  = ResolveJointByName(archive, nameTable[15]);    // long_normal
    state[0x10] = ResolveJointByName(archive, nameTable[16]);    // short_reverse
    state[0x11] = ResolveJointByName(archive, nameTable[17]);    // long_reverse
    if (nameTable[13] != 0) {
        state[0x12] = ResolveJointByName(archive, nameTable[13]); // nofog (conditional)
    }

    // Secondary archive (state[0x1c], byte offset 0x70)
    int archive2 = state[0x1c];
    if (archive2 == 0) {
        state[8] = 0;
        state[9] = 0;
    } else {
        state[8] = ResolveJointByName(archive2, nameTable[7]);
        state[9] = ResolveJointByName(archive2, nameTable[8]);
    }

    // Show/Hide logic
    if (state[0] != 0) clNormal3D_SetFlags(archive, 0x4000000);

    int isUra    = g_isUraCourse;
    int isMirror = g_mirrorMode;

    if (isUra == 0) {
        if (state[1] != 0)    clNormal3D_SetFlags(archive, 0x4000000);
        if (state[6] != 0)    JObj_Show(archive, state[6], 0x10);
        if (state[1] != 0)    JObj_Show(archive, state[1], 0x10);
        if (state[0xc] != 0)  JObj_Show(archive, state[0xc], 0x10);
        if (state[8] != 0)    JObj_Show(archive2, state[8], 0x10);
        if (state[7] != 0)    JObj_Hide(archive, state[7], 0x10);
        if (state[2] != 0)    JObj_Hide(archive, state[2], 0x10);
        if (state[0xd] != 0)  JObj_Hide(archive, state[0xd], 0x10);
        if (state[9] != 0)    JObj_Hide(archive2, state[9], 0x10);
        if (isMirror == 0) {
            if (state[0xe] != 0)  JObj_Show(archive, state[0xe], 0x10);
            if (state[0xf] != 0)  JObj_Hide(archive, state[0xf], 0x10);
            if (state[0x10] != 0) JObj_Hide(archive, state[0x10], 0x10);
            if (state[0x11] != 0) JObj_Hide(archive, state[0x11], 0x10);
        } else {
            if (state[0xe] != 0)  JObj_Hide(archive, state[0xe], 0x10);
            if (state[0xf] != 0)  JObj_Hide(archive, state[0xf], 0x10);
            if (state[0x10] != 0) JObj_Show(archive, state[0x10], 0x10);
            if (state[0x11] != 0) JObj_Hide(archive, state[0x11], 0x10);
        }
    } else {
        if (state[2] != 0)    clNormal3D_SetFlags(archive, 0x4000000);
        if (state[7] != 0)    JObj_Show(archive, state[7], 0x10);
        if (state[2] != 0)    JObj_Show(archive, state[2], 0x10);
        if (state[0xd] != 0)  JObj_Show(archive, state[0xd], 0x10);
        if (state[9] != 0)    JObj_Show(archive2, state[9], 0x10);
        if (state[6] != 0)    JObj_Hide(archive, state[6], 0x10);
        if (state[1] != 0)    JObj_Hide(archive, state[1], 0x10);
        if (state[0xc] != 0)  JObj_Hide(archive, state[0xc], 0x10);
        if (state[8] != 0)    JObj_Show(archive2, state[8], 0x10);
        if (isMirror == 0) {
            if (state[0xe] != 0)  JObj_Hide(archive, state[0xe], 0x10);
            if (state[0xf] != 0)  JObj_Show(archive, state[0xf], 0x10);
            if (state[0x10] != 0) JObj_Hide(archive, state[0x10], 0x10);
            if (state[0x11] != 0) JObj_Hide(archive, state[0x11], 0x10);
        } else {
            if (state[0xe] != 0)  JObj_Hide(archive, state[0xe], 0x10);
            if (state[0xf] != 0)  JObj_Hide(archive, state[0xf], 0x10);
            if (state[0x10] != 0) JObj_Hide(archive, state[0x10], 0x10);
            if (state[0x11] != 0) JObj_Show(archive, state[0x11], 0x10);
        }
    }

    // YAML custom joints: suffix-based visibility for names beyond the hardcoded 18
    const char** joints = GetJointsForCourse(g_courseId);
    if (joints != 0) {
        for (int i = 0; joints[i] != 0; i++) {
            int idx = ResolveJointByName(archive, joints[i]);
            if (idx == 0) continue;
            Visibility vis = ClassifyJoint(joints[i]);
            if (ShouldShow(vis, isUra, isMirror)) {
                JObj_Show(archive, idx, 0x10);
            } else {
                JObj_Hide(archive, idx, 0x10);
            }
        }
    }

    DebugPrintfSafe("MKGP2: joints loaded course=%d ura=%d mirror=%d occShort=%d occLong=%d\n",
                    g_courseId, isUra, isMirror, *outShort, *outLong);
}

// Hook: replaces 0x80047BB0-0x80048080. Exits into 0x80048080 via kmPatchExitPoint.
// Preserves r28/r29 convention: r28 = uVar2 (short_occlusion), r29 = uVar7 (long_occlusion)
asm void CourseJointLoadHook() {
    nofralloc
    stwu r1, -0x20(r1)
    mflr r0
    stw  r0, 0x24(r1)

    mr   r3, r31          // state pointer
    addi r4, r1, 0x08     // outShort
    addi r5, r1, 0x0C     // outLong
    bl   CourseJointLoadImpl

    lwz  r28, 0x08(r1)    // r28 = uVar2 (short_occlusion)
    lwz  r29, 0x0C(r1)    // r29 = uVar7 (long_occlusion)

    lwz  r0, 0x24(r1)
    mtlr r0
    addi r1, r1, 0x20
    blr
}

kmBranch(0x80047bb0, CourseJointLoadHook);
kmPatchExitPoint(CourseJointLoadHook, 0x80048080);

// Raise ArenaLo past our patch code (bin is 0x19C8 = ends at 0x806EE9C8; round up)
kmWrite32(0x80000030, 0x806EF000);
