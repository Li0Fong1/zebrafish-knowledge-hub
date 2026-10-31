while ($true) {
  Start-Sleep -Seconds 2700
  Write-Output 'AGENT_LOOP_TICK_overnight_fulltext {"prompt":"Babysit online fulltext overnight: if dead/stalled restart supervisor; merge report; append one line status to logs/online_fulltext_overnight.md"}'
}
