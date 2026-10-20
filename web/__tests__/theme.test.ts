import { describe, it, expect } from "vitest";
import fs from "fs";
import path from "path";

// Helper: Calculate WCAG relative luminance and contrast ratio
export function hexToRgb(hex: string): [number, number, number] {
  const cleanHex = hex.replace("#", "");
  const expanded =
    cleanHex.length === 3
      ? cleanHex
          .split("")
          .map((c) => c + c)
          .join("")
      : cleanHex;
  const num = parseInt(expanded, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

export function relativeLuminance([r, g, b]: [number, number, number]): number {
  const [rl, gl, bl] = [r, g, b].map((val) => {
    const s = val / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl;
}

export function getContrastRatio(hex1: string, hex2: string): number {
  const l1 = relativeLuminance(hexToRgb(hex1));
  const l2 = relativeLuminance(hexToRgb(hex2));
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

function walkDir(dir: string, extFilter: RegExp): string[] {
  let files: string[] = [];
  if (!fs.existsSync(dir)) return files;
  const list = fs.readdirSync(dir);
  for (const item of list) {
    if (item === "node_modules" || item === ".next" || item === ".git") continue;
    const fullPath = path.join(dir, item);
    const stat = fs.statSync(fullPath);
    if (stat.isDirectory()) {
      files = files.concat(walkDir(fullPath, extFilter));
    } else if (extFilter.test(item)) {
      files.push(fullPath);
    }
  }
  return files;
}

describe("Theme and Design System Constraints (Monochrome)", () => {
  const webDir = path.resolve(__dirname, "..");

  // 1. WCAG Contrast Check
  describe("1. WCAG Contrast Ratios", () => {
    const contrastPairs = [
      { name: "Body text on background", fg: "#111111", bg: "#FFFFFF", min: 4.5 },
      { name: "Body text on surface", fg: "#111111", bg: "#FAFAFA", min: 4.5 },
      { name: "Body text on subtle surface", fg: "#111111", bg: "#F2F2F2", min: 4.5 },
      { name: "Secondary text on background", fg: "#5C5C5C", bg: "#FFFFFF", min: 4.5 },
      { name: "Secondary text on surface", fg: "#5C5C5C", bg: "#FAFAFA", min: 4.5 },
      { name: "Secondary text on subtle surface", fg: "#5C5C5C", bg: "#F2F2F2", min: 4.5 },
      { name: "Button text on primary", fg: "#FFFFFF", bg: "#111111", min: 4.5 },
      { name: "Links on background", fg: "#111111", bg: "#FFFFFF", min: 4.5 },
      { name: "Links on surface", fg: "#111111", bg: "#FAFAFA", min: 4.5 },
      { name: "Success chip text on soft background", fg: "#1D7A4E", bg: "#E9F5EE", min: 4.5 },
      { name: "Warning chip text on soft background", fg: "#8A5300", bg: "#FFF4E0", min: 4.5 },
      { name: "Danger chip text on soft background", fg: "#B42318", bg: "#FDECEA", min: 4.5 },
      { name: "Info chip text on soft background", fg: "#2B5FB4", bg: "#EDF2FB", min: 4.5 },
      { name: "Neutral chip text on neutral background", fg: "#5C5C5C", bg: "#F2F2F2", min: 4.5 },
      { name: "Focus ring on background", fg: "#111111", bg: "#FFFFFF", min: 3.0 },
      { name: "Focus ring on surface", fg: "#111111", bg: "#FAFAFA", min: 3.0 },
    ];

    contrastPairs.forEach(({ name, fg, bg, min }) => {
      it(`verifies contrast for ${name} (${fg} on ${bg}) >= ${min}:1`, () => {
        const ratio = getContrastRatio(fg, bg);
        expect(ratio).toBeGreaterThanOrEqual(min);
      });
    });
  });

  // 2. No raw colors outside web/styles/themes/
  describe("2. No Raw Colors in App or Components", () => {
    it("fails if raw hex, rgb, rgba, or hsl values appear in web/app or web/components", () => {
      const targetDirs = [path.join(webDir, "app"), path.join(webDir, "components")];
      const targetFiles: string[] = [];
      targetDirs.forEach((dir) => {
        targetFiles.push(...walkDir(dir, /\.(tsx|ts|css)$/));
      });

      const rawColorRegex = /(#[0-9a-fA-F]{3,8}\b|\brgba?\s*\([^)]+\)|\bhsla?\s*\([^)]+\))/g;
      const violations: string[] = [];

      for (const filePath of targetFiles) {
        const content = fs.readFileSync(filePath, "utf8");
        const lines = content.split("\n");
        lines.forEach((line, idx) => {
          // Ignore comments
          const trimmed = line.trim();
          if (trimmed.startsWith("//") || trimmed.startsWith("/*") || trimmed.startsWith("*")) {
            return;
          }
          const matches = line.match(rawColorRegex);
          if (matches) {
            const relPath = path.relative(webDir, filePath).replace(/\\/g, "/");
            violations.push(`${relPath}:${idx + 1}: ${matches.join(", ")} in "${trimmed}"`);
          }
        });
      }

      expect(violations).toEqual([]);
    });
  });

  // 3. Archive unused: fail if anything imports web/styles/themes/staffingly-inc/ or teal/
  describe("3. Archive Unused Themes", () => {
    it("fails if any file imports or references archived themes (staffingly-inc or teal)", () => {
      const allFiles = walkDir(webDir, /\.(tsx|ts|css|html|js|mjs)$/).filter((f) => {
        const rel = path.relative(webDir, f).replace(/\\/g, "/");
        return (
          !rel.startsWith("styles/themes/staffingly-inc/") &&
          !rel.startsWith("styles/themes/teal/") &&
          rel !== "__tests__/theme.test.ts"
        );
      });

      const importRegex = /(?:import\s+.*['"].*themes\/(?:staffingly-inc|teal).*['"]|@import\s+['"].*themes\/(?:staffingly-inc|teal).*['"])/i;
      const violations: string[] = [];

      for (const filePath of allFiles) {
        const content = fs.readFileSync(filePath, "utf8");
        if (importRegex.test(content)) {
          const relPath = path.relative(webDir, filePath).replace(/\\/g, "/");
          violations.push(relPath);
        }
      }

      expect(violations).toEqual([]);
    });
  });

  // 4. No company name: fail if 'staffingly' appears in web/app, web/components, or web/lib
  describe("4. No Company Name in UI", () => {
    it("fails if 'staffingly' (case-insensitive) appears in web/app, web/components, or web/lib", () => {
      const targetDirs = [
        path.join(webDir, "app"),
        path.join(webDir, "components"),
        path.join(webDir, "lib"),
      ];
      const targetFiles: string[] = [];
      targetDirs.forEach((dir) => {
        targetFiles.push(...walkDir(dir, /\.(tsx|ts|css)$/));
      });

      const violations: string[] = [];
      const staffinglyRegex = /staffingly/i;

      for (const filePath of targetFiles) {
        const content = fs.readFileSync(filePath, "utf8");
        const lines = content.split("\n");
        lines.forEach((line, idx) => {
          const trimmed = line.trim();
          if (staffinglyRegex.test(line)) {
            const relPath = path.relative(webDir, filePath).replace(/\\/g, "/");
            violations.push(`${relPath}:${idx + 1}: "${trimmed}"`);
          }
        });
      }

      expect(violations).toEqual([]);
    });
  });
});
