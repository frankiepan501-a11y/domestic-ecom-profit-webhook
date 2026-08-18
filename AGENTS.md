# Repository agent guidance

## Agent skills

### Issue tracker

Issues and specs are tracked in GitHub Issues for this repository. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five Matt Pocock triage roles. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read root `CONTEXT.md` when present and relevant ADRs under `docs/adr/`. See `docs/agents/domain.md`.

## Project safeguards

- Production Feishu cards must be tested in Frankie-only mode before they are routed to operators, finance, or procurement.
- Preserve full gap context: platform, shop, object type, business reason, responsibility, evidence, and next action.
- Do not treat an order id, platform product id, or merchant-code gap as an ERP SKU procurement-cost gap.
- Finance-facing product names must come from the ERP Chinese product name. Keep marketplace titles only in order/source details, and aggregate product profit by platform + shop + confirmed ERP SKU rather than by title.
