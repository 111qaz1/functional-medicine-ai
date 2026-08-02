import { AssistantRuleManager } from "../../components/assistant-rule-manager";
import { AdminGate } from "../../components/admin-gate";

export default function AssistantRulePage() {
  return <AdminGate><AssistantRuleManager /></AdminGate>;
}
