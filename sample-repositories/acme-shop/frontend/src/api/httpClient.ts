import { refreshAccessToken, clearSession } from "../auth/sessionManager";

const BASE_URL = "/api";

export interface ApiOptions extends RequestInit {
  skipAuthRetry?: boolean;
}

/**
 * Calls the backend. When the server answers 401 the call is retried exactly once
 * after silently obtaining a new access token; a second 401 signs the user out,
 * which prevents endless retry loops.
 */
export async function apiFetch(path: string, options: ApiOptions = {}): Promise<Response> {
  const token = sessionStorage.getItem("access_token");
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(BASE_URL + path, { ...options, headers });
  if (response.status !== 401 || options.skipAuthRetry) return response;
  try {
    await refreshAccessToken();
  } catch {
    clearSession();
    throw new Error("session expired");
  }
  return apiFetch(path, { ...options, skipAuthRetry: true });
}

export class ApiClient {
  async getJson<T>(path: string): Promise<T> {
    const res = await apiFetch(path);
    if (!res.ok) throw new Error(`request failed: ${res.status}`);
    return res.json() as Promise<T>;
  }
}
