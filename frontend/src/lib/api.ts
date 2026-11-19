/** Small client for the LegacyLens API. All requests go through the same origin. */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    if (body?.error) {
      return new ApiError(response.status, body.error.code, body.error.message, body.error.details);
    }
  } catch {
    // The body was not JSON, for example a proxy error page.
  }
  if (response.status >= 500) {
    return new ApiError(response.status, "server_error", "The server could not complete this request. Please try again.");
  }
  return new ApiError(response.status, "error", `Request failed with status ${response.status}.`);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...init });
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") throw new ApiError(0, "aborted", "Stopped.");
    throw new ApiError(0, "network", "Could not reach the server. Check your connection and try again.");
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function postJson<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function deleteRequest(path: string, body?: unknown): Promise<void> {
  return request<void>(path, {
    method: "DELETE",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function postForm<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: form });
}

/** Sends a file and reports progress. `fetch` cannot report upload progress. */
export function uploadForm<T>(path: string, form: FormData, onProgress?: (percent: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () => reject(new ApiError(0, "network", "The upload failed. Check your connection and try again."));
    request.onload = () => {
      let body: { error?: { code: string; message: string; details?: Record<string, unknown> } } | null = null;
      try {
        body = JSON.parse(request.responseText);
      } catch {
        body = null;
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(body as T);
      } else if (body?.error) {
        reject(new ApiError(request.status, body.error.code, body.error.message, body.error.details));
      } else {
        reject(new ApiError(request.status, "error", `The upload failed with status ${request.status}.`));
      }
    };
    request.send(form);
  });
}

/** SWR fetcher. */
export const fetcher = <T,>(path: string) => request<T>(path);

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
