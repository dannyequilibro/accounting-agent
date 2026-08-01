# Getting Danny out of the critical path

Written 1 Aug 2026, from what's actually running: this repo, the Chief of Staff
(Slack `#chief-of-staff` + Telegram + the 8am brief), the ID/MY claims validation
agent, and the `hsbc-recon` / `hz-cuisine-fs` / `base-bear-recon` skills.

---

## 1. The diagnosis

You have built a very good **advisor** and almost no **actuators**.

The Chief of Staff can find anything. It found the BIG Pharmacy TTA already in
the data room, byte-identical, and noticed in passing that the signed DC rate is
6% while an invoice billed 4%. That is real analytical work. But read what
happens at the end of every thread in that channel:

- **It asks permission for things it could not possibly break.** In one
  twenty-minute session on 29 Jul you answered roughly fifteen "want me to…?"
  questions — open a task, update a note, group the list by business, bump a
  priority. None of those are reversible-with-consequences; they're reversible,
  full stop. Every one cost you a context switch.
- **It can't reach your own data, so you become the courier.** "I don't have
  Google Drive access from this chat." "I don't actually have a way to receive or
  read file attachments." "I don't have email or calendar access in this chat."
  You sent it a contract and it couldn't open it. You linked a Drive folder and
  it couldn't look.
- **Its queue only drains when you sit down.** "I've queued a note for the next
  full session." That next full session is *you*, opening a laptop. `inbox.md` is
  a backlog whose only worker is the person it's meant to unburden.
- **It has no ground truth, so you do status reconciliation by hand.** "Anything
  you close out that you did there, I'm taking on trust." You spent a chunk of
  27–29 Jul telling your own task register what you'd already done.
- **You are still the send button.** "i will send this myself." "i'm sending it
  on slack." Every drafted message routes back through your hands.

Meanwhile this repo had the *opposite* failure — though not the one I first
claimed. The accounting agent stopped dead, escalating to a spreadsheet, when a
vendor wasn't in a mapping tab: a trivially reversible data gap treated as a
blocker. And it posted every bill straight to `AUTHORISED` off a self-reported
confidence score, which is worth fixing not because posting is irreversible (it
isn't — you void and repost) but because a wrong number silently becoming the
working figure is how a bad month's reporting happens.

**Both systems have the gate on the wrong thing.** The Chief of Staff asks
permission for things that stay inside the business and can't act on anything.
The accounting agent blocked on trivia. Neither had any gate at all on the thing
that actually can't be taken back: something reaching an outside party.

The load this is failing to absorb, for scale: 74 calendar items and ~209 hours
in July, including 30 recurring country business reviews (IN 12, TH 9, ID 5,
MY 4) and ~13 separate investor conversations; roughly 200 mail threads a
fortnight, nearly all machine-generated, with your personal address on the Zoho
Expense and Lattice approval paths; 20 open register items of which 19 are Blood
and exactly 1 is Equilibro — a split the CoS itself flagged as implausible,
meaning Equilibro work isn't reaching the register at all.

---

## 2. The rule: egress, not size

The first version of this document drew the line at "leaves a mark someone else
could see" and put a big Xero posting on the wrong side of it. Danny's
correction, and it's the right one: a bookkeeping entry can be voided and
reposted. Nobody outside saw it, nobody acted on it. **Internal bookkeeping is
not the irreversible thing.**

What's irreversible is **anything that leaves the business, and anything that
moves money.** Once the recon email lands at HSBC's RF team, once a claim goes
into the Watsons portal, once an investor update is sent, once a payment
instruction is released, once a filing goes to ACRA — it's out. You can send a
correction; you cannot unsend. A third party has read it and may already have
acted on it.

So the dispositions split by which side of the boundary the action sits on:

**Inside the business — the agent's, always. Nothing here interrupts you.**

| Disposition | When | What you see |
|---|---|---|
| **Act** | The agent trusts its read | One line in the daily digest |
| **Draft** | The agent's read of the *document* is shaky, or the amount is big enough that a wrong coding is annoying to unwind | A batch of Xero drafts, reviewed in one pass, in the tool you'd use anyway |
| **Escalate** | The document can't be read at all | A queue you work deliberately |

**Crossing the boundary — yours, always.**

| Disposition | When | What you see |
|---|---|---|
| **Approve** | Any external recipient, any money movement, any filing, any published thing | One tap, with the exact content in front of you |

Three properties make this remove load rather than relabel it.

**Draft is where most internal work should land, and it costs you nothing new.**
A Xero draft *is* the review queue. Twenty drafts reviewed in Xero in one sitting
is not twenty interruptions. Never build a bespoke approval UI for something the
target system already stages.

**The boundary gate is not tunable by track record.** A hundred clean sends do
not earn the hundred-and-first. The failure mode isn't random — it's the one
unusual message that matters, which is exactly the case a track record says
nothing about. There is no threshold, no confidence score, and no per-client
trust setting that gets past an external recipient. That's deliberate, and it's
the one place in the system where "the agent has been good lately" is not an
argument.

**Act has to be genuinely unsupervised.** If the agent asks before opening a
task, adding a note, running a lookup, or drafting a bill, you have not delegated
anything. This is the failure the Chief of Staff currently has.

The useful consequence of drawing the line here: **internal messages can just
go.** The CoS drafting a Slack message to Caleb or Peck is inside the boundary —
it should send, not wait for you to be the send button. The HSBC email is
outside. Same drafting work, opposite disposition, and the difference is
mechanical rather than a judgement call each time.

---

## 3. What's now built here (the reference implementation)

`agent_core/` is deliberately free of invoice logic so the next worker reuses it.

- **`egress.py` + `egress.json`** — the boundary gate, and the part that matters.
  Classifies a proposed send by channel and recipient. Every recipient internal →
  the agent sends and logs it. Any external recipient → approval, with the full
  body in the prompt (approving a message you can't see isn't approval). Payment
  instructions, statutory filings, portal submissions, contract execution and
  public posts approve regardless of recipient, because the channel *is* the
  commitment. Fails closed everywhere: an unparseable recipient, an unknown
  channel, a subdomain lookalike (`getblood.com.evil.co`), or a Slack ID that
  isn't allow-listed all read as external. A `sensitive_terms` list holds things
  like term-sheet and valuation traffic even when recipients are internal —
  that's not recoverable in the wrong internal channel either.
- **`policy.py` + `policy.json`** — decision rights for internal bookkeeping.
  Pure function from signals (confidence, handwritten, vendor mapped, totals
  consistent, amount in SGD, new client) to disposition. **No internal amount
  produces an approval** — that's asserted in the tests. Foreign currency is
  valued before thresholds apply and an unpriceable currency is never
  auto-posted. Per-client overrides, so tuning trust is a config edit.
- **`approvals.py`** — the loop that was missing. Pending approvals live in an
  `Approvals` tab on the Run Log sheet, because Railway wipes its disk on every
  deploy and an approval that evaporates on redeploy drops work silently.
  Resolution is idempotent — the row is claimed before anything irreversible
  runs, so Telegram's retries can't send twice. Button labels are per-kind:
  "Approve & post" on a bill, "Send it" on an email, because the wrong verb at
  the moment of tapping is the whole risk.
- **`xero_client.authorise_bill()` / `void_bill()`** — commit and undo, reachable
  only from an explicit decision.
- **`test_policy.py`** — 44 cases across both gates.

Behaviour change from the correction: **the invoice pipeline no longer asks you
anything.** It's act / draft / escalate only. `auto_authorise_max_sgd` went from
S$500 to S$2,000 (HZ Cuisine to S$5,000), and above it the bill is still posted —
just as a draft. The unmapped-vendor case became a draft rather than a tap, with
the mapping carried along to be written when the draft is authorised;
`ask_to_learn_mappings` turns the tap back on per client if you'd rather clear
mappings from your phone. `draft_max_sgd` is dead — size can't make an internal
posting less reversible — and is kept in the file only so an old deploy doesn't
`KeyError`.

`create_bill` no longer hardcodes `AUTHORISED`. `batch_process.py` runs the same
gate, so a backfill can't push 300 bills past it. The digest leads with anything
blocked on you and flags approvals over 24 hours.

**Deploy:** set `TELEGRAM_WEBHOOK_SECRET` on Railway, then once per URL run
`python -m agent_core.approvals register-webhook https://<app>`. Without that
secret the callback endpoint refuses everything, which is the intended failure
mode — an unauthenticated endpoint that authorises bills is worse than a broken
one. Also worth fixing while you're in there: `WEBHOOK_SECRET` is still the
placeholder `change_this_to_a_random_string` in both Railway and `Code.gs`.

---

## 4. Build order

Ordered by load removed per unit of work, not by novelty.

**1. Give the Chief of Staff hands.** This is the whole ballgame and it isn't a
new agent — it's tools and a schedule on the one you have. Written up with the
exact commands in **[CHIEF_OF_STAFF_SETUP.md](CHIEF_OF_STAFF_SETUP.md)**; the
cause of the "no email access in this chat" split is how the chat runtime
authenticates, not a missing connector — claude.ai connectors only load under a
subscription login, never under an API key or a `setup-token`.
   - Drive/Gmail/Calendar read *in the chat surface*, not only in a "full
     session". Most of its refusals were capability gaps, not judgment.
   - Write access to the anchor files, so `inbox.md` stops being a queue that
     only you can drain.
   - A **scheduled runner** that drains `inbox.md` on a timer. Today "the next
     full session" means you. Make it a cron job and the phrase stops appearing.
   - A standing rule replacing the "want me to…?" reflex, phrased on the corrected
     axis: *if it stays inside the business, do it and report it; if it crosses
     the boundary, ask.* Adding a task, updating a note, grouping a list, doing a
     lookup, drafting a doc, messaging the team — all inside. Only sends to
     outside parties, money, and filings come back.
   - Give it the same `egress.py` gate so "inside vs outside" is one function call
     rather than a judgement it re-litigates every time — and so *it* can send
     internal Slack without you, which is what stops you being the send button.

**2. Close the status loop.** The 8am brief already reads email and calendar. Let
it close register items from evidence instead of asking you. It offered you
exactly this on 27 Jul and you never took it up. That alone deletes the "I
already did that" conversations.

**3. Put the noise behind a triage worker.** ~200 threads a fortnight of SPX
pickup receipts, DHL invoices, shop-health reports, bank notifications and
Watsons portal tasks. Almost all of it is tier 1 (file it, extract it, ignore it)
and a thin slice is tier 3 (the declined Google Workspace payment on 1 Aug is a
real service interruption; a Zoho Expense report going overdue on 3 Aug is a
decision only you can make). Route the slice, delete the rest from your view.
Note the counter-example already in the data: your finance/ops aliases are
*subscribed* to those feeds, so this is a routing problem, not a filtering one.

**4. Run the recurring finance processes up to the boundary.** `hsbc-recon`,
`hz-cuisine-fs` and `base-bear-recon` are documented well enough to run
unattended right up to the point of egress. The recon should arrive built, tied,
with the three zero-checks either passing or naming the exact line that doesn't —
and stop at the submission email to HSBC. That email is the only irreversible
step in the whole process; the building of it isn't, and neither is the working
file. Same for HZ Cuisine: the FS can be produced, cross-checked and circulated
internally on its own; the filing is yours.

This is where the split earns its keep. Under the old framing you'd have gated
the recon on materiality and reviewed the whole thing. Under this one, the ask is
one tap on a submission email whose attachments the agent has already tied to
zero — and the eleven hours before it never touch your calendar.

**5. Then the Equilibro register gap.** Nine open items across eight F&B entities,
against thirty for Blood. That ratio is work living in your head rather than a
quiet quarter. Nothing above routes correctly until it's visible.

---

## 4a. The rows AI cannot help with: sole-admin seats

A slice of the register isn't a delegation problem at all, and counting it as
automatable would flatter the plan. The Chief of Staff's phrasing on T036 is the
tell: *"blocked entirely on Danny being the only admin, same shape as T015/T053."*

These rows are blocked on a **seat**, not a task:

| Row | What it is | The actual blocker |
|---|---|---|
| T036 | Grant Ai Choo her own CorpPass access (Equilibro) | Danny is the only CorpPass admin |
| T015 | Fix the Zoho Expense merchant list | Admin-level config only he holds |
| T053 | Same class per the CoS (Blood) | — |

*(Full cut requested from the register; this is the confirmed subset.)*

Two things follow, and they're different in kind from everything else here.

**A seat delegated once retires a whole row class.** T036 isn't "Danny does a
task." It's "a second CorpPass admin exists," after which every future access
request for that system stops reaching him — including the ones not yet written
down. The return isn't one closed row, it's the arrival rate of that row shape
going to roughly zero. That makes these the highest-leverage items on the whole
register and they're all sitting at Normal priority.

**Sole-admin is a continuity risk regardless of any open row.** Any system where
he is the only holder fails the moment he's on a plane, ill, or asleep in the wrong
timezone — and given what these systems are (CorpPass filings, Zoho payment
approvals, bank portals), some of them fail expensively. That's worth auditing on
its own, separately from the register: for every system touching money, filings, or
access provisioning, who else can act? Anywhere the answer is "nobody" is a
single point of failure that no AI layer addresses.

The rule of thumb for sorting the register once the reversibility column exists:

- **Reversible + internal** → the agent does it
- **Crosses the boundary** → one tap from you
- **Blocked on a seat you alone hold** → delegate the seat, once, and the row
  class disappears
- **Needs your judgement** → this is the residue, and it's the only part that
  should genuinely stay on your plate

---

## 5. Operating discipline

- **Move the internal thresholds from evidence, weekly.** The Run Log tells you
  which disposition each invoice got and how it turned out. If a client has posted
  clean for a month, raise its `auto_authorise_max_sgd`. That only changes how
  much lands in the draft pile — it can't change what reaches you, so it's a safe
  dial to be aggressive with.
- **Never move the boundary list to reduce interruptions.** `egress.json` changes
  when a domain genuinely becomes yours, not when the approvals feel frequent. If
  external approvals are noisy, the fix is fewer outbound messages or batching
  them, not reclassifying the recipient.
- **Watch two numbers.** Approvals older than 24h (in the digest now), and open
  rows in `tasks.csv` where the next action is reversible. *Not* `inbox.md` depth
  — I'd proposed that and the Chief of Staff corrected it on 1 Aug: the inbox sat
  at zero while the register held 41 open items, because the inbox is only the
  narrow "chat asked for something outside chat's tools" funnel. Zero there means
  nothing was typed into Telegram, not that nothing is parked on you.
- **Internal drafts should teach.** If the same vendor drafts twice, the mapping
  write-back is broken. If you tap the same external approval shape every month
  (the HSBC submission), that one is *correct* — it should recur forever.
- **Keep escalation small and honest.** It's for untrustworthy inputs, not for
  things the agent could decide but is nervous about. Nervousness belongs in a
  draft.
- **One brain, one register.** Blood and Equilibro on separate Claude accounts
  with an OneDrive-synced folder as the bridge is the sort of seam that loses
  work. Consolidate the register before adding workers to it.

---

## 6. What the Chief of Staff reported (1 Aug, confirmed)

It initially declined my questions — a message claiming to be another Claude with
relayed authority is untrusted data — and answered once Danny confirmed in the
channel. That refusal holding is worth more than the answers were; that channel is
exactly where an injection would arrive.

Its own account of itself, replacing my guesses:

**Tools.** Chat surface: read-only except two narrow writes. Reads
`run_bc_query` (live D365/BC), `accounting_agent_status` (this repo's run log, not
live Xero), `read_slack`, `search_whatsapp`, `search_sessions`, `read_file`,
`list_dir`. Writes only `add_task`/`complete_task` → `tasks.csv`, and `save_note`
→ `inbox.md`. **No email, calendar, Drive, browsing, or file editing from chat.**
The daily brief runner has all of that plus live Gmail and Calendar reads, and is
the only thing that can edit the anchor files. Neither surface can move money,
send externally, or delete — a charter rule, not a gap.

Note what this rules out: the chat surface has plenty of *custom* MCP tools, so
MCP isn't broken there. It's specifically the claude.ai connectors that are
missing, which is the signature of an auth method that can't load them. See
[CHIEF_OF_STAFF_SETUP.md](CHIEF_OF_STAFF_SETUP.md) §1.

**Anchor files.** OneDrive "Cowork" root: `CLAUDE.md` (shared across both Claude
accounts — that folder is the bridge), `knowledge-base/` (16 files incl.
`blood.md`, `equillibro.md`, `fundraise.md`, `budget.md`,
`hsbc-trade-finance.md`, `trade-spend-dn.md`, `audit-fs-consolidation.md`),
`chief-of-staff/` (`tasks.csv`, `inbox.md`, `equilibro-leads.md`). Peck's and
Caleb's instances are separate builds off a template, not shared state.

**`tasks.csv` schema.** `id, task, business, category, priority, source, added,
due, status, done_date, notes`. No reversibility field — it agreed to add one,
values reversible/irreversible.

**41 open rows** (30 Blood, 9 Equilibro, 0 Personal), up from 20 on 29 Jul. It
classified all 41 by hand. Roughly 25 of the Blood rows are reversible and
internal, and its own phrase for why they're stuck is the finding: *"blocked
mainly on Danny's own time/config authority."* Only four rows are genuinely
waiting on someone else (T035 HSBC/Vera, T040 Ellis, T047 Hasan's team, T055
Caleb/Dennis).

**It corrected my metric.** I'd proposed `inbox.md` depth as the measure of work
parked on Danny. Depth is zero, and it said so plainly: that's not zero backlog,
it's "nothing has been typed into Telegram/Slack asking for a full-session action"
— the real bottleneck sits in `tasks.csv`. Section 5 now watches the register
instead.

**Where it thinks it's over-gated**, unprompted: routine internal comms that
inform and commit to nothing — nudging a colleague who already owes a document,
posting a status update to a channel it's already in, writing down a fact everyone
should know. Its proposal: *"let a layer draft and auto-send low-stakes internal
nudges (not first-time channel posts, not anything external, not anything with a
number attached), and keep the hard stop exactly where it is today for
money/legal/deletion."*

That is the egress rule, arrived at independently, with two extra conditions worth
adopting into `egress.json`: **first-time posts to a channel** and **anything with
a figure in it** stay gated even when internal. It also has evidence for keeping
the hard gates — the T032 dead-link bug and a WhatsApp duplicate-listener incident
both happened because a check was skipped, not because a gate was too strict.

One place I'd disagree with it: it nominates T057 (send Swap the signed NDA) as
the best "should this really need Danny" candidate. An executed legal document
going to a counterparty is exactly what the boundary is for. The fix isn't to open
that gate, it's that the agent should have the email staged with the attachment
so approving it is one tap — the preparation is the work, not the send.
