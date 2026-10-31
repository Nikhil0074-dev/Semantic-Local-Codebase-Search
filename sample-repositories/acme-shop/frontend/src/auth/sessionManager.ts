let renewalTimer: ReturnType<typeof setTimeout> | undefined;

export async function refreshAccessToken(): Promise<string> {
  const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
  if (!res.ok) throw new Error("renewal rejected");
  const { access_token } = await res.json();
  sessionStorage.setItem("access_token", access_token);
  return access_token;
}

export function scheduleSilentRenewal(expiresInSeconds: number): void {
  if (renewalTimer) clearTimeout(renewalTimer);
  renewalTimer = setTimeout(() => void refreshAccessToken(), (expiresInSeconds - 60) * 1000);
}

export function clearSession(): void {
  sessionStorage.removeItem("access_token");
  if (renewalTimer) clearTimeout(renewalTimer);
}
