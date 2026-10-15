import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

const MARKDOWN_PKGS =
  /node_modules\/(react-markdown|remark-|micromark|mdast-|hast-|unified|unist-|vfile|property-information|decode-named-character-reference|character-entities|comma-separated-tokens|space-separated-tokens|devlop|bail|trough|zwitch|ccount|markdown-table|longest-streak|html-url-attributes|estree-util|style-to-js|style-to-object|inline-style-parser|trim-lines|is-plain-obj)/;

// The SPA calls Cloud Run directly (VITE_API_BASE); there is no /api proxy on purpose (SPEC §2, R4).
export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ""), ...process.env };
  const port = Number(env.VITE_PORT || 5173);
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: { port, strictPort: true },
    preview: { port: port + 1000, strictPort: true },
    build: {
      target: "es2022",
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (!id.includes("node_modules")) return undefined;
            if (id.includes("/firebase/") || id.includes("/@firebase/")) return "firebase";
            if (MARKDOWN_PKGS.test(id)) return "markdown";
            return "vendor";
          },
        },
      },
    },
  };
});
