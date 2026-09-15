export type AppColorScheme = "light" | "dark";
export type AppPalette = "blue" | "coral";
export type AppearanceMode = "system" | AppColorScheme;

const lightBase = {
  background: "#F7F8FA",
  border: "#D7DCE4",
  danger: "#AD3937",
  dangerSoft: "#FCEDEA",
  onAccent: "#FFFFFF",
  success: "#276746",
  successSoft: "#EAF4ED",
  surface: "#FFFFFF",
  surfaceMuted: "#EEF0F4",
  surfaceRaised: "#FFFFFF",
  text: "#19212D",
  textMuted: "#515D6D",
  textSubtle: "#586474",
  warning: "#805318",
  warningBorder: "#DEC69E",
  warningSoft: "#FBF1DF",
};

const darkBase = {
  background: "#10151D",
  border: "#354051",
  danger: "#FFAAA5",
  dangerSoft: "#3D2227",
  onAccent: "#102341",
  success: "#8AD8AB",
  successSoft: "#183329",
  surface: "#19212C",
  surfaceMuted: "#232D3A",
  surfaceRaised: "#202A37",
  text: "#F0F3F8",
  textMuted: "#BEC8D6",
  textSubtle: "#B4C0D1",
  warning: "#EFCA8A",
  warningBorder: "#756040",
  warningSoft: "#392F20",
};

export type AppColors = Record<
  keyof typeof lightBase | "accent" | "accentBorder" | "accentSoft",
  string
>;

export const themeColors: Record<
  AppPalette,
  Record<AppColorScheme, AppColors>
> = {
  blue: {
    light: {
      ...lightBase,
      accent: "#2856C7",
      accentBorder: "#BACAF0",
      accentSoft: "#EDF2FF",
    },
    dark: {
      ...darkBase,
      accent: "#9DBEFF",
      accentBorder: "#4D6590",
      accentSoft: "#233553",
    },
  },
  coral: {
    light: {
      ...lightBase,
      accent: "#AD493B",
      accentBorder: "#DDBAB2",
      accentSoft: "#FAEFEB",
      background: "#FAF8F5",
      border: "#DDD8D3",
      surfaceMuted: "#F0ECE8",
      text: "#2C2625",
      textMuted: "#675852",
      textSubtle: "#6A5B55",
    },
    dark: {
      ...darkBase,
      accent: "#F2AA99",
      accentBorder: "#956A60",
      accentSoft: "#442F2C",
      background: "#1B1717",
      border: "#4A3D3A",
      onAccent: "#36201C",
      surface: "#261F1E",
      surfaceMuted: "#332926",
      surfaceRaised: "#302624",
      text: "#F8F0EB",
      textMuted: "#D2C1B9",
      textSubtle: "#C9B9B1",
    },
  },
};

// The default palette also works in isolated components without a provider.
export const appColors = themeColors.blue;
