# Voiceover lines for the 46-second film with Kokoro (Apache-2.0), voice af_heart.
# Usage: python3 vo_synth.py MODEL.onnx VOICES.bin OUTDIR   (lines and start times in vo_lines.json)
import json, os, sys, soundfile as sf
from kokoro_onnx import Kokoro
model, voices, outdir = sys.argv[1:4]
k = Kokoro(model, voices)
L = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vo_lines.json")))
for i, (t, txt) in enumerate(L):
    a, sr = k.create(txt, voice="af_heart", speed=0.92, lang="en-us")
    sf.write(os.path.join(outdir, f"{i}.wav"), a, sr)
    print(i, t, round(len(a) / sr, 2), txt)
