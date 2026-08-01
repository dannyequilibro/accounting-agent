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

**The chat surface isn't Claude Code.** That's the answer, and it invalidates two
earlier versions of this section — including one that told you to run
`claude mcp add`, which would have done nothing.

From the CoS itself, 1 Aug, reading its own charter file:

> the Telegram surface (`cos.mjs`) is logged as using "the accounting agent's
> `ANTHROPIC_API_KEY`, model claude-sonnet-5." That's API-key auth, not a
> claude.ai subscription login.

and:

> my tool list doesn't look like MCP-connector shaped tools … it looks like a
> fixed custom toolset someone wired directly (`run_bc_query`,
> `accounting_agent_status`, etc.)

So: `cos.mjs` (Telegram) and `slack-cos.mjs` (Slack) are Node programs on a shared
`core.mjs`, calling the Anthropic API directly with an API key, exposing **eleven
hand-wired tools**: `read_file`, `list_dir`, `search_sessions`, `run_bc_query`,
`accounting_agent_status`, `read_slack`, `search_whatsapp`, `list_tasks`,
`add_task`, `complete_task`, `save_note`. No shell. No MCP client. No connectors.

That's why nothing about connectors or scopes was ever going to help: **there is no
MCP layer in the chat surface to configure.** The tools it has, it has because
somebody wrote them. Gmail, Calendar and Drive are absent because nobody has
written them yet.

Note Drive is missing from *both* surfaces — even the brief runner. It reads the
OneDrive Cowork folder through `read_file`; it has never been able to open a Drive
link, which is exactly what happened on 29 Jul.

### The fix: three options, cheapest first

**A. Write the three tools, same as the other eleven.** Most consistent with what
exists, no architectural change. `core.mjs` already has a tool-dispatch pattern —
add `search_gmail`, `read_calendar`, `search_drive` next to `run_bc_query`, each
calling the Google APIs with a service-account or OAuth credential. The brief
runner already reads Gmail and Calendar somehow, so credentials likely exist on
that machine already; reuse them rather than minting new ones. Half a day, and it
leaves the architecture alone.

**B. Give `core.mjs` an MCP client.** More work up front, but then every future
tool is a config line instead of a code change, and the ecosystem's servers become
available. Worth it only if you expect to keep adding tools.

**C. Replace the bespoke loop with the Claude Agent SDK or Claude Code headless.**
You get MCP, connectors, permission modes and session handling for free, and stop
maintaining a tool loop by hand. Biggest change, best end state, and it makes the
scheduled runner in §3 trivial rather than a second thing to build. If the CoS is
going to keep growing — and it is, it's already been cloned to Peck and Caleb —
this is the one I'd pick.

Whichever you choose, **the tool it most needs isn't Gmail — it's write access.**
Today it can only write `tasks.csv` and `inbox.md`. It can't edit the anchor files,
which is why every durable fact becomes "I've queued a note for the next full
session." See §3.

### One thing to fix regardless

`cos.mjs` uses **the accounting agent's `ANTHROPIC_API_KEY`.** Two independent
systems sharing one credential means rotating it for either breaks the other, and
a leak from either exposes both. Give the CoS its own key.

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

Repeatedly in that channel the answer was "I've queued a note for the next full
session." That session is you opening a laptop, which makes `inbox.md` a backlog
whose only worker is the person it's meant to unburden.

A caveat the CoS raised itself, and it's right: `inbox.md` is currently **empty**,
and that isn't zero backlog. It's the narrow "chat asked for something outside
chat's tools" funnel. The real queue is `tasks.csv` — 41 open rows, ~25 of them
reversible and internal. So the runner below matters less for draining the inbox
than for **reconciling the register**, which is the second half of the prompt.

Put it on a timer. On Windows, Task Scheduler running Claude Code headless (this
is separate from `cos.mjs` and needs no changes to it):

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
- **The right metric becomes measurable.** Count open `tasks.csv` rows whose next
  action is reversible. If that grows while a runner is clearing what it can, the
  fleet is generating work rather than absorbing it — and you'll see it before it
  becomes a month of drift. (Not inbox depth. See the caveat above.)

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
