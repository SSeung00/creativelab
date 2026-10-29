# PhotoLab

A personal photo management and RAW development program that replaces Lightroom. It was built around Sony a7 IV ARW files, but it also handles any RAW that LibRaw opens, and JPEG.

- **Originals are never touched.** Photos are only read in place. Edits, thumbnails and AI results are stored only in the `data/` folder.
- **Same colour as Lightroom.** It reads the Adobe camera profiles (Adobe Color/Standard, Camera ST·VV…) and lens profiles installed on the PC as they are.
  - Exposure reference and tone operations were matched by comparing directly against hundreds of photos rendered by Lightroom (`backend/tools/lrcompare.py`, `tune.py`).
- **Processed on the GPU.** A RAW is decoded once and kept on the RTX graphics card; moving a slider redraws it within 30–50 ms.

## Running

| File | What it does |
|---|---|
| `start-photolab.bat` | Starts the server and opens http://127.0.0.1:8002 in the browser. On the first run it prepares the Python packages and the web app by itself. |
| `stop-photolab.bat` | Stops a server running in the background. |
| `install-autostart.bat` | Starts it automatically at log-in. Run `uninstall-autostart.bat` to undo. |

### Linux

`./setup.sh` once (and again after pulling changes: `uv sync`, `npm ci`, web build; needs
[uv](https://docs.astral.sh/uv/) and Node.js 20+), then `./start-photolab.sh`. It runs the server in the
terminal (Ctrl+C stops it) and opens the app window (a Chromium-family browser in `--app` mode, falling
back to the default browser); if PhotoLab is already running, it just opens the window.

Without Adobe software installed, PhotoLab looks for the Adobe camera and lens profiles in `data/adobe-camera-raw`
(a private copy of Adobe's CameraRaw folder, e.g. from the free Adobe DNG Converter), or set `PHOTOLAB_ADOBE_DIR`
to another folder. Those files are Adobe's and must never be committed.

**Using it from the phone**
1. Set a PIN on the Settings screen.
2. Run this command once on the PC.
   ```
   tailscale serve --bg --https=10000 8002
   ```
3. Connect with the QR code shown on the Settings screen.

The port doesn't clash with RunLab (443) or MoneyLab (8443).

## Features

**Library**
- Uses your folders as they are; there is no catalog import step.
- Thumbnails and the loupe view appear immediately from the preview embedded in the RAW.
- Star ratings, pick/reject and colour labels, with filters.
- Click in the loupe view for a 100% focus check.
- Virtual copies.

**Keyboard shortcuts (Library)**

| Key | Action |
|---|---|
| 0–5 | Star rating |
| P / X / U | Pick / reject / clear |
| 6–9 | Colour label |
| Enter | Loupe view |
| D | Go to Develop |
| Ctrl+Shift+C / V | Copy / paste settings |

**Develop**
- **Basic panel**: profile, white balance (eyedropper), exposure, contrast, highlights/shadows, whites/blacks, texture, clarity, dehaze, vibrance, saturation, auto tone
- **Tone**: tone curve (point curve, parametric curve)
- **Colour**: colour mixer (HSL, black & white), colour grading
- **Detail**: sharpening, noise reduction
- **Lens**: distortion and vignetting correction with Adobe lens profiles
- **Transform & crop**: locked aspect ratio, straighten, 90° rotation, flip
- **Effects**: vignette, grain
- **Masks**: linear, radial, brush, AI subject / sky / people / background
- **Management**: presets, snapshots, history, undo, before/after comparison (`\`), 100% view (`Z`)

**AI**
- **AI masks**: SegFormer for sky and people, IS-Net for subjects.
- **AI denoise**: SCUNet. Processed once at the RAW stage, then cached.
- **AI culling**
  - Finds shaky or out-of-focus shots and closed eyes.
  - Groups bursts and picks the best shot in each.

Models are downloaded from their official sources into `data/models` on first use (weights only; no remote code is run).

**Import and export**
- **Memory card import**: copies into per-date folders, with an optional backup folder. Nothing is deleted from the card.
- **Lightroom catalog migration**: brings over ratings, flags, labels and adjustments. Files are matched by capture time even if their names differ.
- **Export**: four presets (social media / phone / full size / 16-bit TIFF for printing) plus custom settings. ICC profile and EXIF are embedded.
- Exported photos can be downloaded or shared directly on the phone.

## Structure

```
backend/   FastAPI + PyTorch(CUDA)
  app/engine/   RAW decoding (LibRaw) · DCP/LCP/look profiles · GPU develop pipeline · masks · export
  app/ai/       AI masks · denoise · culling
  app/library/  folder scan · thumbnails · memory card import · Lightroom catalog
  tools/        Lightroom comparison (lrcompare) · tone coefficient fitting (tune) · render profiling
frontend/  React + Vite + Tailwind
data/      DB, cache, exported photos, models (deleting it doesn't affect the originals)
```

**Development**
- Start it with `photolab-backend` (8002) and `photolab-frontend` (5175) from `.claude/launch.json`.
- After changing the UI, run `npm run build` in `frontend` for it to show on 8002 (phone included).

## App window and taskbar

`start-photolab.bat` opens the app in its own Edge window at `http://photolab.localhost:8002`: Edge names an app window's taskbar entry after its host (not the port), so each app gets its own taskbar icon.
