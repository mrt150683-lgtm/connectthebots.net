# Owner's numbers for connectthebots.net, run from the deploy clone via STATS.cmd.
# Read-only: asks the server for counts (never names or emails) and prints them.
# Visitors and film plays come from the shortened access log, so they are
# approximate and include some bots. Sign-ups and agents come from the database.
param(
  [string]$Server = "root@connectthebots.net",
  [ValidateRange(1, 30)][int]$Days = 14,
  [string]$Csv = ""
)
$ErrorActionPreference = "Stop"

function Ask($command) {
  $text = (& ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 $Server $command | Out-String)
  if ($LASTEXITCODE -ne 0 -or -not $text.Trim()) {
    throw "The server did not answer. Run DEPLOY once (it sets up the key and installs the numbers command), then try again."
  }
  return $text | ConvertFrom-Json
}

$python = "/opt/connectthebots/venv/bin/python"
$db = Ask ("systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/etc/connectthebots/app.env -p User=ctb -p Group=ctb $python -m connectthebots_server.admin numbers --days $Days")
$web = Ask ("$python -B -m connectthebots_server.traffic --days $Days /var/log/nginx/connectthebots.access.log*")

function Line($label, $value, $extra) { Write-Host ("  {0,-26}{1,8}   {2}" -f $label, $value, $extra) }

Write-Host ""
Write-Host "Connect the Bots, last $Days days ($($web.from) to $($web.to), UTC)" -ForegroundColor Cyan
Write-Host ""
Line "Visitors (approx.)" $web.visitors "page views $($web.page_views): home $($web.pages.'/'), about $($web.pages.'/about')"
Line "Film started" $web.video_plays "sound turned on $($web.video_sound_on), watched to the end $($web.video_finished)"
$recent = ($db.signups.by_day.PSObject.Properties | Measure-Object -Property Value -Sum).Sum
Line "Sign-ups" $recent "all time $($db.signups.total), last 24 hours $($db.signups.last_24_hours)"
Line "People with an agent" $db.agents.people_with_an_agent_connected "agents active in last 7 days $($db.agents.agents_active_last_7_days)"
Line "Agent check-ins" $db.checkins.total "last 7 days $($db.checkins.last_7_days)"
Line "Referrals" $db.referrals.total "qualified $($db.referrals.qualified), flagged $($db.referrals.flagged), last 7 days $($db.referrals.last_7_days)"
Line "Introductions" $db.introductions.requested "accepted $($db.introductions.accepted), open conversations $($db.open_conversations)"
Write-Host ""

$rows = foreach ($day in $web.by_day) {
  [pscustomobject]@{
    Day = $day.day
    Visitors = $day.visitors
    PageViews = $day.page_views
    FilmStarted = $day.video_plays
    SoundOn = $day.video_sound_on
    Finished = $day.video_finished
    SignUps = $db.signups.by_day.($day.day)
    AgentsConnected = $db.agents.connected_by_day.($day.day)
  }
}
$rows | Format-Table -AutoSize | Out-String | Write-Host
Write-Host "Visitors are counted by shortened network address, so they are approximate and include some bots."
Write-Host "Sign-ups and agents are current accounts; deleted accounts are not counted."

if ($Csv) {
  $rows | Export-Csv -NoTypeInformation -Path $Csv
  Write-Host "Saved the daily table to $Csv"
}
