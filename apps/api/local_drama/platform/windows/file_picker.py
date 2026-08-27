from __future__ import annotations

import subprocess
from pathlib import Path

from local_drama.domain.errors import DomainRuleError
from local_drama.platform.contracts import FilePickerRequest, FilePickerResult


class WindowsFilePicker:
    name = "WINDOWS_NATIVE_FILE_PICKER"
    available = True
    timeout_seconds = 60

    def choose(self, request: FilePickerRequest) -> FilePickerResult:
        filters = ";".join(f"*{suffix}" for suffix in request.allowed_suffixes)
        script = rf"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '{request.title.replace("'", "''")}'
$dialog.Filter = 'Allowed files ({filters})|{filters}|All files (*.*)|*.*'
$dialog.CheckFileExists = $true
$dialog.Multiselect = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
  [Console]::Out.Write($dialog.FileName)
}}
"""
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("LOCAL_FILE_PICKER_UNAVAILABLE", "无法打开本机文件选择器") from error
        if completed.returncode != 0:
            raise DomainRuleError("LOCAL_FILE_PICKER_FAILED", "本机文件选择器执行失败")
        selected = completed.stdout.strip()
        if not selected:
            return FilePickerResult(False, None)
        path = Path(selected).resolve()
        if not path.is_file() or path.is_symlink():
            raise DomainRuleError("LOCAL_FILE_PATH_INVALID", "选择的路径缺失、不是文件或为 symlink")
        if request.allowed_suffixes and path.suffix.casefold() not in {item.casefold() for item in request.allowed_suffixes}:
            raise DomainRuleError("LOCAL_FILE_TYPE_UNSUPPORTED", "选择的文件类型不受支持")
        return FilePickerResult(True, path)
