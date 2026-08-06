import HealthStatus from "@/components/HealthStatus";
import SubmissionForm from "@/components/submission/SubmissionForm";

export default function Home() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-10 sm:py-14">
      <header className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-slate-500">
            $ ai-code-breaker
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-50">
            AI Code Breaker
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-400">
            Paste a candidate implementation and a reference implementation of
            the same function. AI Code Breaker runs both on a range of inputs,
            finds the first input where they disagree, and — optionally — asks
            Claude to explain why. Every result comes from real execution, not
            a guess.
          </p>
        </div>
        <HealthStatus />
      </header>

      <SubmissionForm />
    </main>
  );
}
