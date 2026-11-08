import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build straight into the FastAPI static folder so the server runs without Node:
//   cd frontend && npm install && npm run build   ->  ../static/app
// Dev with hot reload: npm run dev (proxies /api to the FastAPI server on :8000)
export default defineConfig({
  base: "/app/",
  plugins: [react(), tailwindcss()],
  build: { outDir: "../static/app", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
