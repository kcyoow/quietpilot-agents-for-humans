import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useMemo } from "react";

import { AuthProvider } from "@/src/auth/AuthProvider";
import { MailInterestProvider } from "@/src/mail/MailInterestProvider";
import { NotificationProvider } from "@/src/notifications/NotificationProvider";
import { PrototypeProvider } from "@/src/prototype/PrototypeProvider";
import { AppThemeProvider, useAppTheme } from "@/src/theme/useAppTheme";
import { WorkspaceProvider } from "@/src/workspace/WorkspaceProvider";

export { ErrorBoundary } from "expo-router";

export const unstable_settings = {
  initialRouteName: "index",
};

export default function RootLayout() {
  return (
    <AppThemeProvider>
      <AppLayout />
    </AppThemeProvider>
  );
}

function AppLayout() {
  const { colors, scheme } = useAppTheme();
  const queryClient = useMemo(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, staleTime: 30_000 },
        },
      }),
    [],
  );
  const navigationTheme = useMemo(
    () => ({
      ...(scheme === "dark" ? DarkTheme : DefaultTheme),
      colors: {
        ...(scheme === "dark" ? DarkTheme.colors : DefaultTheme.colors),
        background: colors.background,
        border: colors.border,
        card: colors.surface,
        primary: colors.accent,
        text: colors.text,
      },
    }),
    [colors, scheme],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <PrototypeProvider>
          <WorkspaceProvider>
            <NotificationProvider>
              <MailInterestProvider>
                <ThemeProvider value={navigationTheme}>
                  <StatusBar style={scheme === "dark" ? "light" : "dark"} />
                  <Stack
                    screenOptions={{ animation: "fade", headerShown: false }}
                  >
                    <Stack.Screen name="index" />
                    <Stack.Screen name="(tabs)" />
                    <Stack.Screen name="oauth-return" />
                    <Stack.Screen name="connections" />
                    <Stack.Screen name="settings" />
                    <Stack.Screen name="password-reset" />
                    <Stack.Screen name="policies" />
                    <Stack.Screen name="routines" />
                    <Stack.Screen name="notifications" />
                    <Stack.Screen name="history" />
                    <Stack.Screen name="mail" />
                    <Stack.Screen name="mail-interests" />
                    <Stack.Screen name="prototype-scenarios" />
                    <Stack.Screen name="suggestions/[groupId]" />
                    <Stack.Screen name="cases/[caseId]" />
                  </Stack>
                </ThemeProvider>
              </MailInterestProvider>
            </NotificationProvider>
          </WorkspaceProvider>
        </PrototypeProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
