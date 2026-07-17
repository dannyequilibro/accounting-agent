# Setup — Daily digest + adding the 5 new clients

Two things were built: (A) a morning Telegram status digest, (B) a guard so an invoice for an unconnected org gets flagged instead of posted to the wrong company. Here's what you need to do to switch them on.

## A. Turn on the daily digest

### 1. Add three env vars on Railway
In the Railway project → Variables, add:

```
RUN_LOG_SHEET_ID=10YlfzXYT9zVjMdHFEV5mzJLK_2yanIH3bvfNL-6xIgA
TELEGRAM_BOT_TOKEN=<your bot token — copy from C:\Users\danny\.claude\telegram-notify\config.json>
TELEGRAM_CHAT_ID=238201264
```

(The Run Log sheet is already created and shared to you — it's the `[Accounting Agent] Run Log` sheet in the Agents folder. The bot token is deliberately not written here so it doesn't get committed to git — it's the same token in your `telegram-notify/config.json` and in your local `.env`.)

### 2. Deploy the new code
Push the `accounting-agent` folder to Railway (same way you deploy now). New/changed files: `run_log.py`, `digest.py`, `main.py`, `xero_client.py`.

### 3. Schedule the 7am message in Apps Script
- Open the Apps Script project (script.google.com) and replace the code with the updated `apps_script/Code.gs`.
- In Project Settings, set the timezone to **Asia/Singapore** (so 7am = 7am SGT).
- Run the `setup` function once. It now creates two triggers: the hourly file check + a daily digest at 7am.

### 4. Test it
In Apps Script, run `sendDailyDigest` manually — you should get a Telegram message within a few seconds. (If the log is empty it says "no invoices processed in the last 24h. Agent is up.")

## B. Serve 8 clients on a 5-org cap — by rotation

Your Xero app is genuinely capped at **5 connected orgs** (confirmed by the "connection limit reached" screen). Since your 8 clients bill at different times, we rotate: keep the 5 currently-active clients connected, and swap when a different one has invoices waiting.

**The two moves:**
- **Disconnect** an idle org — instant, via API, no browser, no Railway change.
- **Connect** a client back in — needs your Xero login + "Allow" once (Xero requires the org's consent; can't be automated). This mints a fresh token you push to Railway.

**Why status/disconnect run against Railway, not your laptop:** Xero rotates the refresh token on every use. If your laptop and Railway both refresh it, they invalidate each other and the agent's auth silently breaks. So `rotate.py` sends status/disconnect to the live Railway app (the single token owner). Only `connect` runs locally, and it makes a brand-new token.

### Day-to-day rotation

1. **The morning digest tells you when to rotate.** If a not-connected client got invoices, you'll see a `🔄 Rotate in` line naming it.
2. **See the picture:** `python rotate.py status` — shows the 5 connected orgs, all 8 targets (in/out), and who's waiting.
3. **Free a slot if all 5 are used:** `python rotate.py disconnect "<idle org name>"`.
4. **Bring the waiting client in:** `python rotate.py connect` — opens Xero consent; tick that client.
5. **Push the new token to Railway:** `python rotate.py railway-token` prints the base64 — set it as `XERO_TOKENS_JSON_B64` on Railway and restart. (Only needed after a *connect*, never after a *disconnect*.)

### One-time prep
- **Env vars:** `.env` now also has `RAILWAY_URL` and needs `WEBHOOK_SECRET` to match Railway's (rotate.py authenticates with it). On Railway, `WEBHOOK_SECRET` must be the same string.
- **Drive folder names** for all 8 clients must match their Xero org names (exact is safest). Keep the two Claypot entities — "261" vs non-"261" — distinct.
- **Longer-term:** if rotating gets tedious, ask Xero to raise your app's connection limit or get the app **certified** — either removes the 5-cap for good. Rotation is the stopgap.

### Test it (after the next deploy)
- `python rotate.py status` lists your 5 connected orgs and flags any waiting client.
- `python rotate.py disconnect "<org>"` drops it to 4/5; `status` confirms.
- Reconnect it with `connect`, push the token, and a test invoice for it posts to the right org.

## Notes
- `WEBHOOK_SECRET` must be identical in Railway and in `Code.gs` (it now also guards the digest endpoint). Both currently hold the placeholder `change_this_to_a_random_string` — set a real random string in both when you get a chance.
- The digest sends even on a clean night ("all good, N posted") — that's intentional, you wanted status not silence.
- If a client's org isn't connected, its invoice is now logged as `org_not_connected` (in both the exception sheet and the digest) instead of being posted to the wrong company.
