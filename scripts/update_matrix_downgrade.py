import sys
from pathlib import Path

matrix_path = Path(r"F:\AI_Projects\h3\local_drama_studio\docs\evidence\ui-uat-2026-08-22\CONTROL_STATE_MATRIX.md")
content = matrix_path.read_text(encoding="utf-8", errors="replace")

# Replace PASS in rows 091-098 with INVALID_AS_UI_EVIDENCE
for row_num in range(91, 99):
    marker = f"| **0{row_num}** |"
    if marker in content:
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if line.startswith(marker):
                # replace `PASS` at the end
                if "`PASS`" in line:
                    lines[i] = line.replace("`PASS`", "`INVALID_AS_UI_EVIDENCE`")
        content = "\n".join(lines)

matrix_path.write_text(content, encoding="utf-8")
print("CONTROL_STATE_MATRIX.md updated with INVALID_AS_UI_EVIDENCE for rows 091-098!")
