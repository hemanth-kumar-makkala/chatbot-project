import { redirect } from "next/navigation";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

export default function HomePage() {
  redirect(`/assistants/${DEV_DEFAULT_ASSISTANT_ID}/knowledge`);
}
