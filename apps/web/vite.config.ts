import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // A running browser may still request chunks from the previous build.
  // Content hashes keep releases isolated while preserving those open pages.
  build: { emptyOutDir: false },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": process.env.LOCAL_DRAMA_API_PROXY ?? "http://127.0.0.1:3210",
    },
  },
});
