import path from "node:path"
import { fileURLToPath } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

const rootDir = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, path.resolve(rootDir, ".."), "")
  const apiTarget = (
    process.env.CRM_API_BASE_URL ||
    env.CRM_API_BASE_URL ||
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "")
  const apiToken =
    process.env.CRM_API_TOKEN ||
    process.env.VITE_CRM_API_TOKEN ||
    env.CRM_API_TOKEN ||
    env.VITE_CRM_API_TOKEN ||
    ""

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(rootDir, "./src"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (requestPath) => requestPath.replace(/^\/api/, ""),
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyReq) => {
              if (apiToken) {
                proxyReq.setHeader("X-CRM-Token", apiToken)
              }
            })
          },
        },
      },
    },
  }
})
