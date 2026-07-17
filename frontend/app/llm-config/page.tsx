import { LlmConfigManager } from "../../components/llm-config-manager";
import { AdminGate } from "../../components/admin-gate";

export default function LlmConfigPage() {
  return <AdminGate><LlmConfigManager /></AdminGate>;
}
