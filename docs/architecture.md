# Architecture

Skills Video Studio is a sidecar control plane for
[`skills-video-engine`](https://github.com/mhuot/skills-video-engine).

```text
Browser
  |
  | HTTP and server-sent events
  v
Studio Web
  |
  | internal container network
  v
Engine API ---- SQLite and logs in /data
  |
  +----------- projects and artifacts in /workspace
  |
  +----------- HyperFrames, Kokoro, FFmpeg, and FFprobe
```

## Security boundary

The browser-facing container has no project volume and no Docker socket. The
engine API accepts only named operations from its trusted recipe registry. It
passes argument arrays directly to child processes without a shell and
canonicalizes every project and artifact path beneath `/workspace`.

Video compositions and project-owned authoring scripts are executable content.
The initial release is therefore a trusted, local single-user application.
Untrusted uploads and multi-user hosting require a later isolated-worker mode
that launches each job in its own container or sandbox.

The frontend discovers this registry through `/api/v1/recipes`; it never
advertises unreleased skills. Adding promo-video or another public skill
requires adding its reviewed adapter to the API, after which the same UI
discovers it automatically.

The API joins only an internal network, preventing the worker from reaching
external networks. The web service also joins a separate edge network so its
published port remains reachable from the host.

## Job lifecycle

Jobs move through:

```text
queued -> running -> succeeded
                  -> failed
       -> cancel_requested -> canceled
```

SQLite stores project metadata, exact commands, engine versions, timestamps,
exit codes, and events. Logs are also written beneath `/data/logs`. Server-sent
events stream new records to the browser.

## Deployment profiles

- `compose.yaml` runs the web and engine API as separate services.
- `compose.all-in-one.yaml` serves the compiled UI from the engine API image.

Both profiles use the same API and frontend build. The all-in-one profile
trades worker network isolation for single-container convenience and must be
used only with trusted local projects.
