import { fireEvent, render } from "@testing-library/react-native";
import { router } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { Platform } from "react-native";

import ConnectionsScreen from "@/app/connections";
import { disconnectedGoogle } from "@/src/connections/googleConnectionView";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import type { PrototypeConnection } from "@/src/prototype/types";

jest.mock("@/src/connections/useGoogleConnection", () => ({
  useGoogleConnection: jest.fn(),
}));
jest.mock("expo-web-browser", () => ({ dismissAuthSession: jest.fn() }));
jest.mock("@/src/prototype/PrototypeProvider", () => ({
  usePrototype: jest.fn(),
}));
jest.mock("@/src/theme/useAppTheme", () => {
  const { appColors } = jest.requireActual("@/src/theme/tokens");
  return { useAppTheme: () => ({ colors: appColors.light }) };
});
jest.mock("expo-router", () => ({
  router: { back: jest.fn(), replace: jest.fn() },
}));

const mockedGoogle = jest.mocked(useGoogleConnection);
const mockedPrototype = jest.mocked(usePrototype);
const prototypeConnect = jest.fn();
const prototypeDisconnect = jest.fn();
const connectionAction =
  /^(Connect Google|Reconnect Google|Disconnect Google|Resume Google connection)$/;

function googleState(
  connectionOverrides: Partial<PrototypeConnection> = {},
  updating = false,
) {
  const connection = { ...disconnectedGoogle(), ...connectionOverrides };
  const state = {
    checking: false,
    connect: jest.fn().mockResolvedValue(connection),
    connection,
    disconnect: jest.fn().mockResolvedValue(disconnectedGoogle()),
    error: null,
    refresh: jest.fn().mockResolvedValue(connection),
    rescan: jest.fn().mockResolvedValue(connection),
    updating,
  };
  mockedGoogle.mockReturnValue(state);
  return state;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedPrototype.mockReturnValue({
    connect: prototypeConnect,
    disconnect: prototypeDisconnect,
    error: null,
    snapshot: null,
    status: "ready",
  } as never);
  googleState();
});

afterEach(() => {
  jest.restoreAllMocks();
});

test("Android offers native browser return guidance without invoking unsupported dismissal or resetting OAuth", async () => {
  jest.replaceProperty(Platform, "OS", "android");
  const google = googleState({ status: "CONNECTING" }, true);
  const screen = await render(<ConnectionsScreen />);
  expect(screen.queryByRole("button", { name: "Close sign-in" })).toBeNull();
  expect(
    screen.getByText("Close the sign-in window to return to the app."),
  ).toBeTruthy();
  const reconnect = screen.getByRole("button", {
    name: "Resume Google connection",
  });
  expect(reconnect).toBeDisabled();
  await fireEvent.press(reconnect);
  expect(WebBrowser.dismissAuthSession).not.toHaveBeenCalled();
  expect(google.connect).not.toHaveBeenCalled();
  expect(google.updating).toBe(true);
});

test.each(["ios", "web"] as const)(
  "%s dismisses a supported auth session and waits for the hook to settle",
  async (platform) => {
    jest.replaceProperty(Platform, "OS", platform);
    const google = googleState({ status: "CONNECTING" }, true);
    const screen = await render(<ConnectionsScreen />);
    await fireEvent.press(
      screen.getByRole("button", { name: "Close sign-in" }),
    );
    expect(WebBrowser.dismissAuthSession).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: "Resume Google connection" }),
    ).toBeDisabled();
    expect(google.connect).not.toHaveBeenCalled();
    expect(google.updating).toBe(true);
    mockedGoogle.mockReturnValue({
      ...google,
      updating: false,
      error: "Google connection cancelled.",
    });
    await screen.rerender(<ConnectionsScreen />);
    expect(screen.queryByRole("button", { name: "Close sign-in" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Resume Google connection" }),
    ).not.toBeDisabled();
    expect(screen.getByText("Google connection cancelled.")).toBeTruthy();
  },
);

test("upcoming services have no connection controls or progress indicators", async () => {
  const screen = await render(<ConnectionsScreen />);

  expect(screen.getByLabelText("SmartThings, coming soon")).toBeTruthy();
  expect(screen.getByLabelText("SMS, coming soon")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /SmartThings|문자/ })).toBeNull();
  expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
  expect(prototypeConnect).not.toHaveBeenCalled();
  expect(prototypeDisconnect).not.toHaveBeenCalled();
});

test("a disconnected Google account can connect without opening details", async () => {
  const google = googleState();
  const screen = await render(<ConnectionsScreen />);

  await fireEvent.press(
    screen.getByRole("button", { name: "Connect Google", disabled: false }),
  );

  expect(google.connect).toHaveBeenCalledTimes(1);
  expect(google.disconnect).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

test.each(["CONNECTED", "SCANNING"] as const)(
  "direct Google authorization success %s returns to main interest setup immediately",
  async (status) => {
    const google = googleState();
    google.connect.mockResolvedValueOnce({ ...google.connection, status });
    const screen = await render(<ConnectionsScreen />);

    await fireEvent.press(
      screen.getByRole("button", { name: "Connect Google", disabled: false }),
    );

    expect(router.replace).toHaveBeenCalledTimes(1);
    expect(router.replace).toHaveBeenCalledWith({
      pathname: "/(tabs)",
      params: { mailSetup: "1" },
    });
    expect(google.rescan).not.toHaveBeenCalled();
  },
);

test("cancelling Google authorization does not redirect to interest setup", async () => {
  const google = googleState();
  google.connect.mockRejectedValueOnce(
    new Error("Google connection cancelled."),
  );
  const screen = await render(<ConnectionsScreen />);

  await fireEvent.press(
    screen.getByRole("button", { name: "Connect Google", disabled: false }),
  );

  expect(router.replace).not.toHaveBeenCalled();
  expect(google.rescan).not.toHaveBeenCalled();
});

test.each([true, false])(
  "CONNECTING setup redirects only when Gmail access is already granted: %s",
  async (hasMailAccess) => {
    const google = googleState();
    google.connect.mockResolvedValueOnce({
      ...google.connection,
      status: "CONNECTING",
      grantedScopes: hasMailAccess
        ? ["https://www.googleapis.com/auth/gmail.readonly"]
        : [],
    });
    const screen = await render(<ConnectionsScreen />);
    await fireEvent.press(
      screen.getByRole("button", { name: "Connect Google", disabled: false }),
    );

    if (hasMailAccess) {
      expect(router.replace).toHaveBeenCalledWith({
        pathname: "/(tabs)",
        params: { mailSetup: "1" },
      });
    } else {
      expect(router.replace).not.toHaveBeenCalled();
    }
    expect(google.rescan).not.toHaveBeenCalled();
  },
);

test("initial lookup does not present a disconnected account or an authorization action", async () => {
  const google = googleState();
  mockedGoogle.mockReturnValue({ ...google, checking: true });
  const screen = await render(<ConnectionsScreen />);

  expect(
    screen.getByRole("progressbar", { name: "Checking Google connection" }),
  ).toBeTruthy();
  expect(screen.queryByText("Not connected")).toBeNull();
  expect(screen.queryByRole("button", { name: "Connect Google" })).toBeNull();

  mockedGoogle.mockReturnValue({
    ...google,
    connection: { ...google.connection, status: "CONNECTED" },
  });
  await screen.rerender(<ConnectionsScreen />);
  expect(screen.getByText("Connected")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(google.connect).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

test("disconnect requires opening the connected service and pressing its management action", async () => {
  const google = googleState({ status: "CONNECTED" });
  const screen = await render(<ConnectionsScreen />);

  expect(
    screen.queryByRole("button", { name: "Disconnect Google" }),
  ).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Google connection details",
      expanded: false,
    }),
  );
  expect(google.disconnect).not.toHaveBeenCalled();
  expect(screen.getByText("Automatic sync")).toBeTruthy();

  await fireEvent.press(
    screen.getByRole("button", { name: "Disconnect Google", disabled: false }),
  );

  expect(google.disconnect).toHaveBeenCalledTimes(1);
  expect(google.connect).not.toHaveBeenCalled();
});

test.each([
  ["CONNECTING", "Resume Google connection"],
  ["ERROR", "Reconnect Google"],
] as const)(
  "%s retains the existing connection recovery action",
  async (status, label) => {
    const google = googleState({ status });
    const screen = await render(<ConnectionsScreen />);

    await fireEvent.press(
      screen.getByRole("button", { name: label, disabled: false }),
    );

    expect(google.connect).toHaveBeenCalledTimes(1);
    expect(google.disconnect).not.toHaveBeenCalled();
  },
);

test("a scan exposes its actual progress and retains reconnect when idle", async () => {
  const google = googleState({ status: "SCANNING", scanProgress: 42 });
  const screen = await render(<ConnectionsScreen />);

  expect(
    screen.getByRole("progressbar", { name: "Gmail sync progress" }).props
      .accessibilityValue,
  ).toEqual({ min: 0, max: 100, now: 42 });
  await fireEvent.press(
    screen.getByRole("button", { name: "Reconnect Google", disabled: false }),
  );

  expect(google.connect).toHaveBeenCalledTimes(1);
  expect(google.disconnect).not.toHaveBeenCalled();
});

test("revocation stays in progress without exposing another connection action", async () => {
  const google = googleState({ status: "REVOKING", scanProgress: 100 });
  const screen = await render(<ConnectionsScreen />);

  expect(
    screen.getByRole("progressbar", { name: "Disconnecting Google" }).props
      .accessibilityValue,
  ).toBeUndefined();
  await fireEvent.press(
    screen.getByRole("button", { name: "Google connection details" }),
  );
  expect(screen.queryByRole("button", { name: connectionAction })).toBeNull();
  expect(google.connect).not.toHaveBeenCalled();
  expect(google.disconnect).not.toHaveBeenCalled();
});

test.each([
  "DISCONNECTED",
  "CONNECTED",
  "CONNECTING",
  "SCANNING",
  "ERROR",
] as const)(
  "%s blocks connection actions while an operation is updating",
  async (status) => {
    const google = googleState({ status }, true);
    const screen = await render(<ConnectionsScreen />);
    if (status === "CONNECTED") {
      await fireEvent.press(
        screen.getByRole("button", { name: "Google connection details" }),
      );
    }

    for (const button of screen.getAllByRole("button", {
      name: connectionAction,
    })) {
      expect(button).toBeDisabled();
      await fireEvent.press(button);
    }

    expect(google.connect).not.toHaveBeenCalled();
    expect(google.disconnect).not.toHaveBeenCalled();
  },
);

test.each(["connect", "disconnect"] as const)(
  "a failed %s stays on the screen and shows the hook error",
  async (action) => {
    const actionLabel =
      action === "disconnect" ? "Disconnect Google" : "Connect Google";
    const google = googleState({
      status: action === "disconnect" ? "CONNECTED" : "DISCONNECTED",
    });
    google[action].mockRejectedValueOnce(
      new Error("connection request failed"),
    );
    const screen = await render(<ConnectionsScreen />);
    if (action === "disconnect") {
      await fireEvent.press(
        screen.getByRole("button", { name: "Google connection details" }),
      );
    }

    await fireEvent.press(
      screen.getByRole("button", { name: actionLabel, disabled: false }),
    );
    mockedGoogle.mockReturnValue({
      ...google,
      error: "연결 요청을 완료하지 못했어요.",
    });
    await screen.rerender(<ConnectionsScreen />);

    expect(google[action]).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: actionLabel, disabled: false }),
    ).toBeTruthy();
  },
);
