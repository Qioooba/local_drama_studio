$ErrorActionPreference = 'Stop'
$projectRoot = 'F:\AI_Projects\h3\local_drama_studio'
$apiVenv = "$projectRoot\apps\api\.venv\Scripts\python.exe"
$apiLog = "$projectRoot\logs\api_startup.log"

Set-Location "$projectRoot\apps\api"
$env:PYTHONPATH = "$projectRoot\apps\api"
& $apiVenv -m uvicorn local_drama_studio_api.main:app --host 127.0.0.1 --port 8000 --reload
