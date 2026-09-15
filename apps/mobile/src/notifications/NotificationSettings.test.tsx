import { fireEvent, render } from "@testing-library/react-native";

import { useNotifications } from "./NotificationProvider";
import { NotificationSettings } from "./NotificationSettings";

jest.mock("./NotificationProvider", () => ({ useNotifications: jest.fn() }));
jest.mock("@/src/components/BackHeader", () => ({ BackHeader: () => null }));
jest.mock("@/src/theme/useAppTheme", () => ({
  useAppTheme: () => ({
    colors: {
      surface: "white",
      border: "gray",
      text: "black",
      textMuted: "gray",
      accent: "blue",
      onAccent: "white",
    },
  }),
}));
const enable = jest.fn().mockResolvedValue(undefined);
const disable = jest.fn().mockResolvedValue(undefined);
const refresh = jest.fn().mockResolvedValue(undefined);
beforeEach(() => {
  jest.clearAllMocks();
});

test("settings explains quiet results and makes permission enable an explicit button", async () => {
  jest.mocked(useNotifications).mockReturnValue({
    status: "disabled",
    enabled: false,
    busy: false,
    message: null,
    enable,
    disable,
    refresh,
  });
  const screen = await render(<NotificationSettings />);
  expect(
    screen.getByText(
      /Get alerts for decisions and problems\. Suggestions and successful tasks stay quiet\./,
    ),
  ).toBeTruthy();
  expect(enable).not.toHaveBeenCalled();
  await fireEvent.press(
    screen.getByRole("button", { name: "Enable notifications" }),
  );
  expect(enable).toHaveBeenCalledTimes(1);
});

test("missing setup is not shown as enabled and cannot request permission", async () => {
  jest.mocked(useNotifications).mockReturnValue({
    status: "unavailable",
    enabled: false,
    busy: false,
    message: "앱 설정이 아직 준비되지 않았어요.",
    enable,
    disable,
    refresh,
  });
  const screen = await render(<NotificationSettings />);
  expect(screen.getByText("Notifications unavailable")).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", { name: "Enable notifications" }),
  );
  expect(enable).not.toHaveBeenCalled();
  expect(screen.queryByText("Notifications on")).toBeNull();
});
