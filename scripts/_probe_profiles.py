"""只读探测：从既有数据库读出 manifest 曾登记的 capability / 组件 / runtime。

用于在外部 model_manifest.json 被覆盖后，恢复“本机真实资产”的事实依据。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(sys.argv[1] if len(sys.argv) > 1 else r"F:\AI_Projects\h3\local_drama_studio\data\local_drama.sqlite3")


def main() -> None:
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    print("db:", DB)
    for table in ("local_runtimes", "model_artifacts", "execution_profiles", "execution_profile_versions"):
        try:
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error as error:
            print(f"{table}: missing ({error})")
            continue
        print(f"{table}: {count}")

    print("\n== local_runtimes ==")
    for row in connection.execute("SELECT code,title,base_url,executable_ref,runtime_version,status FROM local_runtimes"):
        print(dict(row))

    print("\n== profile capabilities ==")
    try:
        rows = connection.execute(
            """SELECT p.code AS profile_code, p.title, v.capability, v.status, v.manifest_sha256, v.capability_contract_json
            FROM execution_profile_versions v JOIN execution_profiles p ON p.id = v.execution_profile_id
            ORDER BY v.capability, p.code"""
        ).fetchall()
    except sqlite3.Error as error:
        print("query failed:", error)
        rows = []
    for row in rows:
        contract = {}
        try:
            contract = json.loads(str(row["capability_contract_json"] or "{}"))
        except json.JSONDecodeError:
            pass
        manifest_capability = contract.get("manifest_capability")
        print(
            f"- {row['capability']:<26} profile={row['profile_code']:<34} status={row['status']:<20} "
            f"alias={contract.get('manifest_capability_alias')} route={contract.get('route_status')}"
        )
        if manifest_capability:
            print("    manifest_capability:", json.dumps(manifest_capability, ensure_ascii=False)[:600])

    print("\n== model artifacts (path refs) ==")
    try:
        for row in connection.execute(
            "SELECT DISTINCT kind, machine_path_ref, size_bytes, sha256, status FROM model_artifacts ORDER BY kind, machine_path_ref"
        ):
            print(dict(row))
    except sqlite3.Error as error:
        print("query failed:", error)
    connection.close()


if __name__ == "__main__":
    main()
