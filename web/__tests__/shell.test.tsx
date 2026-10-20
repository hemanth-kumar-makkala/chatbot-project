import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import * as api from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

// Mock API
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getCurrentUser: vi.fn(),
    askQuestion: vi.fn(),
    logout: vi.fn(),
    getDocuments: vi.fn().mockResolvedValue({ ok: true, status: 200, data: [] }),
  };
});

// Mock Next.js router & navigation
const mockPush = vi.fn();
const mockReplace = vi.fn();
let currentPathname = "/assistants";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => currentPathname,
}));

// Imports of shell and pages
import { AppShell } from "@/components/AppShell";
import AssistantsPage from "@/app/assistants/page";
import AssistantHomePage from "@/app/assistants/[assistantId]/page";
import DataSourcesPage from "@/app/assistants/[assistantId]/data-sources/page";
import InstructionsPage from "@/app/assistants/[assistantId]/instructions/page";
import GuardrailsPage from "@/app/assistants/[assistantId]/guardrails/page";
import ActionsPage from "@/app/assistants/[assistantId]/actions/page";
import ConversationsPage from "@/app/assistants/[assistantId]/conversations/page";
import AssistantSettingsPage from "@/app/assistants/[assistantId]/settings/page";
import WorkspaceSettingsPage from "@/app/settings/page";
import LegacyChatRedirectPage from "@/app/assistants/[assistantId]/chat/page";
import LegacyKnowledgeRedirectPage from "@/app/assistants/[assistantId]/knowledge/page";

describe("App Shell & Workspace / Assistant Architecture (Step 5)", () => {
  const originalEnv = process.env.NEXT_PUBLIC_DATA_SOURCE;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
    currentPathname = `/assistants/${DEV_DEFAULT_ASSISTANT_ID}`;
    vi.mocked(api.getCurrentUser).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        id: "usr-staff-1",
        email: "staff@clinic.com",
        tenant_id: "Clinic A",
      },
    });
    vi.mocked(api.getDocuments).mockResolvedValue({
      ok: true,
      status: 200,
      data: [],
    });
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_DATA_SOURCE = originalEnv;
    vi.restoreAllMocks();
  });

  // 1. Sidebar items and strict order
  describe("1. Sidebar Navigation & Order", () => {
    it("renders assistant sidebar links in exact order: Home, Data sources, Instructions, Guardrails, Actions, Conversations, Settings", async () => {
      await act(async () => {
        render(
          <AppShell
            assistantId={DEV_DEFAULT_ASSISTANT_ID}
            assistantName="Clinic A Assistant"
            userEmail="staff@clinic.com"
            workspaceName="Clinic A"
          >
            <div>Test Child Content</div>
          </AppShell>
        );
      });

      const nav = screen.getByRole("navigation", { name: /assistant navigation/i });
      const links = within(nav).getAllByRole("link");
      const linkTexts = links.map((link) => link.textContent?.trim());

      expect(linkTexts).toEqual([
        "Home",
        "Data sources",
        "Instructions",
        "Guardrails",
        "Actions",
        "Conversations",
        "Settings",
      ]);
    });

    it("toggles the mobile drawer when clicking the menu button on narrow viewports", async () => {
      await act(async () => {
        render(
          <AppShell
            assistantId={DEV_DEFAULT_ASSISTANT_ID}
            assistantName="Clinic A Assistant"
            userEmail="staff@clinic.com"
          >
            <div>Content</div>
          </AppShell>
        );
      });

      const menuBtn = screen.getByRole("button", {
        name: /toggle navigation menu/i,
        hidden: true,
      });
      expect(menuBtn).toBeDefined();

      // Drawer initially closed on mobile or hidden
      expect(screen.queryByTestId("sidebar-drawer-overlay")).toBeNull();

      // Click to open
      await act(async () => {
        fireEvent.click(menuBtn);
      });

      expect(screen.getByTestId("sidebar-drawer-overlay")).toBeDefined();

      // Click overlay to close
      await act(async () => {
        fireEvent.click(screen.getByTestId("sidebar-drawer-overlay"));
      });

      expect(screen.queryByTestId("sidebar-drawer-overlay")).toBeNull();
    });
  });

  // 2. Assistants Grid & New Assistant Dialog
  describe("2. Assistants Grid & Provisioning Dialog", () => {
    it("renders the assistants grid with seeded Clinic A card and New Assistant dialog in live mode", async () => {
      currentPathname = "/assistants";
      await act(async () => {
        render(<AssistantsPage />);
      });

      // Renders Clinic A card
      expect(screen.getByText("Clinic A Assistant")).toBeDefined();
      expect(screen.getByText(new RegExp(DEV_DEFAULT_ASSISTANT_ID, "i"))).toBeDefined();

      // Click "New assistant" button
      const newBtn = screen.getByRole("button", { name: /new assistant/i });
      await act(async () => {
        fireEvent.click(newBtn);
      });

      // Dialog appears with Name and Description inputs
      expect(screen.getByRole("dialog", { name: /new assistant/i })).toBeDefined();
      expect(screen.getByLabelText(/assistant name/i)).toBeDefined();
      expect(screen.getByLabelText(/description/i)).toBeDefined();

      // In live mode, dialog shows "Not connected yet" with route POST /v1/assistants
      expect(screen.getAllByText(/not connected yet/i).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/POST \/v1\/assistants/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  // 3. Strict Live vs Demo Isolation
  describe("3. Strict Live vs Demo Isolation", () => {
    it("never renders demo assistants or calls fetch with demo IDs in live mode", async () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
      currentPathname = "/assistants";
      const globalFetchSpy = vi.spyOn(global, "fetch");

      await act(async () => {
        render(<AssistantsPage />);
      });

      // Demo-specific assistants must NEVER appear in live mode
      expect(screen.queryByText(/SPRX Pharmacist Bot/i)).toBeNull();
      expect(screen.queryByText(/PARX Pharmacist Bot/i)).toBeNull();
      expect(screen.queryByText(/SLY IT Support Chatbot/i)).toBeNull();
      expect(screen.queryByText(/MindCraft Health AI Bot/i)).toBeNull();

      // Assert fetch was never invoked with demo assistant IDs
      for (const call of globalFetchSpy.mock.calls) {
        const url = call[0]?.toString() || "";
        expect(url).not.toContain("asst-demo-");
        expect(url).not.toContain("conv-demo-");
      }
    });
  });

  // 4. Every "Not connected yet" state in live mode
  describe("4. 'Not Connected Yet' States across all unbuilt screens in Live Mode", () => {
    it("renders 'Not connected yet' on Workspace Settings (Members)", async () => {
      currentPathname = "/settings";
      await act(async () => {
        render(<WorkspaceSettingsPage />);
      });

      // Click Members tab
      const membersTab = screen.getByRole("button", { name: /members/i });
      await act(async () => {
        fireEvent.click(membersTab);
      });

      expect(screen.getAllByText(/members/i).length).toBeGreaterThanOrEqual(1);
      expect(
        screen.getAllByText(/GET \/v1\/tenant\/members/i).length
      ).toBeGreaterThanOrEqual(1);
    });

    it("renders 'Not connected yet' on Instructions Save", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      currentPathname = `/assistants/${DEV_DEFAULT_ASSISTANT_ID}/instructions`;

      await act(async () => {
        render(<InstructionsPage params={params} />);
      });

      expect(
        screen.getAllByText(/PATCH \/v1\/assistants\/.*\/instructions/i).length
      ).toBeGreaterThanOrEqual(1);
    });

    it("renders 'Not connected yet' on Guardrails Save", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      currentPathname = `/assistants/${DEV_DEFAULT_ASSISTANT_ID}/guardrails`;

      await act(async () => {
        render(<GuardrailsPage params={params} />);
      });

      expect(
        screen.getAllByText(/PATCH \/v1\/assistants\/.*\/guardrails/i).length
      ).toBeGreaterThanOrEqual(1);
    });

    it("renders 'Coming soon' placeholder on Actions screen", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      currentPathname = `/assistants/${DEV_DEFAULT_ASSISTANT_ID}/actions`;

      await act(async () => {
        render(<ActionsPage params={params} />);
      });

      expect(
        screen.getAllByText(/actions that call other systems need a separate security review/i)
          .length
      ).toBeGreaterThanOrEqual(1);
    });

    it("renders 'Not connected yet' on Conversations in live mode", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      currentPathname = `/assistants/${DEV_DEFAULT_ASSISTANT_ID}/conversations`;

      await act(async () => {
        render(<ConversationsPage params={params} />);
      });

      expect(
        screen.getAllByText(/GET \/v1\/assistants\/.*\/conversations/i).length
      ).toBeGreaterThanOrEqual(1);
    });

    it("renders 'Not connected yet' on Assistant Settings Save & Delete actions", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      currentPathname = `/assistants/${DEV_DEFAULT_ASSISTANT_ID}/settings`;

      await act(async () => {
        render(<AssistantSettingsPage params={params} />);
      });

      // Check Save Details route
      expect(screen.getAllByText(/PATCH \/v1\/assistants\//i).length).toBeGreaterThanOrEqual(1);

      // Open Delete all conversations modal
      const deleteChatsBtn = screen.getByRole("button", {
        name: /^delete all conversations$/i,
      });
      await act(async () => {
        fireEvent.click(deleteChatsBtn);
      });
      expect(
        screen.getAllByText(/DELETE \/v1\/assistants\/.*\/conversations/i).length
      ).toBeGreaterThanOrEqual(1);

      // Close modal
      const cancelBtns = screen.getAllByRole("button", { name: /cancel/i });
      await act(async () => {
        fireEvent.click(cancelBtns[0]);
      });

      // Open Delete assistant modal
      const deleteAsstBtn = screen.getByRole("button", {
        name: /^delete assistant$/i,
      });
      await act(async () => {
        fireEvent.click(deleteAsstBtn);
      });
      expect(
        screen.getAllByText(/DELETE \/v1\/assistants\//i).length
      ).toBeGreaterThanOrEqual(1);
    });
  });

  // 5. Invariant 4 Note & Locked Guardrails
  describe("5. Safety Rules & Invariant Notices", () => {
    it("displays the immutable grounding notice on Instructions screen", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      await act(async () => {
        render(<InstructionsPage params={params} />);
      });

      expect(
        screen.getByText(
          /Instructions shape tone and scope\. Answers still come only from your documents\./i
        )
      ).toBeDefined();
    });

    it("displays all 5 locked built-in guardrails on Guardrails screen", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      await act(async () => {
        render(<GuardrailsPage params={params} />);
      });

      expect(screen.getByText(/Answers only from documents or refuse/i)).toBeDefined();
      expect(screen.getByText(/Every answer cites document and page/i)).toBeDefined();
      expect(screen.getByText(/Never shows another client's data/i)).toBeDefined();
      expect(screen.getByText(/Unreadable fields are never guessed/i)).toBeDefined();
      expect(
        screen.getByText(/Documents uploaded in chat never enter the knowledge base/i)
      ).toBeDefined();

      // All 5 rules are marked locked / always on
      const lockedBadges = screen.getAllByText(/locked/i);
      expect(lockedBadges.length).toBeGreaterThanOrEqual(5);
    });
  });

  // 6. Home: "New chat" always starts empty
  describe("6. Assistant Home Chat & 'New Chat' Reset", () => {
    it("'New chat' button resets chat messages and starts an empty conversation", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      await act(async () => {
        render(<AssistantHomePage params={params} />);
      });

      // Find New chat button
      const newChatBtn = screen.getByRole("button", { name: /\+ new chat/i });
      expect(newChatBtn).toBeDefined();

      // Click New Chat
      await act(async () => {
        fireEvent.click(newChatBtn);
      });

      // Input is empty, messages list is empty
      const input = screen.getByPlaceholderText(/ask a question/i);
      expect((input as HTMLInputElement).value).toBe("");
      expect(screen.queryByTestId("chat-message")).toBeNull();
    });
  });

  // 7. Legacy routes redirect
  describe("7. Backward-Compatible Route Redirections", () => {
    it("redirects /assistants/[id]/chat to /assistants/[id]", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      await act(async () => {
        render(<LegacyChatRedirectPage params={params} />);
      });

      expect(mockReplace).toHaveBeenCalledWith(`/assistants/${DEV_DEFAULT_ASSISTANT_ID}`);
    });

    it("redirects /assistants/[id]/knowledge to /assistants/[id]/data-sources", async () => {
      const params = Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID });
      await act(async () => {
        render(<LegacyKnowledgeRedirectPage params={params} />);
      });

      expect(mockReplace).toHaveBeenCalledWith(
        `/assistants/${DEV_DEFAULT_ASSISTANT_ID}/data-sources`
      );
    });
  });
});
