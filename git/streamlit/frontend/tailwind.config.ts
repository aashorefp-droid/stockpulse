import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#0d1117",
        card:    "#161b22",
        border:  "#30363d",
        accent:  "#58a6ff",
        green:   "#00e5a0",
        red:     "#ff4d4f",
        yellow:  "#f5c842",
        muted:   "#8b949e",
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
