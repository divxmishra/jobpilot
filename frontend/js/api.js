// API client: one place for fetch, auth handling, errors and task polling.

export class ApiError extends Error {
  /** @param {number} status @param {string} message */
  constructor(status, message) { super(message); this.status = status; }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

/**
 * @param {string} path
 * @param {{method?: string, body?: any, form?: FormData, signal?: AbortSignal}} [opts]
 */
export async function api(path, opts = {}) {
  const headers = { "X-Requested-With": "JobPilot", Accept: "application/json" };
  let body;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  let res;
  try {
    res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, navigator.onLine ? "Cannot reach the JobPilot server. Is it running?" : "You appear to be offline.");
  }
  if (res.status === 401 && !path.startsWith("/api/auth")) { onUnauthorized(); throw new ApiError(401, "Please sign in"); }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return data;
}

export const get = (p, o) => api(p, o);
export const post = (p, body) => api(p, { method: "POST", body: body ?? {} });
export const put = (p, body) => api(p, { method: "PUT", body });
export const patch = (p, body) => api(p, { method: "PATCH", body });
export const del = (p) => api(p, { method: "DELETE" });

/** Upload with progress (XHR) */
export function upload(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData(); form.append("file", file);
    xhr.open("POST", path); xhr.setRequestHeader("X-Requested-With", "JobPilot");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null; try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else { if (xhr.status === 401) onUnauthorized(); reject(new ApiError(xhr.status, data?.detail || `Upload failed (${xhr.status})`)); }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.send(form);
  });
}

/**
 * Poll a background task until it finishes.
 * @param {string} id
 * @param {(t:any)=>void} [onUpdate]
 */
export async function waitTask(id, onUpdate, { interval = 900, timeout = 15 * 60 * 1000 } = {}) {
  const start = Date.now();
  for (;;) {
    const t = await get(`/api/tasks/${id}`);
    onUpdate?.(t);
    if (!["queued", "running"].includes(t.status)) return t;
    if (Date.now() - start > timeout) throw new ApiError(408, "The task is taking longer than expected; check Activity & Logs.");
    await new Promise((r) => setTimeout(r, interval));
  }
}
