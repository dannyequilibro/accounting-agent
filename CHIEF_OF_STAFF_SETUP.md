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

It isn't a missing connector. **Claude Code's MCP servers default to *local*
scope, which means "this project directory only."** They're stored in
`~/.claude.json` keyed by the project path, so a server added while working in
one folder is invisible from another.

That matches the symptom exactly. The 8am brief "reads email and calendar" and
the Slack/Telegram front end says "I don't have email or calendar access in this
chat" — same machine, same account, different working directory. Nothing is
broken and nothing needs re-authorising; the config just isn't in scope where the
chat runtime runs.

The fix is the `--scope user` flag, which loads the server in **all** your
projects:

```bash
claude mcp add --transport http gmail    <URL> --scope user
claude mcp add --transport http gcal     <URL> --scope user
claude mcp add --transport http gdrive   <URL> --scope user
```

Then in a Claude Code session, `/mcp` → authenticate each one (HTTP transport
carries the OAuth flow).

For `<URL>`: take them from the connector's listing at
<https://claude.ai/directory>. Per the Claude Code MCP docs, "Directory
connectors use the same MCP infrastructure as Claude Code, so you can add any
remote server listed there with `claude mcp add`" — and you already have Gmail,
Google Calendar and Google Drive connected on claude.ai, so these are the same
integrations you're authenticated against rather than anything new to approve.

Two checks worth doing while you're in there:

- `claude mcp list` from the chat runtime's working directory. Anything that
  shows up under the brief runner's directory but not here is a local-scope
  server that wants moving to user scope.
- Confirm both runtimes run as the same OS user. `~/.claude.json` is per-user, so
  a service or scheduled task running as a different account has its own file and
  will keep disagreeing with your interactive sessions no matter what scope you
  use.

Google also publishes its own remote MCP servers for Workspace as an alternative
path — I saw Calendar's given as `https://calendarmcp.googleapis.com/mcp/v1`, but
that came from a search result and their docs returned 403 from here, so verify
it rather than pasting it on my word. The Directory route needs no Cloud project
and is the one I'd take.

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
