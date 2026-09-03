// Only the website derives this identifier; Oracle stores/enforces admissions.
// Never accept a browser-supplied quota key. This is not authenticated identity.
export async function networkKey(clientIp: string, token: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(token), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(`deep-analysis-network:${clientIp}`));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
