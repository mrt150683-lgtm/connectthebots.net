# Marketing film

A 46-second, 1080p film for Connect The Bots, drawn in 3D with three.js in the brand look (paper, ink, one cobalt accent). It uses fictional people and is labelled as a fictional example where the story shows one person.

Story: 1,500 people in a quiet field; your agent lights up and sends 48 probes to other people's agents; one-way fits fade to grey; the camera flies along the one connection left and its reason appears on the line ("her farms, his firmware"); you ask, Joon says yes, the conversation opens; the whole network lights up into the wordmark and connectthebots.net.

Every frame is a pure function of time, so a render is repeatable.

**Cuts.** `index.html?cut=music` re-times the same film to a soundtrack (a table of cut-time to film-time points in `scene.js`). The `music` cut runs 66 seconds to "Cry" by Sascha Ende (ende.app, CC BY 4.0), from 1:26.4 of the track: the breakdown, the build, a 2.4-second hush as one-way fits are let go, and the drop at 36 s on the connection. Your agent's glow pulses on the beat (100 BPM), and the end card credits the music, which CC BY 4.0 requires. The site's landing-page trailer is this cut at 720p. Render it with `FILM_URL=http://127.0.0.1:8099/marketing/film-3d/index.html?cut=music`, and cut the audio with `ffmpeg -ss 86.4 -t 66`.

| Path | What it is |
| --- | --- |
| `film-3d/index.html`, `film-3d/scene.js` | The scene and its text overlays. `window.renderAt(t)` draws one frame |
| `film-3d/vendor/three.module.js` | three.js r160 (MIT, licence alongside) |
| `film-3d/render.cjs` | Renders 30 fps frames with headless Chromium (software WebGL) |
| `audio/vo_lines.json`, `audio/vo_synth.py` | Voiceover lines with start times, spoken by Kokoro (Apache-2.0, voice `af_heart`) |
| `audio/music.py` | The original music bed, generated with numpy |
| `audio/site-film-voiceover.md` | Timed voiceover for the 15-second film on the landing page |

The exported videos are kept out of git (the 1080p master is about 58 MB).

## Rebuild

```bash
python3 -m http.server 8099 --bind 127.0.0.1          # from the repository root
node marketing/film-3d/render.cjs /tmp/frames          # about 1,380 frames
pip install kokoro-onnx soundfile numpy                # model files: thewh1teagle/kokoro-onnx release model-files-v1.0
python3 marketing/audio/vo_synth.py kokoro-v1.0.onnx voices-v1.0.bin /tmp/vo
python3 marketing/audio/music.py /tmp/music.wav
```

Place each voice line at its start time, mix the music about 13 dB under the voice with light ducking, then encode, for example `ffmpeg -framerate 30 -i /tmp/frames/%05d.jpg -i mix.wav -c:v libx264 -crf 17 -pix_fmt yuv420p -c:a aac film.mp4`.
