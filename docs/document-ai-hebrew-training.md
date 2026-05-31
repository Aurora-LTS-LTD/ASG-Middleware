# Hebrew Document AI Processor — Training Playbook (P2-03)

The default Google Document AI Expense Parser is multi-language but
biased toward Latin scripts. For Hebrew (RTL, no diacritics on
typewritten invoices, frequent ׳/״ punctuation) you'll get noticeably
higher field-extraction accuracy by training a custom processor on
Israeli sample documents.

This codebase is wired to consume a Hebrew-tuned processor — just plug
in its ID. The training itself is operator work in the GCP Console.

## Step 1 — Create a Custom Document Extractor

```bash
# In the GCP Console:
#   Document AI → Workbench → Create a new processor
#     Type: "Custom Document Extractor" (NOT "Expense Parser")
#     Name: "aurora-hebrew-invoice-v1"
#     Region: me-west1 (same as Aurora's prod)
```

## Step 2 — Define the schema

Mirror Aurora's `ExpenseParseResult` fields so the existing entity-mapping
in `_documentai_parse()` continues to work without changes:

| Entity name        | Type   | Notes                          |
| ------------------ | ------ | ------------------------------ |
| `supplier_name`    | text   | Hebrew or mixed Hebrew/English |
| `supplier_tax_id`  | text   | 9-digit Israeli ID             |
| `total_amount`     | money  | Currency-code field required   |
| `total_tax_amount` | money  | 17–18% VAT typically           |
| `receipt_date`     | date   |                                |

## Step 3 — Upload 30–50 sample Hebrew invoices

Ideal sample mix:
- 10× supplier invoices from common Israeli SMB vendors
  (קופיקס, אסם, חברת חשמל לישראל, etc.)
- 10× restaurant receipts (Hebrew + sometimes Arabic)
- 10× professional services (משרד עו"ד, רואה חשבון)
- 5–10× edge cases: handwritten amounts, faded thermal-paper, photos
  taken at an angle

Aurora's existing `receipts` table is a natural source — pick rows
where confidence_min < 0.8 (current Expense Parser struggled with them).

## Step 4 — Label, train, deploy

In Workbench:
1. Label each upload (Workbench's labeling UI is RTL-aware).
2. Click "Train new version" — costs ~$5–20 in compute.
3. Wait 30–60 min.
4. Deploy the trained version to a processor endpoint.

## Step 5 — Wire to Aurora

Note the processor ID from the deployed version page.

```bash
# Add to Secret Manager:
echo -n "projects/aurora-lts-prod/locations/me-west1/processors/<ID>" \
  | gcloud secrets create document-ai-hebrew-processor-id --data-file=-

# Cloud Run env binding (terraform/secrets.tf — add a new entry):
#   DOCUMENT_AI_HEBREW_PROCESSOR_ID  ← latest version of the secret
```

After redeploy, the Aurora pipeline accepts `language_hint="he"` and
routes those parses to the Hebrew processor. Watch the field_confidences
distribution in `services/receipts/confidence.py` — the Hebrew processor
should produce noticeably higher numbers on `supplier_name` for
Hebrew-only inputs.

## Re-training cadence

- Every ~3 months OR after collecting another 200 labeled samples,
  whichever comes first.
- Roll new versions with `_v2`, `_v3` suffixes. Old versions stay
  callable so we can A/B compare confidence.
