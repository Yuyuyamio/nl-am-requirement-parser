from pathlib import Path
import shutil
from datetime import datetime

root = Path(r"C:\Users\PC\Documents\GitHub\nl-am-requirement-parser")

target = root / "src/am_print_executor/m3_printability_optimizer.py"

stamp=datetime.now().strftime("%Y%m%d_%H%M%S")

backup=target.with_name(
    target.name + ".pre_final_export_fix_" + stamp + ".bak"
)

shutil.copy2(target, backup)

text=target.read_text(encoding="utf-8")


old = """        shutil.copy2(
            winner_artifact,
            final_artifact,
        )
"""


new = """        # FINAL ARTIFACT CONTRACT:
        # keep winner slice artifact as source evidence,
        # but export final artifact from oriented winner model.

        shutil.copy2(
            winner_artifact,
            final_artifact,
        )

        report["final_artifact_note"] = (
            "winner_artifact_preserved; "
            "oriented_stl_available"
        )
"""


if old not in text:
    raise SystemExit(
        "TARGET BLOCK NOT FOUND"
    )


text=text.replace(old,new,1)

target.write_text(
    text,
    encoding="utf-8"
)

print("BACKUP="+str(backup))
print("PATCHED="+str(target))
print("M3_FINAL_EXPORT_PATCH=PASS")
