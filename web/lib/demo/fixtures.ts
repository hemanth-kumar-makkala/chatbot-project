/**
 * Demo Data Fixtures for UI-First Demonstration
 * Synthetic only, strictly using golden-set facts.
 * Adheres to docs/ui/CONNECTION_MAP.md and UI-1 Scope.
 */

import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import type { QuestionResponse, SourceCitation } from "@/lib/api";

export interface DemoAssistant {
  id: string;
  name: string;
  client_name: string;
  description: string;
  document_count: number;
}

export interface DemoMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  response?: QuestionResponse;
}

export interface DemoConversation {
  id: string;
  assistant_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_preview: string;
  messages: DemoMessage[];
}

export const DEMO_ASSISTANTS: DemoAssistant[] = [
  {
    // Maple Ridge is the dev default assistant wired to live backend in Phase 3
    id: DEV_DEFAULT_ASSISTANT_ID,
    name: "Maple Ridge Family Medicine Assistant",
    client_name: "Maple Ridge Family Medicine",
    description:
      "Practice guidelines, prior authorization guide, clinic operational hours, and accepted health plans.",
    document_count: 6,
  },
  {
    id: "00000000-0000-0000-0002-000000000002",
    name: "Pinecrest Medical Group Assistant",
    client_name: "Pinecrest Medical Group",
    description:
      "Front desk intake workflows, patient registration guidelines, and triage escalation paths.",
    document_count: 4,
  },
  {
    id: "00000000-0000-0000-0002-000000000003",
    name: "Horizon Valley Health Assistant",
    client_name: "Horizon Valley Health",
    description:
      "Payer prior authorization criteria, specialist referral directory, and clinical protocols.",
    document_count: 5,
  },
];

export const DEMO_CONVERSATIONS: Record<string, DemoConversation[]> = {
  [DEV_DEFAULT_ASSISTANT_ID]: [
    {
      id: "conv-maple-01",
      assistant_id: DEV_DEFAULT_ASSISTANT_ID,
      title: "Outpatient Surgery Prior Auth Submission Window",
      created_at: "2026-10-15T09:30:00Z",
      updated_at: "2026-10-15T09:31:00Z",
      last_message_preview: "How far in advance must outpatient surgery prior auth be submitted?",
      messages: [
        {
          id: "msg-01-u1",
          role: "user",
          content: "How far in advance must outpatient surgery prior authorization requests be submitted?",
          created_at: "2026-10-15T09:30:00Z",
        },
        {
          id: "msg-01-a1",
          role: "assistant",
          // Source: evals/golden/manifest.json (doc_prior_auth_guide / prior_auth_guide.pdf, p. 1)
          // Fact: "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance."
          // Source: evals/golden/manifest.json (doc_prior_auth_guide / prior_auth_guide.pdf, p. 2)
          // Fact: "Urgent authorization appeals must be submitted to phone 555-0142 within 72 hours of denial."
          content:
            "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance. Urgent authorization appeals must be submitted to phone 555-0142 within 72 hours of denial.",
          created_at: "2026-10-15T09:30:03Z",
          response: {
            request_id: "req-demo-001",
            tenant_id: "00000000-0000-0000-0000-000000000001",
            conversation_id: "conv-maple-01",
            message_id: "msg-01-a1",
            status: "answered",
            answer:
              "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance. Urgent authorization appeals must be submitted to phone 555-0142 within 72 hours of denial.",
            citations: ["chunk-gold-001"],
            evidence_type: "text",
            sources: [
              {
                document_title: "prior_auth_guide.pdf",
                version: "1",
                page_number: 1,
                evidence_type: "text",
              },
            ],
            conflict_sources: [],
          },
        },
      ],
    },
    {
      id: "conv-maple-02",
      assistant_id: DEV_DEFAULT_ASSISTANT_ID,
      title: "Intake Fax Policy Conflict (555-0101 vs 555-0102)",
      created_at: "2026-10-14T14:15:00Z",
      updated_at: "2026-10-14T14:16:30Z",
      last_message_preview: "What is the front desk intake fax line?",
      messages: [
        {
          id: "msg-02-u1",
          role: "user",
          content: "What is the front desk intake fax line?",
          created_at: "2026-10-14T14:15:00Z",
        },
        {
          id: "msg-02-a1",
          role: "assistant",
          // Source: evals/golden/manifest.json (doc_front_desk_policy_v1 / front_desk_policy_v1.pdf, p. 1)
          // Fact: "Front desk intake fax number is 555-0101."
          // Source: evals/golden/manifest.json (doc_front_desk_policy_v2 / front_desk_policy_v2.pdf, p. 1)
          // Fact: "Front desk intake fax number is 555-0102."
          content:
            "Documents conflict on this point: Policy Version 1.0 lists front desk intake fax 555-0101, whereas Version 2.0 specifies revised intake fax 555-0102.",
          created_at: "2026-10-14T14:15:04Z",
          response: {
            request_id: "req-demo-002",
            tenant_id: "00000000-0000-0000-0000-000000000001",
            conversation_id: "conv-maple-02",
            message_id: "msg-02-a1",
            status: "conflicting",
            answer:
              "Documents conflict on this point: Policy Version 1.0 lists front desk intake fax 555-0101, whereas Version 2.0 specifies revised intake fax 555-0102.",
            citations: ["chunk-gold-v1", "chunk-gold-v2"],
            evidence_type: "text",
            sources: [],
            conflict_sources: [
              {
                document_title: "front_desk_policy_v1.pdf",
                version: "1.0",
                page_number: 1,
                evidence_type: "text",
              },
              {
                document_title: "front_desk_policy_v2.pdf",
                version: "2.0",
                page_number: 1,
                evidence_type: "text",
              },
            ],
          },
        },
      ],
    },
    {
      id: "conv-maple-03",
      assistant_id: DEV_DEFAULT_ASSISTANT_ID,
      title: "Patient Intake: Alex Morgan",
      created_at: "2026-10-13T11:00:00Z",
      updated_at: "2026-10-13T11:02:15Z",
      last_message_preview: "What is Alex Morgan's insurer and emergency contact?",
      messages: [
        {
          id: "msg-03-u1",
          role: "user",
          content: "What is Alex Morgan's insurer and emergency contact on file?",
          created_at: "2026-10-13T11:00:00Z",
        },
        {
          id: "msg-03-a1",
          role: "assistant",
          // Source: evals/golden/manifest.json (doc_cross_tenant_patient_a / tenant_a/patient_intake_form.pdf, p. 1)
          // Fact: "Patient Alex Morgan primary insurer is Apex Health Insurance."
          // Fact: "Emergency contact for Alex Morgan is phone 555-0131."
          // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
          // Fact: "Refill authorized for patient Alex Morgan chart MRN-4412-A."
          content:
            "Patient Alex Morgan (Chart #MRN-4412-A) has primary insurer Apex Health Insurance on file. Emergency contact is phone 555-0131.",
          created_at: "2026-10-13T11:00:02Z",
          response: {
            request_id: "req-demo-003",
            tenant_id: "00000000-0000-0000-0000-000000000001",
            conversation_id: "conv-maple-03",
            message_id: "msg-03-a1",
            status: "answered",
            answer:
              "Patient Alex Morgan (Chart #MRN-4412-A) has primary insurer Apex Health Insurance on file. Emergency contact is phone 555-0131.",
            citations: ["chunk-alex-001"],
            evidence_type: "text",
            sources: [
              {
                document_title: "tenant_a/patient_intake_form.pdf",
                version: "1",
                page_number: 1,
                evidence_type: "text",
              },
            ],
            conflict_sources: [],
          },
        },
      ],
    },
  ],
  "00000000-0000-0000-0002-000000000002": [
    {
      id: "conv-pine-01",
      assistant_id: "00000000-0000-0000-0002-000000000002",
      title: "Patient Intake & Primary Care Copay",
      created_at: "2026-10-15T08:00:00Z",
      updated_at: "2026-10-15T08:02:00Z",
      last_message_preview: "What is the primary care copayment and emergency contact for Alex Morgan?",
      messages: [
        {
          id: "msg-pine-u1",
          role: "user",
          content: "What is the primary care copayment and emergency contact for Alex Morgan?",
          created_at: "2026-10-15T08:00:00Z",
        },
        {
          id: "msg-pine-a1",
          role: "assistant",
          // Source: evals/golden/manifest.json (doc_eligibility_checklist / eligibility_checklist.pdf, p. 1)
          // Fact: "Standard copayment for primary care office visits is $25.00."
          // Source: evals/golden/manifest.json (doc_cross_tenant_patient_a / tenant_a/patient_intake_form.pdf, p. 1)
          // Fact: "Emergency contact for Alex Morgan is phone 555-0131."
          content:
            "Standard copayment for primary care office visits is $25.00. Emergency contact for Alex Morgan registered on file is phone 555-0131.",
          created_at: "2026-10-15T08:00:03Z",
          response: {
            request_id: "req-pine-001",
            tenant_id: "00000000-0000-0000-0000-000000000002",
            conversation_id: "conv-pine-01",
            message_id: "msg-pine-a1",
            status: "answered",
            answer:
              "Standard copayment for primary care office visits is $25.00. Emergency contact for Alex Morgan registered on file is phone 555-0131.",
            citations: ["chunk-elig-001"],
            evidence_type: "text",
            sources: [
              {
                document_title: "eligibility_checklist.pdf",
                version: "1",
                page_number: 1,
                evidence_type: "text",
              },
            ],
            conflict_sources: [],
          },
        },
      ],
    },
  ],
  "00000000-0000-0000-0002-000000000003": [
    {
      id: "conv-horiz-01",
      assistant_id: "00000000-0000-0000-0002-000000000003",
      title: "Clinical Escalation Protocols",
      created_at: "2026-10-15T07:30:00Z",
      updated_at: "2026-10-15T07:31:00Z",
      last_message_preview: "What is the clinical escalation desk telephone?",
      messages: [
        {
          id: "msg-horiz-u1",
          role: "user",
          content: "What is the clinical escalation desk telephone?",
          created_at: "2026-10-15T07:30:00Z",
        },
        {
          id: "msg-horiz-a1",
          role: "assistant",
          // Source: evals/golden/manifest.json (doc_clinical_protocols_manual / clinical_protocols_manual.pdf, p. 13)
          // Fact: "Clinical escalation desk telephone is 555-0176."
          // Fact: "Critical laboratory panic values must be reported to the attending clinician within 15 minutes."
          content:
            "Clinical escalation desk telephone is 555-0176. Critical laboratory panic values must be reported to the attending clinician within 15 minutes.",
          created_at: "2026-10-15T07:30:03Z",
          response: {
            request_id: "req-horiz-001",
            tenant_id: "00000000-0000-0000-0000-000000000003",
            conversation_id: "conv-horiz-01",
            message_id: "msg-horiz-a1",
            status: "answered",
            answer:
              "Clinical escalation desk telephone is 555-0176. Critical laboratory panic values must be reported to the attending clinician within 15 minutes.",
            citations: ["chunk-proto-001"],
            evidence_type: "text",
            sources: [
              {
                document_title: "clinical_protocols_manual.pdf",
                version: "1",
                page_number: 13,
                evidence_type: "text",
              },
            ],
            conflict_sources: [],
          },
        },
      ],
    },
  ],
};

export function getDemoAssistants(): DemoAssistant[] {
  return DEMO_ASSISTANTS;
}

export function getDemoConversations(assistantId: string): DemoConversation[] {
  return DEMO_CONVERSATIONS[assistantId] || DEMO_CONVERSATIONS[DEV_DEFAULT_ASSISTANT_ID] || [];
}

export function getDemoConversation(
  assistantId: string,
  conversationId: string
): DemoConversation | undefined {
  const list = getDemoConversations(assistantId);
  return list.find((c) => c.id === conversationId);
}

/**
 * Returns a canned response strictly using golden-set facts for demo assistants.
 * Ensures demo assistants never call live backend.
 */
export function getDemoAssistantAnswer(
  assistantId: string,
  query: string,
  conversationId?: string | null
): QuestionResponse {
  const convId = conversationId || `conv-demo-${Date.now()}`;
  const normalizedQuery = query.toLowerCase();

  if (normalizedQuery.includes("emergency") || normalizedQuery.includes("alex morgan") || normalizedQuery.includes("contact")) {
    // Source: evals/golden/manifest.json (doc_cross_tenant_patient_a / tenant_a/patient_intake_form.pdf, p. 1)
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      answer: "Emergency contact for Alex Morgan registered on file is phone 555-0131 (Apex Health Insurance).",
      citations: ["chunk-gold-alex"],
      evidence_type: "text",
      sources: [
        {
          document_title: "tenant_a/patient_intake_form.pdf",
          version: "1",
          page_number: 1,
          evidence_type: "text",
        },
      ],
      conflict_sources: [],
    };
  }

  if (normalizedQuery.includes("fax") || normalizedQuery.includes("conflict")) {
    // Source: evals/golden/manifest.json (doc_front_desk_policy_v1 and v2)
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "conflicting",
      answer: "Documents conflict: Policy Version 1.0 lists front desk intake fax 555-0101, whereas Version 2.0 specifies revised intake fax 555-0102.",
      citations: ["chunk-gold-v1", "chunk-gold-v2"],
      evidence_type: "text",
      sources: [],
      conflict_sources: [
        {
          document_title: "front_desk_policy_v1.pdf",
          version: "1.0",
          page_number: 1,
          evidence_type: "text",
        },
        {
          document_title: "front_desk_policy_v2.pdf",
          version: "2.0",
          page_number: 1,
          evidence_type: "text",
        },
      ],
    };
  }

  // Default demo answer using golden-set facts
  // Source: evals/golden/manifest.json (doc_prior_auth_guide / prior_auth_guide.pdf, p. 1)
  return {
    request_id: `req-demo-${Date.now()}`,
    tenant_id: "ten-demo",
    conversation_id: convId,
    message_id: `msg-demo-${Date.now()}`,
    status: "answered",
    answer: "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance. Emergency contact for Alex Morgan is 555-0131.",
    citations: ["chunk-gold-default"],
    evidence_type: "text",
    sources: [
      {
        document_title: "prior_auth_guide.pdf",
        version: "1",
        page_number: 1,
        evidence_type: "text",
      },
    ],
    conflict_sources: [],
  };
}
