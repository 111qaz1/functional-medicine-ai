import { CaseWorkbenchLocal } from "../../../components/case-workbench-local";

export default async function CaseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  return (
    <main className="shell">
      <CaseWorkbenchLocal caseId={id} />
    </main>
  );
}
