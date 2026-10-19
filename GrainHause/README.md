# GrainHause — film developer for RAW photos

A desktop app (macOS + Windows) that develops your photos like film: Kodak, Fujifilm and CineStill
stocks, real film grain, halation, light leaks, film borders, and a Lightroom Classic round trip.

## What's inside

**38 film stocks**
- **Kodak** — Portra 160 / 400 / 800, Gold 200, Ektar 100, UltraMax 400, ColorPlus 200, ProImage 100, Max 800,
  Vision3 50D / 250D / 500T, Ektachrome E100, Elite Chrome 100, Kodachrome 64, Aerochrome III (infrared),
  Tri-X 400, T-Max 100, T-Max P3200, Double-X 5222
- **Fujifilm** — Superia X-TRA 400, Superia 200, Pro 400H, Pro 160NS, C200, Fujifilm 400, Natura 1600,
  Eterna 500T, Velvia 50 / 100, Provia 100F, Astia 100F, Acros 100 II, Neopan 400
- **CineStill** — 800T, 50D, 400D, BwXX
- plus a "Digital (no film)" base if you only want grain/halation

**Controls**
| Section | Controls |
|---|---|
| Film stock | grid of large live previews of *your* photo on every stock, filters (Kodak / Fujifilm / CineStill / B&W), **Compare all** full-window view (`C`), film strength |
| Exposure | auto-meter (RAW), exposure ±4 EV, push/pull ±2 stops, highlights, shadows, contrast |
| White balance | As shot / Daylight / Cloudy / Shade / Tungsten / Fluorescent / Flash, Kelvin temperature + tint, eyedropper |
| Skin tones | skin warmth (rosy ↔ golden), skin saturation — only skin is affected, for every complexion |
| Colour & lab | lab process (Frontier-style scan, Noritsu-style scan, neutral camera scan, optical RA-4 print), saturation, fade |
| Tone curve | RGB + separate Red/Green/Blue curves, drag points; Linear / Soft contrast / Matte / Punchy starters |
| Crop | Original, Free, 1:1, 4:5 Instagram, 9:16 Story, 3:2, 16:9, 1.91:1 — rule-of-thirds guides, rotate ratio |
| Grain | presets Minimal / Fine / Classic / Pronounced / Heavy, amount, size, roughness, film softness, clean digital noise |
| Light | halation, bloom, vignette |
| Light leaks | edge fog, corner glow, frame burn, full veil, light streak — amount + shuffle |
| Dust, scratches & texture | dust, scratches, texture (matte paper, fibre paper, expired film, emulsion mottle) |
| Frame & border | scan edge, 35mm strip (sprockets + edge print), medium format, instant print, white print |

**Other**
- **Zoom**: Fit / 100% / zoom buttons, pinch or ⌘-scroll, double-click (or `Z`) toggles Fit ↔ 100%, drag to pan.
  Zoomed views are rendered sharp from the full-resolution file. Shortcuts: ⌘+ ⌘− ⌘0 (Fit) ⌘1 (100%)
- **Undo / redo**: ⌘Z / ⇧⌘Z, a full history per photo (a slider drag is one step)
- **Reset**: ↺ next to every slider, "Reset" on every panel, or double-click a slider's name
- Hold `\` to see the original instantly; let go to see the edit
- **⌘S** writes the look back into a Lightroom TIFF (stays open, save as often as you like); for other photos it quick-exports to your last export folder
- The photo list is remembered between sessions — every photo you've edited (and photos Lightroom sends over) stays there with its edits; right-click to remove
- Saves your edit next to each photo (`photo.CR3.grainhouse.json`) — originals are never touched
- Save looks, copy/paste settings, sync a look to selected photos, batch export
- Export JPEG / 16-bit TIFF / PNG in sRGB, Display P3, Adobe RGB or ProPhoto; Instagram 1080 px size
- Export any look as a `.cube` LUT for Premiere, Final Cut, DaVinci or CapCut (colour only)

## How the film is modelled — dyed, not filtered

1. **Light on the film.** White balance (as a colour temperature), exposure, vignette, bloom, halation
   and light leaks are all light reaching the emulsion. The film's lower resolution softens fine detail.
2. **Three emulsion layers.** The light is split by each layer's spectral sensitivity (red-, green- and
   blue-sensitive; Fuji stocks add their 4th cyan-sensitive layer, which is where Fuji greens come from).
3. **Dye formation.** Each layer develops dye — cyan, magenta, yellow — through its own characteristic
   curve (toe, straight line, shoulder). DIR couplers between layers add saturation inside the dyes.
4. **Grain is simulated, not noise.** Real grains — dye clouds in colour film, silver grains in black &
   white — are simulated as particles of random size and position (a Boolean model). Where the film
   is dense there are more of them and they overlap and clump; where it's thin they're sparse. Each
   dye layer has its own grains, with the fast blue-sensitive layer coarsest.
5. **Development effects.** Each layer develops harder next to thinner areas and weaker next to denser
   ones, carving edges and separating tones inside the dyes ("Film depth").
6. **Dyes become the image.** The three dyes' real absorption spectra, including their unwanted side
   absorptions, are multiplied together at every wavelength. That light is read by a lab scanner's
   narrow-band sensors, exposed onto RA-4 colour paper (which forms its *own* three dyes), or — for
   slides — viewed directly. The lab then grey-balances, the way a lab tech would.
7. **Surface.** Dust and hairs print white from negatives and dark from slides; scratches run along
   the film; paper and expired-film textures sit on top; then your tone curve.

Stock parameters are tuned from each film's datasheet curves and its known character; they are
not lab measurements of real film, and film names are used only to describe the look. Every number
lives in `grainhouse/stocks.py`.

## Install

You need Python 3.10 or newer.

**Mac**: open Terminal in this folder and run `bash build_mac.sh`. You get `dist/GrainHause.app` —
drag it to Applications. The first time, right-click → Open (it isn't signed by Apple).

**Windows**: double-click `build_windows.bat`. You get `dist\GrainHause\GrainHause.exe`. Move the
whole `dist\GrainHause` folder somewhere permanent, e.g. `C:\Program Files\GrainHause`.

**Or run straight from source**: `pip install -r requirements.txt` then `python run.py`.

## Use it from Lightroom Classic

1. Lightroom Classic → Settings (Mac: Lightroom Classic menu; Windows: Edit menu) → **External Editing**.
2. Under **Additional External Editor** click Choose and pick GrainHause.app / GrainHause.exe.
3. Set **File Format: TIFF**, **Color Space: ProPhoto RGB**, **Bit Depth: 16 bits/component**.
4. Preset → **Save Current Settings as New Preset…** → name it "GrainHause".
5. Right-click any photo → **Edit In → GrainHause**.

Lightroom makes `IMG_1234-Edit.tif` with your Lightroom edits and opens it in GrainHause (an orange
banner confirms Lightroom mode). Develop it, then press **Save & return to Lightroom** — the film
look is written back into that TIFF (16-bit, ProPhoto, same colour profile) and Lightroom shows
it next to the original. You can select several photos and send them all at once.

Tips: do your basic exposure/white balance in Lightroom, leave Lightroom's own grain and film
presets off, and set Exposure in GrainHause rather than stacking curves. The Lightroom cloud app
(not Classic) has no external-editor support — export from it and open the files here instead.

You can also skip Lightroom entirely: open RAW files directly (CR2, CR3, NEF, ARW, RAF, DNG, ORF,
RW2 and most others) and iPhone HEIC photos — GrainHause develops the full 16-bit RAW and auto-meters it like a film camera.

## Command line / batch

```
python -m grainhouse.cli --list
python -m grainhouse.cli shoot/*.CR3 --stock portra400 --exposure 0.5 --grain 120 --border strip35 -o out/
python -m grainhouse.cli shoot/ --stock cinestill800t --lab print --dust 30 --aspect 4:5 --wb-temp 3200 -o out/
python -m grainhouse.cli shoot/ --use-sidecars --format tif --space "ProPhoto RGB" -o out/
python -m grainhouse.cli --lut cinestill800t -o "CineStill 800T.cube"
```

## Files

```
run.py                 start the app
grainhouse/gui.py      desktop app
grainhouse/engine.py   renderer (light → film → finishing)
grainhouse/dye.py      spectral dye model: layer sensitivities, dye spectra, scanners
grainhouse/film.py     builds each stock's curves + scan/print/slide lookup
grainhouse/textures.py dust, hairs, scratches, paper and expired-film textures
grainhouse/widgets.py  tone curve editor, crop guides, eyedropper
grainhouse/stocks.py   the film stocks (edit to tune)
grainhouse/effects.py  grain, halation, bloom, leaks, vignette, borders
grainhouse/imageio.py  RAW/TIFF/JPEG loading, ICC profiles, saving
grainhouse/cli.py      batch processing and LUT export
tests/                 python -m pytest tests
```
