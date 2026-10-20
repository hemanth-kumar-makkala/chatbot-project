"use client";

import { use, useEffect } from "react";
import { useRouter } from "next/navigation";

interface ChatRedirectProps {
  params: Promise<{ assistantId: string }>;
}

export default function LegacyChatRedirectPage({ params }: ChatRedirectProps) {
  const resolvedParams = use(params);
  const router = useRouter();

  useEffect(() => {
    router.replace(`/assistants/${resolvedParams.assistantId}`);
  }, [router, resolvedParams.assistantId]);

  return (
    <div
      style={{
        padding: "var(--spacing-8)",
        textAlign: "center",
        color: "var(--color-text-secondary)",
      }}
    >
      Redirecting to assistant home...
    </div>
  );
}
