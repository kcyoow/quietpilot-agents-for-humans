import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Redirect, Tabs } from "expo-router";
import { useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useAuth } from "@/src/auth/AuthProvider";
import { WORK_AREAS } from "@/src/navigation/workAreas";
import { useAppTheme } from "@/src/theme/useAppTheme";

export default function TabLayout() {
  const { colors, scheme } = useAppTheme();
  const { status, user } = useAuth();
  const { bottom } = useSafeAreaInsets();
  const { fontScale } = useWindowDimensions();

  if (status === "ready" && !user) {
    return <Redirect href="/" />;
  }

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        sceneStyle: { backgroundColor: colors.background },
        tabBarActiveTintColor: colors.accent,
        tabBarBadgeStyle: {
          backgroundColor: colors.accent,
          color: colors.onAccent,
          fontSize: 10,
        },
        tabBarInactiveTintColor: colors.textMuted,
        tabBarLabelStyle: { fontSize: 12, fontWeight: "600", marginTop: 2 },
        tabBarStyle: {
          backgroundColor:
            scheme === "dark" ? colors.surfaceRaised : colors.surface,
          borderTopColor: colors.border,
          elevation: 0,
          height: 70 + bottom + Math.ceil(14 * Math.max(0, fontScale - 1)),
          paddingBottom: 9 + bottom,
          paddingTop: 7,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          tabBarIcon: ({ color, size }) => (
            <MaterialCommunityIcons
              color={color}
              name="clipboard-check-outline"
              size={size}
            />
          ),
          title: WORK_AREAS[0].label,
        }}
      />
      <Tabs.Screen
        name="suggestions"
        options={{
          tabBarIcon: ({ color, size }) => (
            <MaterialCommunityIcons
              color={color}
              name="progress-check"
              size={size}
            />
          ),
          title: WORK_AREAS[1].label,
        }}
      />
    </Tabs>
  );
}
