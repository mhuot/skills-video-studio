import type {
  Artifact,
  Health,
  Job,
  JobEvent,
  Phase,
  Project,
  ProjectDetail,
  Recipe,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
  recipes: () => request<Recipe[]>("/api/v1/recipes"),
  projects: () => request<Project[]>("/api/v1/projects"),
  project: (id: string) => request<ProjectDetail>(`/api/v1/projects/${id}`),
  createProject: (name: string, recipe: string) =>
    request<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ name, recipe }),
    }),
  createJob: (projectId: string, phase: Phase) =>
    request<Job>(`/api/v1/projects/${projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({ phase }),
    }),
  job: (id: string) => request<Job>(`/api/v1/jobs/${id}`),
  cancelJob: (id: string) =>
    request<Job>(`/api/v1/jobs/${id}/cancel`, { method: "POST" }),
  artifacts: (projectId: string) =>
    request<Artifact[]>(`/api/v1/projects/${projectId}/artifacts`),
};

export function subscribeToJob(
  jobId: string,
  onEvent: (event: JobEvent) => void,
  onComplete: (job: Job) => void,
  onError: () => void,
): () => void {
  const source = new EventSource(`/api/v1/jobs/${jobId}/events`);
  for (const eventName of ["status", "log", "command", "error"]) {
    source.addEventListener(eventName, (event) => {
      onEvent(JSON.parse((event as MessageEvent<string>).data) as JobEvent);
    });
  }
  source.addEventListener("complete", (event) => {
    onComplete(JSON.parse((event as MessageEvent<string>).data) as Job);
    source.close();
  });
  source.onerror = () => {
    onError();
    source.close();
  };
  return () => source.close();
}
