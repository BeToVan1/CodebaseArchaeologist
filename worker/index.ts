import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";
import { handleAnalyzeRequest } from "./github-analyzer";
import { deepConfigured, handleDeepRequest, type DeepEnv } from "./deep-proxy";
import { handleNetworkProbe, type NetworkProbeEnv } from "./network-probe";

interface Env { ASSETS: { fetch(request: Request): Promise<Response> }; IMAGES: { input(stream: ReadableStream): { transform(options: Record<string, unknown>): { output(options: { format: string; quality: number }): Promise<{ response(): Response }> } } } }
interface ExecutionContext { waitUntil(promise: Promise<unknown>): void; passThroughOnException(): void }

export default {
  async fetch(request: Request, env: Env & DeepEnv & NetworkProbeEnv, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/api/network-probe") return handleNetworkProbe(request, env);
    if (url.pathname === "/api/analyze/deep") return handleDeepRequest(request, env);
    if (url.pathname === "/api/analysis-capabilities") return Response.json(
      { deep: deepConfigured(env) }, { headers: { "Cache-Control": "no-store" } });
    if (url.pathname === "/api/analyze") return handleAnalyzeRequest(request);
    if (url.pathname === "/_vinext/image") {
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES]);
    }
    return handler.fetch(request, env, ctx);
  },
};
