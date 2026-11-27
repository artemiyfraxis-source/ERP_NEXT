---
name: owner-time-berlin
description: Owner lives and works in Berlin — all times and deadlines in Berlin time, never MSK; Git Bash TZ prints UTC
metadata:
  type: user
---

Owner 17.11.2026: «я по берлину живу и работаю там же» — deadlines («до 5 утра»), shifts and times in chat are Berlin time.
Don't convert to or mention MSK in replies (the «MSK» in older repo notes about the release schedule is the server's setting,
not the owner's clock).
Git Bash on this PC has no tz database: `TZ=Europe/Moscow date` silently prints UTC — I once reported «03:52 МСК» at 04:52
Berlin. Get local time with PowerShell: `[TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'W. Europe Standard Time')`.
