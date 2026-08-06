// A single field-level error message, wired for accessibility: it carries
// an id so the associated control can point at it via aria-describedby, and
// role="alert" so screen readers announce it when it appears.

export default function FieldError({
  id,
  message,
}: {
  id: string;
  message?: string;
}) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="mt-1.5 text-xs text-rose-400">
      {message}
    </p>
  );
}
