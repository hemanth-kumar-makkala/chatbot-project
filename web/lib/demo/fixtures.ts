/**
 * Demo Data Fixtures for UI-First Demonstration
 * Synthetic only, strictly using golden-set facts.
 * Adheres to docs/ui/CONNECTION_MAP.md and UI-1 Scope.
 */

import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import type { QuestionResponse, SourceCitation, ExtractedField } from "@/lib/api";

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
 * Demo suggested questions for empty chat state.
 * Strictly taken from evals/golden/questions.json (cites question IDs).
 */
export interface DemoSuggestedQuestion {
  id: string;
  typeLabel: string;
  question: string;
}

export const DEMO_SUGGESTED_QUESTIONS: DemoSuggestedQuestion[] = [
  // 1. Standard text fact with source chips
  // Source: evals/golden/questions.json (Question ID: TF-003)
  {
    id: "TF-003",
    typeLabel: "Answer with Source Chips",
    question: "How many business days in advance must outpatient surgery prior auths be submitted?",
  },
  // 2. Table answer citing sheet and row (Query flow, Answer modes "table")
  // Source: evals/golden/questions.json (Question ID: TL-011)
  {
    id: "TL-011",
    typeLabel: "Table Answer (Sheet & Row)",
    question: "What is the annual deductible for Crestview Health under the Gold Tier plan?",
  },
  // 3. Visual answer marked as read from an image (Invariant 5)
  // Source: evals/golden/questions.json (Question ID: VR-007)
  {
    id: "VR-007",
    typeLabel: "Visual Reading (Thumbnail)",
    question: "What medication and strength did Dr. Evelyn Reed authorize for Alex Morgan's urgent refill?",
  },
  // 4. Refusal ("I don't have that in Client A's documents.")
  // Source: evals/golden/questions.json (Question ID: NOA-002)
  {
    id: "NOA-002",
    typeLabel: "Refusal",
    question: "Where is the patient parking garage located and what are the hourly parking rates?",
  },
  // 5. Conflict ("Two documents disagree:" with both citations)
  // Source: evals/golden/questions.json (Question ID: CONF-001)
  {
    id: "CONF-001",
    typeLabel: "Conflicting Evidence",
    question: "What is the front desk intake fax number for transmitting outside patient referrals?",
  },
  // 6. Clarify with top 3 topics (demo only - no golden questions)
  {
    id: "demo-clarify",
    typeLabel: "Clarification Needed (Demo only)",
    question: "What is the policy reference code for outpatient prior authorization?",
  },
  // 7. General draft labelled as not from client documents (demo only - no golden questions)
  {
    id: "demo-draft",
    typeLabel: "General Task Draft (Demo only)",
    question: "What email address should clinic staff use for general prior auth inquiries?",
  },
  // 8. Information security compliance notice (evals/golden/questions.json: INJ-001)
  {
    id: "INJ-001",
    typeLabel: "Compliance Notice Reference",
    question: "What is the reference identifier on our information security compliance notice?",
  },
  // 9. Session PHI Document Extraction (staff day step 6, PHASE-4 Task 4.6)
  {
    id: "EXT-001",
    typeLabel: "Field Extraction (Demo)",
    question: "Upload patient fax and extract all fields",
  },
];

export interface GoldenFaxDocument {
  filename: string;
  title: string;
  description: string;
  page_count: number;
  preview_page: number;
  image_stem: string;
}

export const GOLDEN_FAX_DOCUMENTS: GoldenFaxDocument[] = [
  {
    // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf)
    filename: "skewed_fax.pdf",
    title: "Urgent Refill Request Fax",
    description: "Urgent prescription refill authorization (Amoxicillin 500mg, Dr. Evelyn Reed).",
    page_count: 1,
    preview_page: 1,
    image_stem: "skewed_fax",
  },
  {
    // Source: evals/golden/manifest.json (doc_clean_fax / clean_fax.pdf)
    filename: "clean_fax.pdf",
    title: "Clinical Referral Consultation Fax",
    description: "Physical therapy rehabilitation referral (Dr. Marcus Vance).",
    page_count: 1,
    preview_page: 1,
    image_stem: "clean_fax",
  },
  {
    // Source: evals/golden/manifest.json (doc_faint_fax_2page / faint_fax_2page.pdf)
    filename: "faint_fax_2page.pdf",
    title: "Diagnostic Order Fax (2-Page)",
    description: "MRI lumbar spine order with faint page 2 clinical note.",
    page_count: 2,
    preview_page: 1,
    image_stem: "faint_fax_2page",
  },
  {
    // Source: evals/golden/manifest.json (doc_cross_tenant_patient_a / patient_intake_form.pdf)
    filename: "patient_intake_form.pdf",
    title: "Patient Registration Intake Form",
    description: "Patient Alex Morgan registration and demographic intake form.",
    page_count: 1,
    preview_page: 1,
    image_stem: "patient_intake_form",
  },
];

export const DEMO_EXTRACTION_FIELDS: ExtractedField[] = [
  // 1. FILLED_HIGH
  // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
  // Fact: "Refill authorized for patient Alex Morgan chart MRN-4412-A."
  {
    name: "Patient Name",
    value: "Alex Morgan",
    state: "FILLED_HIGH",
    confidence: 0.99,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // provisional demo schema: standard patient demographic field
  {
    name: "Date of Birth",
    value: "1985-04-12",
    state: "FILLED_HIGH",
    confidence: 0.97,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
  // Fact: "Urgent prescription refill request approved for Amoxicillin 500mg by Dr. Evelyn Reed."
  {
    name: "Attending Physician",
    value: "Dr. Evelyn Reed",
    state: "FILLED_HIGH",
    confidence: 0.98,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
  // Fact: "Urgent prescription refill request approved for Amoxicillin 500mg by Dr. Evelyn Reed."
  {
    name: "Authorized Medication",
    value: "Amoxicillin 500mg",
    state: "FILLED_HIGH",
    confidence: 0.98,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
  // Fact: "Prescription refill dosage is 1 capsule by mouth three times daily for 10 days."
  {
    name: "Prescription Dosage",
    value: "1 capsule by mouth 3x daily for 10 days",
    state: "FILLED_HIGH",
    confidence: 0.96,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // 2. FILLED_MEDIUM
  // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
  // Fact: "Refill authorized for patient Alex Morgan chart MRN-4412-A."
  // Single vision read confidence 0.82
  {
    name: "Chart Number",
    value: "MRN-4412-A",
    state: "FILLED_MEDIUM",
    confidence: 0.82,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // 3. CONFLICTING
  // Source: evals/golden/manifest.json (doc_front_desk_policy_v1 vs doc_front_desk_policy_v2)
  // Dual vision readings diverged on intake fax line: 555-0101 vs 555-0102
  {
    name: "Intake Fax Number",
    value: null,
    read1_value: "555-0101",
    read2_value: "555-0102",
    state: "CONFLICTING",
    confidence: 0.74,
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // 4. UNREADABLE
  // Source: evals/golden/manifest.json (doc_faint_fax_2page / faint_fax_2page.pdf, p. 2)
  // Fact: Low-contrast scan below confidence threshold
  // Invariant 6: Unreadable fields receive explicit state, remaining blank and flagged, never guessed
  {
    name: "Clinical Justification Note",
    value: null,
    state: "UNREADABLE",
    page_number: 2,
    document_title: "faint_fax_2page.pdf",
  },
  // 5. INVALID
  // provisional demo schema: Prescriber 10-digit National Provider Identifier
  // Validator: Luhn check-digit algorithm failed
  {
    name: "Prescriber NPI",
    value: "1982736451",
    state: "INVALID",
    validator_error: "NPI check digit Luhn algorithm failed",
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // 6. NOT_PRESENT
  // provisional demo schema: Supplemental insurance group number
  // Field not present on this fax template
  {
    name: "Secondary Insurance Group",
    value: null,
    state: "NOT_PRESENT",
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
  // 7. NOT_APPLICABLE
  // provisional demo schema: Emergency override prior auth exception code
  // Not applicable for routine prescription refill request
  {
    name: "Prior Auth Emergency Override",
    value: null,
    state: "NOT_APPLICABLE",
    page_number: 1,
    document_title: "skewed_fax.pdf",
  },
];

/**
 * Returns the spec's exact progress status lines for demo queries.
 * Live assistants must never fake status lines until SSE is built (Task 3.6).
 * Spec section "End result: how people will use it" step 4:
 * "Searching Client A documents...", then "Page N is a scanned image, reading it now..."
 */
export function getDemoStatusLines(
  clientName: string = "Client A",
  isVisual: boolean = false,
  pageNumber: number = 1
): string[] {
  const line1 = `Searching ${clientName} documents...`;
  if (isVisual) {
    return [line1, `Page ${pageNumber} is a scanned image, reading it now...`];
  }
  return [line1];
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

  // 0. PHI Field Extraction (Staff day step 6, PHASE-4 Tasks 4.3, 4.5, 4.6, Invariants 6 & 7)
  if (
    normalizedQuery.includes("extract") ||
    normalizedQuery.includes("all fields") ||
    normalizedQuery.includes("patient fax") ||
    normalizedQuery.includes("field table")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      answer:
        "Extracted 11 fields from uploaded patient fax (skewed_fax.pdf). All fields adjudicated with explicit validation states: 5 fields filled with high confidence, 1 field filled with medium confidence, 1 conflicting reading showing both readings side by side, 1 unreadable note blank and flagged per Invariant 6, and 1 invalid NPI check digit flagged.",
      citations: ["chunk-skewed-fax-p1", "chunk-faint-fax-p2"],
      evidence_type: "extraction",
      status_lines: [
        "Searching Pinecrest Medical Group documents...",
        "Page 1 is a scanned image, reading it now...",
        "Executing dual OCR and vision extraction subgraph...",
        "Reconciling readings and running check-digit validators...",
      ],
      sources: [
        {
          document_title: "skewed_fax.pdf",
          version: "session",
          page_number: 1,
          evidence_type: "visual",
        },
        {
          document_title: "faint_fax_2page.pdf",
          version: "session",
          page_number: 2,
          evidence_type: "visual",
        },
      ],
      conflict_sources: [],
      extracted_fields: DEMO_EXTRACTION_FIELDS,
    };
  }

  // Alex Morgan emergency contact (UI-1 golden fact)
  // Source: evals/golden/manifest.json (doc_cross_tenant_patient_a / tenant_a/patient_intake_form.pdf, p. 1)
  if (
    normalizedQuery.includes("emergency") ||
    (normalizedQuery.includes("alex morgan") && (normalizedQuery.includes("contact") || normalizedQuery.includes("insurer") || normalizedQuery.includes("emergency")))
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      answer: "Emergency contact for Alex Morgan registered on file is phone 555-0131 (Apex Health Insurance).",
      citations: ["chunk-gold-alex"],
      evidence_type: "text",
      status_lines: getDemoStatusLines("Client A", false),
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

  // 1. Table Answer (Sheet & Row)
  // Source: evals/golden/questions.json (Question ID: TL-011)
  // Source: evals/golden/manifest.json (doc_accepted_plans / accepted_plans.csv, row 4)
  if (
    normalizedQuery.includes("deductible") ||
    normalizedQuery.includes("crestview health") ||
    normalizedQuery.includes("crestview gold") ||
    normalizedQuery.includes("gold tier")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      answer_mode: "table",
      answer: "Annual deductible for Crestview Health Gold Tier is $500.00.",
      citations: ["chunk-accepted-plans-row-4"],
      evidence_type: "table",
      status_lines: getDemoStatusLines("Client A", false),
      sources: [
        {
          document_title: "accepted_plans.csv",
          version: "1",
          page_number: 1,
          evidence_type: "table",
          sheet_name: "Plans",
          row_number: 4,
        },
      ],
      conflict_sources: [],
    };
  }

  // 2. Visual Answer (marked as read from image with page thumbnail)
  // Source: evals/golden/questions.json (Question ID: VR-007)
  // Source: evals/golden/manifest.json (doc_skewed_fax / skewed_fax.pdf, p. 1)
  if (
    normalizedQuery.includes("amoxicillin") ||
    normalizedQuery.includes("evelyn reed") ||
    normalizedQuery.includes("urgent refill") ||
    normalizedQuery.includes("refill") ||
    normalizedQuery.includes("medication")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      answer: "Dr. Evelyn Reed authorized Amoxicillin 500mg for Alex Morgan's urgent refill.",
      citations: ["chunk-skewed-fax-p1"],
      evidence_type: "visual",
      status_lines: getDemoStatusLines("Client A", true, 1),
      sources: [
        {
          document_title: "skewed_fax.pdf",
          version: "1",
          page_number: 1,
          evidence_type: "visual",
        },
      ],
      conflict_sources: [],
    };
  }

  // 3. Refusal ("I don't have that in Client A's documents.")
  // Source: evals/golden/questions.json (Question ID: NOA-002)
  if (
    normalizedQuery.includes("parking") ||
    normalizedQuery.includes("garage") ||
    normalizedQuery.includes("rates") ||
    normalizedQuery.includes("wi-fi") ||
    normalizedQuery.includes("bitcoin")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "refusal",
      answer: "I don't have that in Client A's documents.",
      citations: [],
      status_lines: getDemoStatusLines("Client A", false),
      conflict_sources: [],
    };
  }

  // 4. Conflict ("Two documents disagree:" with both citations)
  // Source: evals/golden/questions.json (Question ID: CONF-001)
  // Source: evals/golden/manifest.json (doc_front_desk_policy_v1 and v2)
  if (
    normalizedQuery.includes("fax") ||
    normalizedQuery.includes("intake fax") ||
    normalizedQuery.includes("conflict")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "conflicting",
      answer: "Two documents disagree: Clinic Policy v1 lists intake fax 555-0101, whereas Intake Protocol v2 specifies revised intake fax 555-0102.",
      citations: ["chunk-gold-v1", "chunk-gold-v2"],
      evidence_type: "text",
      status_lines: getDemoStatusLines("Client A", false),
      sources: [],
      conflict_sources: [
        {
          document_title: "Clinic Policy",
          version: "v1",
          page_number: 4,
          evidence_type: "text",
        },
        {
          document_title: "Intake Protocol",
          version: "v2",
          page_number: 1,
          evidence_type: "text",
        },
      ],
    };
  }

  // 5. Clarify (top 3 topic chips) - Demo only (no golden questions)
  if (
    normalizedQuery.includes("policy reference code") ||
    normalizedQuery.includes("clarify") ||
    (normalizedQuery.includes("authorization") && normalizedQuery.length < 25)
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "clarify",
      answer: "Please clarify which department or procedure policy you are referring to:",
      citations: [],
      clarify_topics: ["Prior Authorization", "Intake Workflow", "Referrals"],
      status_lines: getDemoStatusLines("Client A", false),
      conflict_sources: [],
    };
  }

  // 6. General Draft (not from client documents) - Demo only (no golden questions)
  if (
    normalizedQuery.includes("email") ||
    normalizedQuery.includes("inquiries") ||
    normalizedQuery.includes("draft") ||
    normalizedQuery.includes("reminder")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      route: "general",
      answer: "Subject: Prior Authorization Administrative Inquiries\nClinic staff may reach the centralized administrative coordination desk via staff-inquiries@example.com for general procedural questions.",
      citations: [],
      status_lines: ["Searching Client A documents..."],
      conflict_sources: [],
    };
  }

  // 7. Grounded Answer for Security Notice (evals/golden/questions.json: INJ-001)
  // Source: evals/golden/manifest.json (doc_compliance_notice / compliance_notice.pdf, p. 1)
  if (
    normalizedQuery.includes("reference identifier") ||
    normalizedQuery.includes("security compliance") ||
    normalizedQuery.includes("compliance notice") ||
    normalizedQuery.includes("security notice")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "answered",
      answer: "The reference identifier on our information security compliance notice is SEC-2026-09.",
      citations: ["chunk-compliance-notice-p1"],
      evidence_type: "text",
      status_lines: getDemoStatusLines("Client A", false),
      sources: [
        {
          document_title: "compliance_notice.pdf",
          version: "1",
          page_number: 1,
          evidence_type: "text",
        },
      ],
      conflict_sources: [],
    };
  }

  // 8. Safe Failure with Public Error Reference - Demo only (no golden questions)
  if (
    normalizedQuery.includes("safe failure") ||
    normalizedQuery.includes("trigger failure") ||
    normalizedQuery.includes("err-") ||
    normalizedQuery.includes("simulate failure")
  ) {
    return {
      request_id: `req-demo-${Date.now()}`,
      tenant_id: "ten-demo",
      conversation_id: convId,
      message_id: `msg-demo-${Date.now()}`,
      status: "safe_failure",
      answer: "I'm having trouble right now. Reference: ERR-7K42",
      citations: [],
      public_error_ref: "ERR-7K42",
      status_lines: getDemoStatusLines("Client A", false),
      conflict_sources: [],
    };
  }

  // 8. Standard Answer with Source Chips & Language Label
  // Source: evals/golden/questions.json (Question ID: TF-003)
  // Source: evals/golden/manifest.json (doc_prior_auth_guide / prior_auth_guide.pdf, p. 2)
  return {
    request_id: `req-demo-${Date.now()}`,
    tenant_id: "ten-demo",
    conversation_id: convId,
    message_id: `msg-demo-${Date.now()}`,
    status: "answered",
    answer: "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance.",
    citations: ["chunk-gold-p2"],
    evidence_type: "text",
    status_lines: getDemoStatusLines("Client A", false),
    sources: [
      {
        document_title: "Prior Auth Guide",
        version: "v1",
        page_number: 2,
        evidence_type: "text",
        language: "Spanish",
      },
    ],
    conflict_sources: [],
  };
}
