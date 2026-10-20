"use client";

import { use, useEffect } from "react";
import { useRouter } from "next/navigation";

interface KnowledgeRedirectProps {
  params: Promise<{ assistantId: string }>;
}

export default function LegacyKnowledgeRedirectPage({ params }: KnowledgeRedirectProps) {
  const resolvedParams = use(params);
  const router = useRouter();

  useEffect(() => {
    router.replace(`/assistants/${resolvedParams.assistantId}/data-sources`);
  }, [router, resolvedParams.assistantId]);

  return (
    <div
      style={{
        padding: "var(--spacing-8)",
        textAlign: "center",
        color: "var(--color-text-secondary)",
      }}
    >
      Redirecting to data sources...
    </div>
  );
}
