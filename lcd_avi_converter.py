#!/usr/bin/env python3
"""Convert videos for the device-specific 320x240 LCD AVI player.

Requirements: FFmpeg/FFprobe on PATH, Pillow (pip install Pillow), and the
original working video.avi alongside this script (or --template PATH).
This version reproduces the JPEG marker layout of the hardware-tested hybrid.
"""
import argparse
import io
import shutil
import struct
import subprocess
from pathlib import Path
from PIL import Image

WIDTH, HEIGHT = 320, 240
FPS_NUM, FPS_DEN = 500000, 23061
SAMPLE_RATE = 22050
SAMPLES_PER_BLOCK = 1017
BLOCK_SIZE = 512


def run(program, args):
    p = subprocess.run([program, *args], capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace"))
    return p.stdout


def get_audio_present(src):
    return bool(run("ffprobe", ["-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(src)]).strip())


def ffmpeg(args):
    return run("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", *args])


def jpeg_frames(src, quality):
    raw = ffmpeg(["-i", str(src), "-map", "0:v:0", "-vf",
        f"fps={FPS_NUM}/{FPS_DEN},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    frame_bytes = WIDTH * HEIGHT * 3
    if len(raw) % frame_bytes:
        raise ValueError("Unexpected raw frame data length")
    frames = []
    for off in range(0, len(raw), frame_bytes):
        rgb = Image.frombytes("RGB", (WIDTH, HEIGHT), raw[off:off + frame_bytes])
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=quality, subsampling=2, optimize=False)
        jpg = bytearray(out.getvalue())
        if jpg[0:6] != b"\xff\xd8\xff\xe0\x00\x10" or jpg[6:10] != b"JFIF":
            raise ValueError("Unexpected Pillow JPEG header")
        # The working frame starts AVI1 followed by ten zero bytes.
        jpg[6:20] = b"AVI1" + b"\x00" * 10
        frames.append(bytes(jpg))
    return frames


def wav_data(wav):
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("Audio converter did not produce WAV")
    pos = 12
    while pos + 8 <= len(wav):
        size = struct.unpack_from("<I", wav, pos + 4)[0]
        if wav[pos:pos + 4] == b"data":
            return wav[pos + 8:] if size == 0xffffffff else wav[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    raise ValueError("No audio payload in WAV")


def convert(src, dest, template, quality):
    for app in ("ffmpeg", "ffprobe"):
        if not shutil.which(app):
            raise RuntimeError(f"{app} missing from PATH")
    base = Path(template).read_bytes()
    if base[:4] != b"RIFF" or base[8:12] != b"AVI " or base[324:328] != b"movi" or base[328:332] != b"00dc":
        raise ValueError("Template must be the original working device video.avi")
    frames = jpeg_frames(src, quality)
    if not frames:
        raise ValueError("No video frames found")
    duration = len(frames) * SAMPLES_PER_BLOCK / SAMPLE_RATE
    if get_audio_present(src):
        wav = ffmpeg(["-i", str(src), "-map", "0:a:0", "-af",
            f"apad,atrim=duration={duration:.9f}", "-ar", str(SAMPLE_RATE),
            "-ac", "1", "-c:a", "adpcm_ima_wav", "-block_size", "512", "-f", "wav", "-"])
    else:
        wav = ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
            "-t", f"{duration:.9f}", "-c:a", "adpcm_ima_wav",
            "-block_size", "512", "-f", "wav", "-"])
    audio = wav_data(wav)
    blocks = [audio[x:x+BLOCK_SIZE] for x in range(0, len(audio), BLOCK_SIZE)]
    if len(blocks) < len(frames) or any(len(b) != BLOCK_SIZE for b in blocks[:len(frames)]):
        raise ValueError("Not enough complete audio blocks")
    header = bytearray(base[:328])
    struct.pack_into("<I", header, 48, len(frames))
    payload, index = bytearray(), bytearray()
    for frame, block in zip(frames, blocks):
        for tag, blob in ((b"00dc", frame), (b"01wb", block)):
            offset = 4 + len(payload)
            payload.extend(tag + struct.pack("<I", len(blob)) + blob)
            payload.extend(b"\0" * (-len(payload) % 4))
            index.extend(struct.pack("<4sIII", tag, 0x10, offset, len(blob)))
    struct.pack_into("<I", header, 320, 4 + len(payload))
    result = header + payload + b"idx1" + struct.pack("<I", len(index)) + index
    struct.pack_into("<I", result, 4, len(result) - 8)
    Path(dest).write_bytes(result)
    print(f"Created {dest}: {len(frames)} frames, {duration:.2f} seconds")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path, nargs="?")
    p.add_argument("--template", type=Path, default=Path(__file__).with_name("video.avi"))
    p.add_argument("--quality", type=int, default=65, choices=range(1, 96), metavar="1-95")
    args = p.parse_args()
    try:
        convert(args.input, args.output or args.input.with_name(args.input.stem + "_device.avi"), args.template, args.quality)
    except (OSError, ValueError, RuntimeError) as exc:
        p.exit(1, f"Error: {exc}\n")

if __name__ == "__main__":
    main()
