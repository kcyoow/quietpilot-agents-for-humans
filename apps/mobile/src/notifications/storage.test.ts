import * as SecureStore from "expo-secure-store";

import { preferenceKey } from "./helpers";
import { notificationStorage } from "./storage";

jest.mock("react-native-get-random-values", () => ({}));
const secure = jest.mocked(SecureStore);
beforeEach(() => jest.clearAllMocks());

test("enabled preference is securely bound to exact owner and stores no token", async () => {
  await notificationStorage.setEnabled("tenant|owner ", true);
  expect(secure.setItemAsync).toHaveBeenCalledWith(
    preferenceKey("tenant|owner "),
    JSON.stringify({ owner: "tenant|owner ", enabled: true }),
  );
  secure.getItemAsync.mockResolvedValue(
    JSON.stringify({ owner: "tenant|owner", enabled: true }),
  );
  expect(await notificationStorage.enabled("tenant|owner ")).toBe(false);
  secure.getItemAsync.mockResolvedValue(
    JSON.stringify({ owner: "tenant|owner ", enabled: true }),
  );
  expect(await notificationStorage.enabled("tenant|owner ")).toBe(true);
});

test("stable device ID is reused without storing a push token", async () => {
  secure.getItemAsync.mockResolvedValue("qp-device-0123456789abcdef");
  expect(await notificationStorage.deviceId()).toBe(
    "qp-device-0123456789abcdef",
  );
  expect(await notificationStorage.deviceId()).toBe(
    "qp-device-0123456789abcdef",
  );
  expect(secure.setItemAsync).not.toHaveBeenCalled();
});
