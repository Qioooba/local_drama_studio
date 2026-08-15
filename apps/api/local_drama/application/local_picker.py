"""Windows-native local file picker for this loopback-only desktop platform."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError

_MODEL_PICKER_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '选择电脑中的模型文件'
$dialog.Filter = 'Model files (*.safetensors;*.ckpt;*.bin;*.pt;*.pth)|*.safetensors;*.ckpt;*.bin;*.pt;*.pth|All files (*.*)|*.*'
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dialog.FileName)
}
"""

_DOCUMENT_PICKER_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '选择电脑中的剧本文档'
$dialog.Filter = 'Script documents (*.txt;*.md;*.markdown;*.docx)|*.txt;*.md;*.markdown;*.docx|All files (*.*)|*.*'
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dialog.FileName)
}
"""


def _pick_local_file(script: str, unavailable_code: str, failed_code: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DomainRuleError(unavailable_code, "无法打开本机文件选择器") from error
    if completed.returncode != 0:
        raise DomainRuleError(failed_code, "本机文件选择器执行失败")
    selected = completed.stdout.strip()
    if not selected:
        return {"selected": False, "path": None, "uploaded": False, "copied": False}
    path = Path(selected).resolve()
    if not path.is_file() or path.is_symlink():
        raise DomainRuleError("LOCAL_FILE_PATH_INVALID", "选择的路径缺失、不是文件或为 symlink")
    return {"selected": True, "path": str(path), "uploaded": False, "copied": False}


def pick_local_model_file() -> dict[str, Any]:
    return _pick_local_file(_MODEL_PICKER_SCRIPT, "LOCAL_MODEL_PICKER_UNAVAILABLE", "LOCAL_MODEL_PICKER_FAILED")


def pick_local_document_file() -> dict[str, Any]:
    result = _pick_local_file(_DOCUMENT_PICKER_SCRIPT, "LOCAL_DOCUMENT_PICKER_UNAVAILABLE", "LOCAL_DOCUMENT_PICKER_FAILED")
    if result["selected"] and Path(str(result["path"])).suffix.lower() not in {".txt", ".md", ".markdown", ".docx"}:
        raise DomainRuleError("UNSUPPORTED_DOCUMENT_TYPE", "剧本文档仅支持 TXT、Markdown、DOCX")
    return result
