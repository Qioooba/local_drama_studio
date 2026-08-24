$ErrorActionPreference = 'Stop'
$projectRoot = 'F:\AI_Projects\h3\local_drama_studio'

Set-Location "$projectRoot\apps\web"
& node node_modules\vite\bin\vite.js --host 0.0.0.0 --port 5173
