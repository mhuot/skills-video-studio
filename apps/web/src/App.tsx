import {
  Box,
  Check,
  Circle,
  FileVideo,
  FolderOpen,
  OctagonX,
  Play,
  Plus,
  Radio,
  ScrollText,
  Server,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, subscribeToJob } from "./api";
import {
  phases,
  type Artifact,
  type Health,
  type Job,
  type JobEvent,
  type JobStatus,
  type Phase,
  type Project,
  type ProjectDetail,
  type Recipe,
} from "./types";

const phaseCopy: Record<
  Phase,
  { label: string; description: string; command: string }
> = {
  prepare: {
    label: "Prepare",
    description: "Verify the project manifest, recipe, source files, and output directories.",
    command: "studio-internal prepare",
  },
  narrate: {
    label: "Narrate",
    description:
      "Generate narration segments with the engine's resumable tts-batch tool; unchanged segments are skipped on rerun.",
    command: "tts-batch narration.json --output-dir video/assets/audio/segments",
  },
  measure: {
    label: "Measure",
    description: "Measure narration and derive scene boundaries from the resulting durations.",
    command: "python tools/measure_audio.py",
  },
  compose: {
    label: "Compose",
    description: "Check the browser composition and narration-derived timeline.",
    command: "hyperframes check video",
  },
  validate: {
    label: "Validate",
    description: "Run runtime, layout, motion, contrast, and offline validation.",
    command: "hyperframes validate video",
  },
  snapshot: {
    label: "Snapshot",
    description: "Render representative frames for visual review before the final encode.",
    command: "hyperframes snapshot video --output production/snapshots",
  },
  render: {
    label: "Render",
    description: "Encode the approved composition using the measured production timeline.",
    command: "hyperframes render video --output production/renders/master.mp4",
  },
  qa: {
    label: "QA",
    description: "Inspect media properties and assemble the final production evidence.",
    command: "ffprobe -v error -show_format -show_streams production/renders/master.mp4",
  },
};

function statusLabel(status: JobStatus | null): string {
  if (!status) return "Not run";
  return status.replaceAll("_", " ");
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [selectedPhase, setSelectedPhase] = useState<Phase>("prepare");
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [tab, setTab] = useState<"logs" | "artifacts" | "evidence">("logs");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isCreatingJob, setIsCreatingJob] = useState(false);
  const jobSubscription = useRef<(() => void) | null>(null);
  const selectedProjectId = useRef<string | null>(null);
  const operationGeneration = useRef(0);

  const refreshProject = useCallback(async (projectId: string) => {
    const [detail, projectArtifacts] = await Promise.all([
      api.project(projectId),
      api.artifacts(projectId),
    ]);
    if (selectedProjectId.current === projectId) {
      setProject(detail);
      setArtifacts(projectArtifacts);
    }
    return detail;
  }, []);

  useEffect(() => {
    void Promise.all([api.projects(), api.health(), api.recipes()])
      .then(async ([nextProjects, nextHealth, nextRecipes]) => {
        setProjects(nextProjects);
        setHealth(nextHealth);
        setRecipes(nextRecipes);
        if (nextProjects[0]) {
          selectedProjectId.current = nextProjects[0].id;
          await refreshProject(nextProjects[0].id);
        }
      })
      .catch((nextError: unknown) => {
        setError(nextError instanceof Error ? nextError.message : "Unable to load Studio");
      });
  }, [refreshProject]);

  useEffect(() => () => jobSubscription.current?.(), []);

  const phaseState = useMemo(
    () => project?.phases.find((item) => item.phase === selectedPhase) ?? null,
    [project, selectedPhase],
  );

  async function selectProject(nextProject: Project) {
    operationGeneration.current += 1;
    jobSubscription.current?.();
    jobSubscription.current = null;
    selectedProjectId.current = nextProject.id;
    setError(null);
    setEvents([]);
    setActiveJob(null);
    setIsCreatingJob(false);
    await refreshProject(nextProject.id);
  }

  async function createProject(name: string, recipe: string) {
    operationGeneration.current += 1;
    jobSubscription.current?.();
    jobSubscription.current = null;
    setEvents([]);
    setActiveJob(null);
    setIsCreatingJob(false);
    setError(null);
    try {
      const created = await api.createProject(name, recipe);
      const nextProjects = [created, ...projects];
      setProjects(nextProjects);
      selectedProjectId.current = created.id;
      await refreshProject(created.id);
      setSelectedPhase("prepare");
      setShowCreate(false);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Unable to create project");
    }
  }

  async function runPhase() {
    if (!project) return;
    const projectId = project.id;
    const generation = operationGeneration.current + 1;
    operationGeneration.current = generation;
    setIsCreatingJob(true);
    setError(null);
    setEvents([]);
    jobSubscription.current?.();
    let job: Job;
    try {
      job = await api.createJob(projectId, selectedPhase);
    } catch (nextError) {
      if (operationGeneration.current === generation) {
        setError(nextError instanceof Error ? nextError.message : "Unable to create job");
        setIsCreatingJob(false);
      }
      return;
    }
    if (
      operationGeneration.current !== generation ||
      selectedProjectId.current !== projectId
    ) {
      return;
    }
    setIsCreatingJob(false);
    setActiveJob(job);
    jobSubscription.current = subscribeToJob(
      job.id,
      (event) => {
        if (operationGeneration.current === generation) {
          setEvents((current) => [...current, event]);
        }
      },
      (completeJob) => {
        if (operationGeneration.current !== generation) return;
        jobSubscription.current = null;
        setActiveJob(completeJob);
        if (selectedProjectId.current === completeJob.project_id) {
          void refreshProject(completeJob.project_id);
        }
      },
      () => {
        if (operationGeneration.current === generation) {
          void api.job(job.id).then((currentJob) => {
            if (operationGeneration.current === generation) setActiveJob(currentJob);
          });
        }
      },
    );
  }

  async function cancelPhase() {
    if (!activeJob) return;
    setActiveJob(await api.cancelJob(activeJob.id));
    if (project) await refreshProject(project.id);
  }

  const displayedStatus =
    activeJob?.phase === selectedPhase ? activeJob.status : phaseState?.status ?? null;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">SV</div>
          <div>
            <strong>Skills Video</strong>
            <span>Studio</span>
          </div>
        </div>
        <button className="primary wide" onClick={() => setShowCreate(true)}>
          <Plus size={16} /> New project
        </button>
        <nav>
          <p className="nav-label">Projects</p>
          <div className="project-list">
            {projects.map((item) => (
              <button
                className={`project-link ${project?.id === item.id ? "active" : ""}`}
                key={item.id}
                onClick={() => void selectProject(item)}
              >
                <FileVideo size={17} />
                <span>
                  <strong>{item.name}</strong>
                  <small>{item.recipe}</small>
                </span>
              </button>
            ))}
          </div>
        </nav>
        <div className="engine-card">
          <div className="engine-title">
            <span className="status-dot" />
            Engine connected
          </div>
          <div><span>Version</span><strong>{health?.engine_version ?? "—"}</strong></div>
          <div><span>Platform</span><strong>{health?.platform ?? "—"}</strong></div>
          <div><span>Mode</span><strong>Offline</strong></div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <div>
            <p>Projects / {project?.recipe ?? "Studio"}</p>
            <h1>{project?.name ?? "Create your first project"}</h1>
          </div>
          <div className="top-actions">
            <button className="secondary"><FolderOpen size={16} /> Project files</button>
            <button className="secondary"><ScrollText size={16} /> Production record</button>
            <button
              className="primary"
              disabled={
                !project ||
                isCreatingJob ||
                ["queued", "running"].includes(activeJob?.status ?? "")
              }
              onClick={() => void runPhase()}
            >
              <Play size={16} /> {isCreatingJob ? "Queuing phase" : "Run selected phase"}
            </button>
          </div>
        </header>

        <div className="content">
          {error && <div className="error-banner"><OctagonX size={18} />{error}</div>}
          {!project ? (
            <section className="empty-state card">
              <Box size={42} />
              <h2>No video projects yet</h2>
              <p>Create a project to begin the staged production workflow.</p>
              <button className="primary" onClick={() => setShowCreate(true)}>
                <Plus size={16} /> Create project
              </button>
            </section>
          ) : (
            <>
              <section className="pipeline-card">
                <div className="pipeline-heading">
                  <strong>Production pipeline</strong>
                  <span>Each phase is independently rerunnable and auditable.</span>
                </div>
                <div className="pipeline">
                  {phases.map((phase, index) => {
                    const state = project.phases.find((item) => item.phase === phase);
                    const status =
                      activeJob?.phase === phase ? activeJob.status : state?.status ?? null;
                    return (
                      <button
                        key={phase}
                        className={`phase ${selectedPhase === phase ? "selected" : ""}`}
                        onClick={() => setSelectedPhase(phase)}
                      >
                        <div className="phase-top">
                          <span>{String(index + 1).padStart(2, "0")}</span>
                          <StatusIcon status={status} />
                        </div>
                        <strong>{phaseCopy[phase].label}</strong>
                        <small>{statusLabel(status)}</small>
                      </button>
                    );
                  })}
                </div>
              </section>

              <div className="workspace">
                <section className="card preview-card">
                  <div className="card-header">
                    <div>
                      <h2>{phaseCopy[selectedPhase].label} workspace</h2>
                      <p>{statusLabel(displayedStatus)}</p>
                    </div>
                    <button className="secondary">Open full size</button>
                  </div>
                  <div className="preview-wrap">
                    <div className="preview">
                      <div className="preview-copy">
                        <div className="eyebrow">One production environment</div>
                        <h3>Pull once.<br />Run every phase.</h3>
                        <p>Narration, browser rendering, encoding, fonts, and media checks stay pinned inside one portable engine.</p>
                      </div>
                      <div className="preview-stack">
                        <StackItem label="Kokoro narration" icon="K" />
                        <StackItem label="HyperFrames render" icon="H" active />
                        <StackItem label="FFmpeg encode" icon="F" />
                      </div>
                    </div>
                  </div>
                  <div className="tabs">
                    {(["logs", "artifacts", "evidence"] as const).map((item) => (
                      <button
                        className={tab === item ? "active" : ""}
                        key={item}
                        onClick={() => setTab(item)}
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                  <div className="tab-panel">
                    {tab === "logs" && <LogPanel events={events} activeJob={activeJob} />}
                    {tab === "artifacts" && (
                      <ArtifactPanel artifacts={artifacts} projectId={project.id} />
                    )}
                    {tab === "evidence" && (
                      <EvidencePanel health={health} project={project} activeJob={activeJob} />
                    )}
                  </div>
                </section>

                <aside className="details">
                  <section className="card">
                    <div className="card-header">
                      <div><h2>{phaseCopy[selectedPhase].label}</h2><p>Selected phase</p></div>
                    </div>
                    <div className="detail-body">
                      <div className={`status-pill status-${displayedStatus ?? "idle"}`}>
                        <StatusIcon status={displayedStatus} />
                        {statusLabel(displayedStatus)}
                      </div>
                      <p>{phaseCopy[selectedPhase].description}</p>
                      <dl>
                        <div><dt>Recipe</dt><dd>{project.recipe}</dd></div>
                        <div><dt>Engine</dt><dd>{health?.engine_version ?? "—"}</dd></div>
                        <div><dt>Worker</dt><dd>engine-api-01</dd></div>
                      </dl>
                      <pre>{activeJob?.phase === selectedPhase
                        ? activeJob.command.join(" ")
                        : phaseCopy[selectedPhase].command}</pre>
                      {activeJob?.phase === selectedPhase &&
                        ["queued", "running", "cancel_requested"].includes(activeJob.status) && (
                          <button className="secondary" onClick={() => void cancelPhase()}>
                            <X size={16} /> Cancel phase
                          </button>
                        )}
                    </div>
                  </section>
                  <section className="card">
                    <div className="card-header">
                      <div><h2>Execution boundary</h2><p>Sidecar deployment</p></div>
                    </div>
                    <div className="detail-body boundary">
                      <div><Server size={17} /><span><strong>Studio web</strong><small>Browser-facing UI</small></span></div>
                      <div><Radio size={17} /><span><strong>Engine API</strong><small>Trusted job runner</small></span></div>
                      <div><Box size={17} /><span><strong>/workspace</strong><small>Project artifacts</small></span></div>
                    </div>
                  </section>
                </aside>
              </div>
            </>
          )}
        </div>
      </main>

      {showCreate && (
        <CreateProjectDialog
          recipes={recipes}
          onClose={() => setShowCreate(false)}
          onCreate={(name, recipe) => void createProject(name, recipe)}
        />
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: JobStatus | null }) {
  if (status === "succeeded") return <Check className="status-success" size={16} />;
  if (status === "running" || status === "queued") return <Radio className="status-running" size={16} />;
  if (status === "failed" || status === "canceled") return <OctagonX className="status-danger" size={16} />;
  return <Circle className="status-idle" size={14} />;
}

function StackItem({ label, icon, active = false }: { label: string; icon: string; active?: boolean }) {
  return <div className={`stack-item ${active ? "active" : ""}`}><b>{icon}</b>{label}</div>;
}

function LogPanel({ events, activeJob }: { events: JobEvent[]; activeJob: Job | null }) {
  return (
    <div className="log-panel">
      {events.length === 0 ? (
        <span className="muted">Run the selected phase to stream its command and logs.</span>
      ) : events.map((event) => (
        <div key={event.id}>
          <time>{new Date(event.created_at).toLocaleTimeString()}</time>
          <span className={`event-${event.kind}`}>{event.message}</span>
        </div>
      ))}
      {activeJob?.error && <div><time>error</time><span className="event-error">{activeJob.error}</span></div>}
    </div>
  );
}

function ArtifactPanel({ artifacts, projectId }: { artifacts: Artifact[]; projectId: string }) {
  if (artifacts.length === 0) return <p className="muted">No production artifacts yet.</p>;
  return <div className="artifact-list">{artifacts.map((artifact) => (
    <div className="artifact" key={artifact.path}>
      <div><strong>{artifact.name}</strong><small>{artifact.path} · {formatBytes(artifact.size)}</small></div>
      <a href={`/api/v1/projects/${projectId}/artifacts/${artifact.path}`} target="_blank" rel="noreferrer">Open</a>
    </div>
  ))}</div>;
}

function EvidencePanel({ health, project, activeJob }: {
  health: Health | null;
  project: ProjectDetail;
  activeJob: Job | null;
}) {
  return <div className="artifact-list">
    <Evidence label="Engine version" value={health?.engine_version ?? "Unknown"} />
    <Evidence label="Platform" value={health?.platform ?? "Unknown"} />
    <Evidence label="Recipe" value={project.recipe} />
    <Evidence label="Exact command" value={activeJob?.command.join(" ") ?? "Select and run a phase"} />
  </div>;
}

function Evidence({ label, value }: { label: string; value: string }) {
  return <div className="artifact"><div><strong>{label}</strong><small>{value}</small></div></div>;
}

function CreateProjectDialog({ recipes, onClose, onCreate }: {
  recipes: Recipe[];
  onClose: () => void;
  onCreate: (name: string, recipe: string) => void;
}) {
  const [name, setName] = useState("");
  const [recipe, setRecipe] = useState(recipes[0]?.id ?? "");
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <form className="dialog" onMouseDown={(event) => event.stopPropagation()} onSubmit={(event) => {
        event.preventDefault();
        onCreate(name, recipe);
      }}>
        <div className="dialog-header">
          <div><h2>Create video project</h2><p>Choose the trusted production recipe.</p></div>
          <button type="button" className="icon-button" onClick={onClose}><X size={18} /></button>
        </div>
        <label>Project name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Customer solution preview" required /></label>
        <label>Recipe<select value={recipe} onChange={(event) => setRecipe(event.target.value)}>
          {recipes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
        <div className="dialog-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" type="submit" disabled={!recipe}>Create project</button></div>
      </form>
    </div>
  );
}

export default App;
