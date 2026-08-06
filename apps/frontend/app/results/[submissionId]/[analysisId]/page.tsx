// Route: /results/[submissionId]/[analysisId]
// The shareable result page. Thin server component that reads the route
// params and hands them to the client component that does the fetching.

import SharedResult from "@/components/results/SharedResult";

export default function ResultsPage({
  params,
}: {
  params: { submissionId: string; analysisId: string };
}) {
  return (
    <main className="mx-auto max-w-6xl px-6 py-10 sm:py-14">
      <SharedResult
        submissionId={params.submissionId}
        analysisId={params.analysisId}
      />
    </main>
  );
}
