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

## B. Add the 5 new clients (get from 5 → 8 orgs)

The Xero limit is **25 orgs**, not 5 — you just haven't authorized the others yet. No rotation needed.

**Already connected (3 of your 8):** S Grill House, S Grill Kitchen, Claypot Curry Fishhead 261.
**To add (5):** JWS (BB), HZ Cuisine, HZS Cuisine, Nest Delight Holding, Claypot Curry Fishhead (the non-261 one).

### 1. Check your Xero login has access to all 5
You can only tick an org on the consent screen if your login is an advisor/user on it. Confirm you're on all 5 in Xero first.

### 2. Re-run the consent
Locally: `python xero_auth.py`. Log in, and on the org-picker **tick all 8 target orgs** (the 3 existing + 5 new). This writes a fresh `xero_tokens.json` listing all of them.

### 3. Push the new token to Railway
The live agent reads the token from an env var, not the file. Regenerate it:
- Base64-encode the new `xero_tokens.json` and set it as `XERO_TOKENS_JSON_B64` on Railway.
- On Windows PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("xero_tokens.json"))`
- Restart the Railway service so it picks up the new token.

### 4. Match Drive folder names to Xero org names
The agent matches each client's Drive folder name to its Xero org name. For the 5 new clients, make sure each has a **"Vendor invoices"** folder and the client folder name matches its Xero org name (exact match is safest). Watch the two Claypot entities — "261" vs non-"261" must stay distinct.

### 5. Test one invoice per new client
Drop a test invoice into one new client's "Vendor invoices" folder, let the hourly run pick it up (or trigger it), and confirm it posts to *that* client's Xero — not a wrong org, not an error.

## Notes
- `WEBHOOK_SECRET` must be identical in Railway and in `Code.gs` (it now also guards the digest endpoint). Both currently hold the placeholder `change_this_to_a_random_string` — set a real random string in both when you get a chance.
- The digest sends even on a clean night ("all good, N posted") — that's intentional, you wanted status not silence.
- If a client's org isn't connected, its invoice is now logged as `org_not_connected` (in both the exception sheet and the digest) instead of being posted to the wrong company.
