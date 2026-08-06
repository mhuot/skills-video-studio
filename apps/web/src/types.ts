export const phases = [
  "prepare",
  "narrate",
  "measure",
  "compose",
  "validate",
  "snapshot",
  "render",
  "qa",
] as const;

export type Phase = (typeof phases)[number];
export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancel_requested"
  | "canceled";

export interface Project {
  id: string;
  name: string;
  recipe: string;
  path: string;
  created_at: string;
}

export interface PhaseState {
  phase: Phase;
  status: JobStatus | null;
  job_id: string | null;
  updated_at: string | null;
}

export interface ProjectDetail extends Project {
  phases: PhaseState[];
}

export interface Job {
  id: string;
  project_id: string;
  phase: Phase;
  status: JobStatus;
  command: string[];
  engine_version: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  error: string | null;
}

export interface JobEvent {
  id: number;
  job_id: string;
  kind: string;
  message: string;
  created_at: string;
}

export interface Artifact {
  path: string;
  name: string;
  size: number;
  modified_at: string;
}

export interface Health {
  status: string;
  engine_version: string;
  platform: string;
}

export interface Recipe {
  id: string;
  label: string;
  phases: Phase[];
}
