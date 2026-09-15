import { themeColors } from "./tokens";

function luminance(hex: string) {
  const channels = [1, 3, 5].map((offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(first: string, second: string) {
  const levels = [luminance(first), luminance(second)];
  return (Math.max(...levels) + 0.05) / (Math.min(...levels) + 0.05);
}

describe.each(["blue", "coral"] as const)("%s palette", (palette) => {
  describe.each(["light", "dark"] as const)("%s readable text", (scheme) => {
    const colors = themeColors[palette][scheme];
    test.each([
      "background",
      "surface",
      "surfaceMuted",
      "surfaceRaised",
      "accentSoft",
    ] as const)("keeps body and secondary text legible on %s", (surface) => {
      for (const text of ["text", "textMuted", "textSubtle"] as const) {
        expect(contrast(colors[text], colors[surface])).toBeGreaterThanOrEqual(
          4.5,
        );
      }
    });
    test("keeps selected actions and status text readable", () => {
      expect(contrast(colors.onAccent, colors.accent)).toBeGreaterThanOrEqual(
        4.5,
      );
      expect(contrast(colors.accent, colors.accentSoft)).toBeGreaterThanOrEqual(
        4.5,
      );
      for (const status of ["danger", "success", "warning"] as const) {
        expect(
          contrast(colors[status], colors[`${status}Soft`]),
        ).toBeGreaterThanOrEqual(4.5);
      }
    });
  });
});
