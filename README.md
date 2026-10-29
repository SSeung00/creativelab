# CreativeLab

Self-hosted creative tools I am building to replace a commercial creative-suite subscription for my own
photography, video and print work. Each tool is a local web app (FastAPI backend, React frontend) that uses
the GPU when there is one.

| Tool | Status | What it does |
|---|---|---|
| [photolab](photolab/) | working (v0.x) | Raw photo library and editor: culling, develop sliders, masks, AI denoise, export, catalog import |
| video | planned | Timeline video editor (cuts, titles, colour, audio, export with FFmpeg) |
| layout | planned | Page layout for photo books and print (master pages, text frames, PDF/X export) |
| vector | planned | Vector illustration (paths, shapes, type, SVG/PDF) |
| image | planned | Layer-based image editor |

## Ground rules

- Independent implementations. Code is written from public specifications (DNG, XMP, ICC, PDF, SVG) and
  observed behaviour; no third-party application code is copied or decompiled.
- No vendor assets in this repository. Camera/lens profiles, presets, LUTs and fonts that ship with
  commercial software stay in the user's private `data/` folder (git-ignored) and are only read locally.
- Each tool keeps its library, caches and settings in its own `data/` folder.

## Run

See each tool's README. PhotoLab: `start-photolab.bat` on Windows, `./setup.sh` then `./start-photolab.sh` on Linux.
