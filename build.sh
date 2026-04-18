#!/bin/bash
set -e

CW="/c/Program Files (x86)/Freescale/CW for MPC55xx and MPC56xx 2.10/PowerPC_EABI_Tools/Command_Line_Tools"
KAMEK_STDLIB="/c/Users/naari/src/github.com/Treeki/Kamek/k_stdlib"
KAMEK="/c/Users/naari/src/github.com/Treeki/Kamek/Kamek.exe"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Generating header from YAML ==="
python3 "$PATCH_DIR/gen_joints_header.py"

echo "=== Compiling ==="
"$CW/mwcceppc.exe" \
    -I- -i "$KAMEK_STDLIB" -i "$PATCH_DIR" \
    -Cpp_exceptions off -enum int -Os \
    -use_lmw_stmw on -fp hard -rostr \
    -sdata 0 -sdata2 0 \
    -c -o "$PATCH_DIR/joint_extend.o" "$PATCH_DIR/joint_extend.cpp"

echo "=== Linking ==="
"$KAMEK" \
    "$PATCH_DIR/joint_extend.o" \
    -static=0x806ED000 \
    -externals="$PATCH_DIR/externals.txt" \
    -output-gecko="$PATCH_DIR/joint_extend_gecko.txt" \
    -output-riiv="$PATCH_DIR/joint_extend_riiv_raw.xml" \
    -output-code="$PATCH_DIR/joint_extend.bin"

echo "=== Wrapping Riivolution XML ==="
{
  echo '<wiidisc version="1">'
  echo '  <id game="GNLJ"/>'
  echo '  <options>'
  echo '    <section name="MKGP2 Joint Extend">'
  echo '      <option name="Custom Joints" default="1">'
  echo '        <choice name="Enabled">'
  echo '          <patch id="joint_extend"/>'
  echo '        </choice>'
  echo '      </option>'
  echo '    </section>'
  echo '  </options>'
  echo '  <patch id="joint_extend">'
  while IFS= read -r line || [ -n "$line" ]; do
    echo "    $line"
  done < "$PATCH_DIR/joint_extend_riiv_raw.xml"
  echo '  </patch>'
  echo '</wiidisc>'
} > "$PATCH_DIR/joint_extend.xml"

RIIV_DIR="/c/Users/naari/Documents/Dolphin Emulator/Load/Riivolution/riivolution"
mkdir -p "$RIIV_DIR"
cp "$PATCH_DIR/joint_extend.xml" "$RIIV_DIR/joint_extend.xml"

echo "=== Done ==="
echo "Riivolution: $RIIV_DIR/joint_extend.xml"
echo "Gecko:       $PATCH_DIR/joint_extend_gecko.txt"
