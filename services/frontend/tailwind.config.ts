import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#15202b",
          muted: "#3d4f61",
          faint: "#6b7c8d",
        },
        paper: {
          DEFAULT: "#f6f1e8",
          raised: "#fffdf8",
        },
        copper: {
          DEFAULT: "#b45309",
          dark: "#92400e",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "ui-serif", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};

export default config;
