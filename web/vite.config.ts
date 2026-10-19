import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// allowedHosts: el túnel de Cloudflare entra con un Host aleatorio (*.trycloudflare.com)
export default defineConfig({ plugins: [react()], base: "./", preview: { allowedHosts: true } });
