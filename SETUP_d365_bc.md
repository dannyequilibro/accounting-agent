# D365 Business Central — read-only access setup

Purpose: diagnose the SG entity's fixed-asset setup so the Thailand entity can be
configured correctly from day one. Everything in `bc_client.py` is a GET — nothing
here writes to BC.

## 1. Configuration

Add to `.env` locally (it is gitignored) — do not paste the secret into chat:

```
BC_TENANT_ID=<Entra tenant GUID>
BC_CLIENT_ID=<app registration client ID>
BC_CLIENT_SECRET=<client secret value>
BC_ENVIRONMENT=<sandbox environment name, exactly as it appears in the BC admin centre>
```

`BC_ENVIRONMENT` is the environment *name*, not "Sandbox" as a category — if the
admin centre shows it as `SANDBOX-01`, use that.

## 2. Confirm the app is authorized inside BC

The Entra app registration alone is not enough. In each company, on the
**Microsoft Entra Applications** page:

- Client ID matches the app registration
- A permission set is assigned (read-only is sufficient for this work)
- **State = Enabled**

## 3. Run the probe

```
python bc_client.py probe
```

It walks four steps and tells you where it stops:

1. Acquire a client-credentials token
2. List companies — the real test of BC-side authorization
3. Read the chart of accounts for the first company
4. Enumerate published OData web services and flag anything fixed-asset related

Other commands:

```
python bc_client.py companies
python bc_client.py accounts "Company Name"
python bc_client.py odata
python bc_client.py fa "Company Name"
```

## 4. Getting at fixed-asset data

Fixed assets are **not exposed by the BC standard API v2.0**. There are no FA
entities, and the FA-specific fields on journal lines (`FA Posting Type`,
`Depreciation Book Code`) are absent from the standard `journalLines` entity.

Two routes, neither needing a developer:

**Option A — publish the FA pages as web services.** In BC, open the **Web
Services** page and publish the pages covering: Fixed Asset, FA Classes, FA
Subclasses, FA Posting Groups, Depreciation Books, and FA Depreciation Books.
Search by name rather than page ID. Once published, `bc_client.py fa` reads them.

**Option B — export a RapidStart configuration package.** Create a configuration
package in BC containing the FA setup tables and export it. This is the better
route for a one-off diagnosis: it captures the complete setup in one file, needs
no web service plumbing, and the same package format is how the corrected
Thailand setup gets imported later.

Option B is recommended for the SG diagnosis. Option A is worth doing anyway if
ongoing FA reporting is wanted.

## 5. Why two depreciation books matter for Thailand

The most common fixed-asset misconfiguration is running a single depreciation
book. Thailand needs two:

- a **book** depreciation book for TFRS reporting, on actual useful life
- a **tax** depreciation book for Revenue Code rates, which cap depreciation at
  statutory maxima and diverge from book life

Retrofitting a tax book onto assets that have already depreciated under a single
book is painful — which is the likely root of the SG problem. Confirm the current
statutory rates and the passenger-vehicle cost cap with a Thai tax advisor before
they are committed to master data.

## Not set up yet

No write access, no posting, and no AL extension. Those only become relevant if
ongoing automation (e.g. posting FA acquisitions from invoices) is wanted later,
and posting FA journals would require a custom API page in an AL extension.
