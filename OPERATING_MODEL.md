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

Meanwhile this repo has the *opposite* failure. The accounting agent posts bills
to a client's Xero as `AUTHORISED` — into their ledger, their AP aging, their
reports, voidable only in a way an auditor can see — on nothing but a
self-reported confidence score. And it stops dead, escalating to a spreadsheet,
when a vendor isn't in a mapping tab. That's a trivially reversible data gap.

**Both systems have the gate on the wrong thing.** The Chief of Staff asks
permission for the reversible and can't act on anything. The accounting agent
commits the irreversible unattended and blocks on the trivial. Neither is
calibrated to consequence, which is the only thing that should decide whether
something reaches you.

The load this is failing to absorb, for scale: 74 calendar items and ~209 hours
in July, including 30 recurring country business reviews (IN 12, TH 9, ID 5,
MY 4) and ~13 separate investor conversations; roughly 200 mail threads a
fortnight, nearly all machine-generated, with your personal address on the Zoho
Expense and Lattice approval paths; 20 open register items of which 19 are Blood
and exactly 1 is Equilibro — a split the CoS itself flagged as implausible,
meaning Equilibro work isn't reaching the register at all.

---

## 2. The rule: reversibility, not difficulty

Stop sorting work by how hard or important it is. Sort it by **what it costs to
be wrong**, and give every action exactly one of four dispositions.

| | Disposition | Test | What you see |
|---|---|---|---|
| **1** | **Act** | Undoable with no trace anyone outside would see | One line in a daily digest |
| **2** | **Stage** | Real work, but the committing step is still pending | A batch of drafts, reviewed in one pass, in the tool you'd use anyway |
| **3** | **Ask** | Committing is consequential, but the question is closed and the answer is already proposed | One tap on your phone |
| **4** | **Escalate** | The inputs can't be trusted, so there's nothing to propose | A queue you work deliberately |

Two properties make this actually remove load rather than relabel it.

**Tier 2 is where most work should land, and it costs you nothing new.** A Xero
draft bill *is* the review queue. Twenty drafts reviewed in Xero in one sitting
is not twenty interruptions. Never build a bespoke approval UI for something the
target system already stages.

**Tier 3 must teach.** An approval that doesn't write back a rule is an
interruption you'll get again next month. Every tap should narrow the class of
things that can ask you. In this repo, approving an unmapped vendor writes the
vendor→account mapping, so that vendor never asks twice. That's the mechanism by
which your involvement actually decays instead of plateauing.

The corollary, which is the hard part: **tier 1 has to be genuinely
unsupervised.** If the agent asks before opening a task, adding a note, or
running a lookup, you have not delegated anything.

---

## 3. What's now built here (the reference implementation)

`agent_core/` is deliberately free of invoice logic so the next worker reuses it.

- **`policy.py`** — a pure function from signals to disposition. Inputs:
  extraction confidence, handwritten, vendor mapped, totals consistent, amount
  converted to SGD, whether the client is new. Foreign currency is valued before
  thresholds apply, and a currency with no rate is never auto-posted.
- **`policy.json`** — the thresholds, with per-client overrides. Tuning trust is
  a config edit, not a code change. Defaults: auto-authorise ≤ S$500, stage as
  draft ≤ S$5,000, auto-map unmapped vendors ≤ S$150.
- **`approvals.py`** — the loop that was missing. Pending approvals live in an
  `Approvals` tab on the Run Log sheet, because Railway wipes its disk on every
  deploy and an approval that evaporates on redeploy drops work silently. Each
  one pushes a Telegram message with three buttons. Resolution is idempotent —
  the row is claimed before anything irreversible runs, so Telegram's retries
  can't post a bill twice.
- **`xero_client.authorise_bill()` / `void_bill()`** — the commit and the undo,
  reachable only from an explicit decision, never from a confidence score.
- **`test_policy.py`** — 20 cases. This is the file that decides what hits a
  client's ledger unattended; it's worth the tests even though nothing else here
  has any.

Behaviour change: `create_bill` no longer hardcodes `AUTHORISED`. Only tier 1
authorises. Tiers 2 and 3 create a **draft** — the contact, coding, line item and
PDF attachment are all done, the ledger is untouched, and the draft is deletable
without trace. `batch_process.py` runs the same gate, so a backfill can't become
a way to push 300 bills past it. The daily digest now leads with anything blocked
on you and flags approvals older than 24 hours.

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
new agent — it's tools and a schedule on the one you have.
   - Drive/Gmail/Calendar read *in the chat surface*, not only in a "full
     session". Most of its refusals were capability gaps, not judgment.
   - Write access to the anchor files, so `inbox.md` stops being a queue that
     only you can drain.
   - A **scheduled runner** that drains `inbox.md` on a timer. Today "the next
     full session" means you. Make it a cron job and the phrase stops appearing.
   - A standing rule replacing the "want me to…?" reflex: *act on anything
     reversible and report it; ask only about tier 3+.* Adding a task, updating a
     note, grouping a list, doing a lookup — just do it.

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

**4. Make the recurring finance processes tier 2 by default.** `hsbc-recon`,
`hz-cuisine-fs` and `base-bear-recon` are documented well enough to run
unattended up to the point of commitment. The recon should arrive built, tied,
with the three zero-checks either passing or naming the exact line that doesn't —
and stop before the submission email to HSBC. That email is irreversible and
external; the eleven hours of building it are not.

**5. Then the Equilibro register gap.** One open item across eight F&B entities
is not a quiet month, it's work living entirely in your head. Nothing above
routes correctly until it's visible.

---

## 5. Operating discipline

- **Move thresholds from evidence, weekly.** The Run Log tells you which
  disposition each invoice got and how it turned out. If a client has posted
  clean for a month, raise its `auto_authorise_max_sgd`. Every raise deletes a
  class of interruption permanently. Starting conservative is correct; *staying*
  conservative is how this quietly becomes another queue.
- **Watch two numbers only.** Approvals older than 24h (in the digest now), and
  `inbox.md` depth. Both measure work parked on you showing up. If either trends
  up, the fleet is generating queue rather than absorbing it.
- **Every tier 3 answer writes a rule.** If you tap the same kind of approval
  twice, that's a bug in the learning path, not a fact about the work.
- **Keep tier 4 small and honest.** Escalation is for untrustworthy inputs, not
  for things the agent could decide but is nervous about. Nervousness belongs in
  tier 2.
- **One brain, one register.** Blood and Equilibro on separate Claude accounts
  with an OneDrive-synced folder as the bridge is the sort of seam that loses
  work. Consolidate the register before adding workers to it.

---

## 6. On the Chief of Staff refusing this session

While writing this I messaged `#chief-of-staff` asking for its tool list, anchor
file map, `inbox.md` depth, and the open register. It declined, on the grounds
that a message claiming to be another Claude instance with relayed authority from
you is untrusted data, and asked you to confirm before handing over the register
or business specifics.

That was the right call and it's worth knowing it holds under pressure — that
channel is exactly where an injection would arrive. If you want the numbers in
section 4 grounded in its actual state rather than in what's visible from Slack
history, tell it directly in that channel that this session is real and what
you're willing to have it share.

It also said the reversibility field was worth building regardless of the answer.
Agreed. That field is the thing that routes work away from you: for every open
item, whether the next action is reversible (a draft, a note, a lookup — agent
does it) or irreversible (money moves, something external is sent, a filing goes
out — you do it). Add it to the register schema and most of section 4 becomes
mechanical.
