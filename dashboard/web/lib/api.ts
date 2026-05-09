const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8090";

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (res.status === 401) {
    // Not authenticated — redirect to login
    if (typeof window !== "undefined") {
      window.location.href = `${API_URL}/auth/login`;
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }
  return res.json() as Promise<T>;
}
