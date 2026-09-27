const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export type ApiError = { detail: string };

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("ks_token");
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem("ks_token", token);
  else localStorage.removeItem("ks_token");
}

export async function api<T>(
  path: string,
  options: RequestInit & { json?: unknown } = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const { json, ...rest } = options;
  const res = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });

  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }

  if (!res.ok) {
    const detail =
      (data as ApiError | null)?.detail ||
      (data as { message?: string } | null)?.message ||
      `Request failed (${res.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export { API_URL };

/**
 * Multipart form upload with auth — used by the vision endpoints
 * (frame recognition, reference-image onboarding). Unlike `api()`, this
 * must NOT set Content-Type: the browser supplies the multipart boundary.
 */
export async function apiForm<T>(
  path: string,
  form: FormData,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_URL}${path}`, { ...options, headers, body: form });

  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }

  if (!res.ok) {
    const detail =
      (data as ApiError | null)?.detail ||
      (data as { message?: string } | null)?.message ||
      `Request failed (${res.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}
