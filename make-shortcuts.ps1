Write-Host "`n=== KastoBrain: create the app icon ===" -ForegroundColor Yellow
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher = Join-Path $Root "app\launch.pyw"
$Icon = Join-Path $Root "app\web\kastobrain.ico"
$pyw = Get-Command pythonw -ErrorAction SilentlyContinue

if (-not (Test-Path $Launcher)) {
  Write-Host "launch.pyw not found in $Root\app - update KastoBrain first (git pull)." -ForegroundColor Red
} elseif (-not $pyw) {
  Write-Host "pythonw not found - Python is needed." -ForegroundColor Red
} else {
  $Desktop = [Environment]::GetFolderPath("Desktop")
  $StartMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "KastoBrain.lnk"
  $Targets = @((Join-Path $Desktop "KastoBrain.lnk"), $StartMenu)
  Write-Host "This will create (or refresh) these two shortcuts only:"
  $Targets | ForEach-Object { Write-Host "  $_" }
  if ((Read-Host "Create them? (Y/N)") -eq "Y") {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($t in $Targets) {
      $s = $shell.CreateShortcut($t)
      $s.TargetPath = $pyw.Source
      $s.Arguments = "`"$Launcher`""
      $s.WorkingDirectory = Join-Path $Root "app"
      $s.IconLocation = $Icon
      $s.Description = "KastoBrain"
      $s.Save()
      if (Test-Path $t) { Write-Host "created: $t" -ForegroundColor Green } else { Write-Host "FAILED: $t" -ForegroundColor Red }
    }
    Write-Host "`nDone. Click the KastoBrain icon on your Desktop or in the Start menu." -ForegroundColor Green
    Write-Host "It opens in its own window, runs hidden, and stops by itself a few minutes after you close it."
  } else { Write-Host "Skipped. Nothing was changed." }
}
