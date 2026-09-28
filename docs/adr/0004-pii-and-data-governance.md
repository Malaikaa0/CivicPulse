# ADR-0004: PII and data governance

- **Status:** Accepted
- **Owner:** M1 (backend / AI)

## Context
A citizen complaint is free text written in a hurry, and it routinely contains personal data:
phone numbers ("call me on 0300-..."), emails, national ID numbers, and sometimes names and
street addresses.

The hosted model we use for triage is Google Gemini on the free tier. Google's terms for the
free tier say inputs may be used to improve its models. That means anything we send may be
retained and may influence a model other people use. So what leaves our machine is a
data-governance decision, not an implementation detail.

Two facts shape the decision:
- Triage only needs to know *what is wrong and how urgent it is*. The words around a phone
  number ("burst pipe, call me on ...") carry the meaning; the number itself carries none.
- We can detect numbers and emails reliably with patterns. We cannot detect names or street
  addresses in free text reliably, and claiming to would be worse than admitting it.

## Decision
1. **Only the complaint text is ever sent to the hosted model.** `reporter_contact` and
   `location` are never sent. The provider interface still receives `location`, but the
   Gemini provider does not forward it.
2. **Before sending, identifiers are redacted.** Emails become `[EMAIL]`; runs of nine or more
   digits (phones, national ID numbers) become `[NUMBER]`. Shorter numbers are kept because
   they matter for triage ("Street 12", "2000 rupay", "three days"). See
   `backend/app/providers/triage/redaction.py`.
3. **The redacted text is what is cached and logged, never the original.** The content-hash
   cache key and any log line use the redacted form.
4. **Deployments that cannot accept the residual risk run without the hosted model.**
   `TRIAGE_PROVIDER=rules` never calls out at all. An offline Ollama provider would have kept
   model-quality triage on the machine, but it was evaluated and not built (issue #45), so
   `rules` is the only fully local option in this codebase.
5. **The API key comes from the environment only** (a Kubernetes Secret, GitHub Secrets, a
   gitignored `.env`) and is never logged or committed.

Options considered and rejected:
- *Accept the exposure and document it.* Rejected: the mitigation is cheap and the harm
  (a citizen's phone number in a third party's training data) is real and not undoable.
- *Send everything, rely on the provider's privacy terms.* Rejected for the same reason; a
  free tier's terms are a reason for caution, not for trust.
- *Try to redact names and addresses too.* Rejected: it cannot be done reliably with patterns,
  and a false sense of safety is a worse failure than a documented limit.

## Consequences
- **Good:** the most directly identifying and most reusable data (phone numbers, emails,
  national ID numbers) never leaves the machine. The behaviour is tested, including that
  redaction never alters the seed complaints or changes their triage result.
- **Residual risk, stated plainly:** a name or a street address typed inside the complaint
  text is still sent. A complaint saying "Bilal Ahmed at House 44 has no water" leaves with
  both. Redaction reduces the exposure; it does not remove it.
- **Cost:** a small loss of context (the model sees `[NUMBER]`), which does not affect
  classification. If a future need arises for names, that is a case for the offline provider,
  not for sending more.
- **Operational:** whoever deploys chooses the provider knowingly. The README and RUNBOOK
  should say which providers send data off the machine and which do not.
- **Revisit when:** the free tier's terms change, or complaints start to include categories
  of data we did not anticipate.
