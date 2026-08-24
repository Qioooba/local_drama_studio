param(
  [Parameter(Mandatory = $true)][string]$OutputPath,
  [string]$VoiceName = "Microsoft Huihui Desktop",
  [string]$Text = "LocalDramaStudio local SAPI smoke test"
)

$ErrorActionPreference = "Stop"
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$parent = [System.IO.Path]::GetDirectoryName($resolvedOutput)
if (-not [System.IO.Directory]::Exists($parent)) {
  [System.IO.Directory]::CreateDirectory($parent) | Out-Null
}

$runtime = "System.Speech"
try {
  Add-Type -AssemblyName System.Speech
  $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
  try {
    $installed = @($synthesizer.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
    if ($VoiceName -notin $installed) { throw "Requested SAPI voice is not installed." }
    $synthesizer.SelectVoice($VoiceName)
    $synthesizer.SetOutputToWaveFile($resolvedOutput)
    $synthesizer.Speak($Text)
  }
  finally {
    if ($null -ne $synthesizer) { $synthesizer.Dispose() }
  }
}
catch {
  try {
    $runtime = "SAPI.SpVoice COM"
    $voice = New-Object -ComObject SAPI.SpVoice
    $tokens = @($voice.GetVoices())
    $token = $tokens | Where-Object { $_.GetDescription() -eq $VoiceName -or $_.GetDescription().StartsWith($VoiceName) } | Select-Object -First 1
    if ($null -eq $token) { throw "Requested SAPI voice is not installed." }
    $stream = New-Object -ComObject SAPI.SpFileStream
    try {
      $voice.Voice = $token
      $stream.Open($resolvedOutput, 3, $false)
      $voice.AudioOutputStream = $stream
      [void]$voice.Speak($Text)
    }
    finally {
      if ($null -ne $stream) { $stream.Close() }
    }
  }
  catch {
    $runtime = "Windows.Media.SpeechSynthesis"
    [void][System.Reflection.Assembly]::LoadWithPartialName("System.Runtime.WindowsRuntime")
    [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime] | Out-Null
    [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

    function Await-WinRtOperation($operation, [Type]$resultType) {
      $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
      $task = $method.MakeGenericMethod($resultType).Invoke($null, @($operation))
      $task.Wait()
      return $task.Result
    }

    $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    try {
      $voiceKey = (($VoiceName -replace '^Microsoft\s+', '') -replace '\s+Desktop$', '')
      $oneCoreVoice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
        Where-Object { $_.DisplayName -like "*$voiceKey*" } | Select-Object -First 1
      if ($null -eq $oneCoreVoice) { throw "Requested OneCore voice is not installed." }
      $synth.Voice = $oneCoreVoice
      $speechStream = Await-WinRtOperation ($synth.SynthesizeTextToStreamAsync($Text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
      $reader = New-Object Windows.Storage.Streams.DataReader($speechStream)
      try {
        [void](Await-WinRtOperation ($reader.LoadAsync([uint32]$speechStream.Size)) ([uint32]))
        $bytes = New-Object byte[] ([int]$speechStream.Size)
        $reader.ReadBytes($bytes)
        [System.IO.File]::WriteAllBytes($resolvedOutput, $bytes)
      }
      finally {
        $reader.Dispose()
        $speechStream.Dispose()
      }
    }
    finally {
      $synth.Dispose()
    }
  }
}

$file = Get-Item -LiteralPath $resolvedOutput
[pscustomobject]@{
  status = "PASS"
  runtime = $runtime
  voice = $VoiceName
  output = $file.FullName
  byte_size = $file.Length
  network_contacted = $false
} | ConvertTo-Json -Compress
