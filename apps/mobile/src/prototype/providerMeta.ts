import type { PrototypeProvider } from "@/src/prototype/types";

export const PROTOTYPE_PROVIDER_ORDER: PrototypeProvider[] = [
  "google",
  "smartthings",
  "sms",
];

export const PROTOTYPE_PROVIDER_META: Record<
  PrototypeProvider,
  {
    description: string;
    executionScope: string;
    icon: string;
    label: string;
    readScope: string;
  }
> = {
  google: {
    description: "Read Gmail",
    executionScope: "No mail sending or Calendar creation permission",
    icon: "google",
    label: "Google",
    readScope: "Gmail from the last 7 days and new mail",
  },
  smartthings: {
    description: "Device status and controls",
    executionScope: "Device controls will require connection and approval",
    icon: "home-automation",
    label: "SmartThings",
    readScope: "Device status and capabilities when supported",
  },
  sms: {
    description: "Events and reminders from SMS",
    executionScope: "Local reminders only within approved rules",
    icon: "message-text-clock-outline",
    label: "SMS",
    readScope: "Analyze only permitted messages on the device",
  },
};

export function prototypeProviderLabel(provider: PrototypeProvider): string {
  return PROTOTYPE_PROVIDER_META[provider].label;
}
