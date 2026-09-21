import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // A running browser may still request chunks from the previous build.
  // Content hashes keep releases isolated while preserving those open pages.
  build: {
    emptyOutDir: false,
    manifest: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("@xyflow")) return "vendor-flow";
          if (id.includes("@tanstack/react-query")) return "vendor-query";
          if (id.includes("react-router")) return "vendor-router";
          if (id.includes("react-dom") || id.includes("/react/")) return "vendor-react";
          if (id.includes("pinyin-pro")) return "vendor-pinyin";
          return undefined;
        },
      },
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": process.env.LOCAL_DRAMA_API_PROXY ?? "http://127.0.0.1:3210",
    },
  },
});
