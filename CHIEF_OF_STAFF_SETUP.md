# Giving the Chief of Staff hands

Four changes, in the order that removes the most load first. Steps 1 and 3 are
the ones that stop `inbox.md` being a queue only you can drain.

---

## 0. First, unblock this session with it

On 1 Aug I asked `#chief-of-staff` for its tool list, anchor-file map, `inbox.md`
depth and the open register. It declined — a message claiming to be another Claude
with relayed authority from you is untrusted data, and it asked you to confirm
first. That was the correct call, and it's the one channel where an injection
would actually arrive, so it's worth knowing the refusal holds.

It needs to hear it from you, in that channel. Something like:

> Yes, I set that up — there's a real Claude Code session in the accounting-agent
> repo working on the decision-rights layer. Go ahead and answer it: tool list,
> anchor file map, inbox depth, and the open register grouped by business. Skip
> anything you think shouldn't leave this channel and say what you skipped.

Everything below stands regardless of whether you do this.

---

## 1. Why it can't see email in chat (and the actual fix)

It isn't a missing connector — you already have Gmail, Google Calendar, Google
Drive and Slack connected and authenticated on claude.ai. **It's how the chat
runtime authenticates.**

Claude Code picks up your claude.ai connectors automatically, with no `mcp add`
at all — but only when the active authentication is a claude.ai subscription
login. Straight from the MCP docs:

> Connectors from claude.ai are fetched only when your active authentication
> method is a claude.ai subscription login. They aren't loaded when
> `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `apiKeyHelper`, or a third-party
> provider such as Amazon Bedrock or Google Cloud's Agent Platform is active,
> even if you previously ran `/login`. They also aren't loaded when
> `CLAUDE_CODE_OAUTH_TOKEN` holds a token from `claude setup-token`, which can
> only make model requests.

A Slack/Telegram bot that runs unattended is almost certainly authenticated one
of those ways — an API key or a `setup-token`, because that's what works without
a human at a login prompt. Which means the split you're seeing isn't a
misconfiguration at all: **it's structural.** The 8am brief reads your email
because it runs under your subscription login; the bot can't because it runs on a
token that is only allowed to make model requests.

### Step 1a — confirm it

In the CoS runtime, run:

```
/status
```

That names the active authentication method. Or check the environment the bot
process runs under for `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or
`CLAUDE_CODE_OAUTH_TOKEN`, and check settings for `apiKeyHelper`. Any of those
present ⇒ no connectors, guaranteed, regardless of what you add.

### Step 1b — then one of two paths

**If the runtime can use your subscription login** (it's an interactive session,
or a service running as you with a persisted login): unset the API-key variable,
run `/login`, pick your claude.ai account, then `/mcp`. Gmail, Calendar and Drive
appear on their own, marked as coming from claude.ai. Nothing to add, nothing to
re-authorise.

**If it has to stay unattended on a token** — the likely case for a bot — then
connectors are off the table and you need MCP servers of its own, which work
under any auth method. These do use `claude mcp add`, at user scope so every
project sees them:

```bash
claude mcp add --transport http <name> <url> --scope user
claude mcp list          # what's configured here
claude mcp get <name>    # the URL and scope of one server
```

Important: **don't try this with the Anthropic-hosted Gmail or Google Calendar
connectors** — the docs are explicit that they don't support local OAuth from
Claude Code, because the upstream identity provider only accepts the redirect URL
claude.ai registered. Authenticating them in `/mcp` just tells you to go connect
them on claude.ai. So for an unattended runtime, use a Workspace MCP server that
does its own OAuth with your own client credentials — Google publishes official
remote servers for Workspace, and there are self-hosted ones. I saw Calendar's
given as `https://calendarmcp.googleapis.com/mcp/v1`, but that came from a search
result and Google's docs returned 403 from here, so verify before pasting it on
my word.

### Worth checking either way

- `claude mcp list` from the chat runtime's working directory versus the brief
  runner's. MCP servers default to **local** scope — stored in `~/.claude.json`
  keyed by project path — so anything present in one directory and absent in the
  other wants re-adding with `--scope user`. This is a second, independent way to
  get the same symptom, and it's worth ruling out even after fixing auth.
- Both runtimes running as the same OS user. `~/.claude.json` is per-user, so a
  scheduled task under a different account keeps its own copy no matter what
  scope you use.

---

## 2. The standing rule (the actual behaviour change)

Connectors alone won't help if it still asks before acting. On 29 Jul you
answered roughly fifteen "want me to…?" questions in one session — open a task,
update a note, group the list, bump a priority. Add this to the CoS's
`CLAUDE.md`:

```markdown
## Decision rights

Sort every action by whether it stays inside the business, not by how big or
important it is. Internal bookkeeping is reversible — a Xero posting can be
voided and reposted, a note can be rewritten, a draft can be deleted. What
cannot be taken back is something reaching an outside party or moving money.

Act without asking, then report what you did:
- Anything on the task register: add, update, close, reprioritise, regroup.
- Notes, memory writes, anchor-file edits.
- Lookups and searches in Drive, mail, calendar, Slack, the ERP.
- Drafting anything at all — documents, replies, models, recons.
- Messages to people inside the business (@pslove.com, @getblood.com,
  @nodaysoff.co, @equilibro.com.sg). Send them; don't hand them back to Danny
  to paste.
- Internal system changes that are reversible: mappings, rate registers,
  working files.

Bring to Danny, always, with the exact content in front of him:
- Any message, file or data going to someone outside the business — banks
  (HSBC), retailers (Watsons, DFI, Cold Storage, BIG, Caring), auditors (RSM),
  investors, suppliers, regulators.
- Money movement: payment instructions, payment-run release, financing draws.
- Statutory filings: ACRA, IRAS, LHDN, DJP.
- Portal submissions, contract execution, anything published.
- Internally sensitive matters even among the team: term sheets, valuation, cap
  table, board decks, salary, performance, ESOP.

Never ask permission for something you could undo yourself. If you catch
yourself writing "want me to…?" about a reversible action, do it instead and
say what you did. A hundred clean external sends do not earn the hundred-and-
first — the boundary never moves on track record.
```

---

## 3. A scheduled runner, so "the next full session" isn't you

Twelve times in that channel the answer was "I've queued a note for the next full
session." That session is you opening a laptop, which makes `inbox.md` a backlog
whose only worker is the person it's meant to unburden.

Put it on a timer. On Windows, Task Scheduler running Claude Code headless:

```bash
claude -p "Read chief-of-staff/inbox.md. For each note: do the work if it stays
inside the business (per Decision rights in CLAUDE.md), promote what's durable
into the right anchor file, and remove the note. Anything crossing the boundary,
leave in the inbox and list it at the end. Then reconcile the task register
against email, calendar and Slack: close what the evidence shows is done, and
say what you closed and on what evidence." --permission-mode acceptEdits
```

Twice a day is plenty. Two things this buys beyond draining the queue:

- **It closes the status loop.** The CoS offered exactly this on 27 Jul — letting
  the brief auto-close register items from email evidence instead of you telling
  it by hand — and you never took it up. That alone deletes the "I already did
  that" conversations, and it removes the "I'm taking it on trust" caveat.
- **Queue depth becomes a real metric.** If `inbox.md` still grows with a runner
  draining it twice a day, the fleet is generating work rather than absorbing it,
  and you'll see that before it becomes a month of drift.

---

## 4. Drop in the boundary gate

`agent_core/egress.py` and `agent_core/policy.py` are **stdlib-only** — no
gspread, requests, fastapi or anything else — so copy those two plus
`egress.json` and `policy.json` into the CoS and they work as-is:

```python
from gate import egress
d = egress.decide_send("slack", ["caleb@getblood.com"], body=msg)
# d.outcome == "auto"    -> send it
d = egress.decide_send("email", ["rfcreditcontrolsgh@hsbc.com.sg"], body=msg)
# d.outcome == "approve" -> d.ask holds the full prompt, body included
```

Worth doing rather than leaving it to judgement, because the gate catches things
a per-message decision won't: subdomain lookalikes (`getblood.com.evil.co` reads
as external), an unresolved recipient, an unrecognised channel, a Slack ID that
isn't allow-listed, and one external address hidden in a cc list of internals.
All fail closed. Edit `egress.json` when a domain genuinely becomes yours — never
to make approvals less frequent.

Check `internal_domains` covers every tenancy you actually own, and put anything
that looks internal but is read by an outside party (a mailbox an agency or
auditor has access to) into `external_overrides`.

---

## 5. Add the reversibility column to the task register

The CoS said this was worth building regardless, and it's right: it's what makes
routing mechanical instead of a judgement call per item. On every row, whether
the next action is:

- **inside** — a draft, a note, a lookup, an internal message → the agent does it
- **outside** — money moves, something external is sent, a filing goes out → you

Once that column exists, "what needs Danny" is a filter rather than a daily
reading exercise, and the runner in step 3 knows what it's allowed to clear.

One thing to fix while you're in the schema: 20 open items, 19 Blood, **1
Equilibro**. Across eight F&B entities that isn't a quiet month — that work is
living in your head, and none of the routing above reaches it until it's visible.
The CoS flagged the same thing itself on 29 Jul.
