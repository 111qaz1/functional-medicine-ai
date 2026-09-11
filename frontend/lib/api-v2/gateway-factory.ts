import { FixtureWorkflowGateway, type FixtureScenario } from "./fixture-gateway";
import { HttpWorkflowGateway, type WorkflowGateway } from "./gateway";

export function createWorkflowGateway(fixtureMode: boolean, fixtureScenario: FixtureScenario): WorkflowGateway {
  return fixtureMode ? new FixtureWorkflowGateway(fixtureScenario) : new HttpWorkflowGateway();
}
