import { useState, type FormEvent } from "react";
import { ApiError, submitComplaint, type Complaint } from "../api/client";

type FieldKey = "text" | "location" | "reporter_contact";

interface FormValues {
  text: string;
  location: string;
  reporter_contact: string;
}

const INITIAL_VALUES: FormValues = { text: "", location: "", reporter_contact: "" };

// Mirrors the backend's ComplaintCreate constraints exactly (see backend/app/schemas.py) so the
// user gets instant feedback instead of waiting on a round-trip for the obvious cases.
function validateText(value: string): string | null {
  const length = value.trim().length;
  if (length < 10) return "Must be at least 10 characters.";
  if (length > 2000) return "Must be at most 2000 characters.";
  return null;
}

function validateLocation(value: string): string | null {
  const length = value.trim().length;
  if (length < 3) return "Must be at least 3 characters.";
  if (length > 200) return "Must be at most 200 characters.";
  return null;
}

function validateReporterContact(value: string): string | null {
  if (value.trim().length > 200) return "Must be at most 200 characters.";
  return null;
}

function clientErrorsFor(values: FormValues): Partial<Record<FieldKey, string>> {
  const errors: Partial<Record<FieldKey, string>> = {};
  const textError = validateText(values.text);
  const locationError = validateLocation(values.location);
  const contactError = validateReporterContact(values.reporter_contact);
  if (textError) errors.text = textError;
  if (locationError) errors.location = locationError;
  if (contactError) errors.reporter_contact = contactError;
  return errors;
}

function isKnownField(field: string): field is FieldKey {
  return field === "text" || field === "location" || field === "reporter_contact";
}

function SubmitPage() {
  const [values, setValues] = useState<FormValues>(INITIAL_VALUES);
  const [touched, setTouched] = useState<Partial<Record<FieldKey, boolean>>>({});
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverFieldErrors, setServerFieldErrors] = useState<Partial<Record<FieldKey, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<Complaint | null>(null);

  const clientErrors = clientErrorsFor(values);
  const isValid = Object.keys(clientErrors).length === 0;

  function fieldError(field: FieldKey): string | null {
    if (serverFieldErrors[field]) return serverFieldErrors[field] as string;
    if ((touched[field] || submitAttempted) && clientErrors[field]) return clientErrors[field] as string;
    return null;
  }

  function updateField(field: FieldKey, value: string) {
    setValues((prev) => ({ ...prev, [field]: value }));
    setServerFieldErrors((prev) => {
      if (!prev[field]) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }

  function handleBlur(field: FieldKey) {
    setTouched((prev) => ({ ...prev, [field]: true }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitAttempted(true);
    setFormError(null);

    if (!isValid || submitting) return;

    setSubmitting(true);
    try {
      const complaint = await submitComplaint({
        text: values.text.trim(),
        location: values.location.trim(),
        reporter_contact: values.reporter_contact.trim() || undefined,
      });
      setResult(complaint);
      setValues(INITIAL_VALUES);
      setTouched({});
      setSubmitAttempted(false);
      setServerFieldErrors({});
    } catch (error: unknown) {
      if (error instanceof ApiError && error.kind === "validation") {
        const mapped: Partial<Record<FieldKey, string>> = {};
        for (const fieldErr of error.fieldErrors ?? []) {
          if (isKnownField(fieldErr.field)) {
            mapped[fieldErr.field] = fieldErr.message;
          }
        }
        setServerFieldErrors(mapped);
      } else if (error instanceof ApiError && error.kind === "rate_limited") {
        const retrySuffix =
          typeof error.retryAfterSeconds === "number"
            ? ` Try again in ${error.retryAfterSeconds} seconds.`
            : "";
        setFormError(`${error.message}${retrySuffix}`);
      } else {
        setFormError("Something went wrong while submitting. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="submit-page">
      <h1>Report a problem</h1>
      <p>Tell us what&apos;s wrong and where — we&apos;ll route it to the right team.</p>

      <form className="submit-form" onSubmit={handleSubmit} noValidate>
        <div className="field">
          <label htmlFor="submit-text">Description</label>
          <textarea
            id="submit-text"
            name="text"
            rows={5}
            value={values.text}
            disabled={submitting}
            aria-invalid={fieldError("text") ? true : undefined}
            aria-describedby={fieldError("text") ? "submit-text-error" : undefined}
            onChange={(event) => updateField("text", event.target.value)}
            onBlur={() => handleBlur("text")}
          />
          {fieldError("text") && (
            <p className="field-error" id="submit-text-error" role="alert">
              {fieldError("text")}
            </p>
          )}
        </div>

        <div className="field">
          <label htmlFor="submit-location">Location</label>
          <input
            id="submit-location"
            name="location"
            type="text"
            value={values.location}
            disabled={submitting}
            aria-invalid={fieldError("location") ? true : undefined}
            aria-describedby={fieldError("location") ? "submit-location-error" : undefined}
            onChange={(event) => updateField("location", event.target.value)}
            onBlur={() => handleBlur("location")}
          />
          {fieldError("location") && (
            <p className="field-error" id="submit-location-error" role="alert">
              {fieldError("location")}
            </p>
          )}
        </div>

        <div className="field">
          <label htmlFor="submit-contact">Contact (optional)</label>
          <input
            id="submit-contact"
            name="reporter_contact"
            type="text"
            value={values.reporter_contact}
            disabled={submitting}
            aria-invalid={fieldError("reporter_contact") ? true : undefined}
            aria-describedby={fieldError("reporter_contact") ? "submit-contact-error" : undefined}
            onChange={(event) => updateField("reporter_contact", event.target.value)}
            onBlur={() => handleBlur("reporter_contact")}
          />
          {fieldError("reporter_contact") && (
            <p className="field-error" id="submit-contact-error" role="alert">
              {fieldError("reporter_contact")}
            </p>
          )}
        </div>

        {formError && (
          <p className="form-error" role="alert">
            {formError}
          </p>
        )}

        <button type="submit" disabled={submitting || !isValid}>
          {submitting ? "Submitting…" : "Submit"}
        </button>
      </form>

      {result && (
        <section className="submit-result" aria-live="polite">
          <h2>Complaint submitted</h2>
          <dl>
            <dt>Category</dt>
            <dd>{result.category}</dd>
            <dt>Priority</dt>
            <dd>{result.priority}</dd>
            <dt>AI summary</dt>
            <dd>{result.ai_summary ?? "No summary available."}</dd>
          </dl>
          <span className="provider-badge">triaged by: {result.triaged_by}</span>
        </section>
      )}
    </main>
  );
}

export default SubmitPage;
