#include <kamek.h>
#include "patch_common.h"

// TimeAttack コース選択の cursor=0/1 枠 (ヨッシーランド) を test_course に差し替え。
//
// clFlowCourse_Dtor (0x801d0c30) 内の switch (cursor/2) テーブルで
// case 0 は `li r0, 0x7` (courseId=7 → YI) を実行する。
// この即値 7 を 0 に書き換えることで、コース選択画面の先頭エントリ
// (short YI / long YI) が courseId=0 (test_course) として起動する。
//
// 副作用: TimeAttack で YI コースへの正規ルートが塞がる (test_course と入れ替わる)。
// 他のコース選択枠は影響なし。
//
// 参照: mkgp2_item_select_menu.md (clFlowCourse_Dtor の courseId switch),
//       mkgp2_course_layout_system.md (courseId=0 = TEST course, ファイルは
//       Riivolution dump の test_course_*.dat で確認済み)

// 0x801d0cf0: li r0, 0x7 → li r0, 0x0
kmWrite32(0x801d0cf0, 0x38000000);
