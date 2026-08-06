// An accessible on/off toggle built on a native checkbox, so it's keyboard
// operable (Tab to focus, Space to toggle) and announced correctly by
// screen readers without any ARIA gymnastics. The switch visual is layered
// on top of a visually-hidden but focusable input.

"use client";

interface ToggleProps {
  id: string;
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

export default function Toggle({
  id,
  label,
  description,
  checked,
  onChange,
  disabled = false,
}: ToggleProps) {
  const descId = description ? `${id}-desc` : undefined;
  return (
    <label
      htmlFor={id}
      className={`flex items-start gap-3 ${
        disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"
      }`}
    >
      <span className="relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center">
        <input
          id={id}
          type="checkbox"
          role="switch"
          aria-checked={checked}
          aria-describedby={descId}
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="peer absolute h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-full bg-slate-700 transition-colors peer-checked:bg-emerald-500/80 peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-400 peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-[#0b0d12]"
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-0.5 h-4 w-4 rounded-full bg-slate-200 shadow transition-transform peer-checked:translate-x-4"
        />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-slate-200">
          {label}
        </span>
        {description && (
          <span id={descId} className="block text-xs text-slate-500">
            {description}
          </span>
        )}
      </span>
    </label>
  );
}
