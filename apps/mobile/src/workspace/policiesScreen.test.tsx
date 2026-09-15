import { fireEvent, render } from "@testing-library/react-native";
import { router } from "expo-router";

import PoliciesScreen from "@/app/policies";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

jest.mock("expo-router", () => ({
  router: { back: jest.fn(), push: jest.fn(), replace: jest.fn() },
}));
jest.mock("@/src/prototype/PrototypeProvider", () => ({
  usePrototype: jest.fn(),
}));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
}));

const mockedPrototype = jest.mocked(usePrototype);
const mockedWorkspace = jest.mocked(useWorkspace);
const reset = jest.fn();
const revokePolicy = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  mockedPrototype.mockReturnValue({
    reset,
    revokePolicy,
    snapshot: {
      policies: [
        {
          affectedCaseIds: [],
          description: "테스트용 모의 권한",
          grantMode: "STANDING",
          policyId: "fixture-policy",
          recentUse: null,
          revokedAt: null,
          riskCeiling: "LOW",
          scope: "fixture-only",
          status: "ACTIVE",
          title: "모의 반복 허용",
          version: 1,
        },
      ],
    },
  } as never);
});

it("does not expose retained mock policies or their mutations in a live workspace", async () => {
  mockedWorkspace.mockReturnValue({ source: "LIVE" } as never);

  const screen = await render(<PoliciesScreen />);

  expect(screen.getByText("Automatic execution is not available")).toBeTruthy();
  expect(screen.queryByText("모의 반복 허용")).toBeNull();
  expect(screen.queryByText("Reset example data")).toBeNull();
  expect(screen.queryByText("Revoke permission")).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", { name: "Review connection permissions" }),
  );
  expect(router.push).toHaveBeenCalledWith("/connections");
  expect(reset).not.toHaveBeenCalled();
  expect(revokePolicy).not.toHaveBeenCalled();
});

it("keeps explicit mock policy review available in a chosen scenario", async () => {
  mockedWorkspace.mockReturnValue({ source: "SCENARIO" } as never);

  const screen = await render(<PoliciesScreen />);

  expect(screen.getByText("모의 반복 허용")).toBeTruthy();
  expect(screen.getByText("Example permissions")).toBeTruthy();
  expect(screen.getByText("Reset example data")).toBeTruthy();
  expect(screen.queryByText("Automatic execution is not available")).toBeNull();
  expect(reset).not.toHaveBeenCalled();
  expect(revokePolicy).not.toHaveBeenCalled();
});
