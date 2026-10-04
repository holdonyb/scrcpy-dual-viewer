"""FFmpeg export of paused recording segments and presentation layouts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys


def media_info(ffprobe, path):
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, **kwargs)
    if result.returncode:
        raise ValueError(f"无法读取录制片段：{Path(path).name}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    duration = float(data.get("format", {}).get("duration", 0))
    return {"video": video, "audio": any(s.get("codec_type") == "audio" for s in streams), "duration": duration}


def presentation_boxes(count, layout="presentation"):
    if count == 1:
        return [(48, 166, 1824, 850)]
    if count == 2 and layout == "presentation":
        return [(48, 166, 528, 850), (608, 166, 1264, 850)]
    if count == 3 and layout == "presentation":
        return [(48, 166, 528, 850), (608, 166, 1264, 386), (608, 630, 1264, 386)]
    gap, left = 28, 48
    width = ((1824 - gap * (count - 1)) // count) // 2 * 2
    return [(left + index * (width + gap), 166, width, 850) for index in range(count)]


def find_font():
    for path in [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/segoeui.ttf"),
                 Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]:
        if path.is_file():
            return path
    raise RuntimeError("未找到合成视频使用的字体。请安装微软雅黑或 Noto Sans CJK。")


def export_recording(plan, ffmpeg, ffprobe, progress=lambda value, message: None):
    """Return outputs and audio notes; source segments survive any export failure."""
    folder = Path(plan["folder"]).resolve()
    parts = folder / ".parts"
    work = parts / "export"
    work.mkdir(parents=True, exist_ok=True)
    devices, segments = plan["devices"], plan["segments"]
    inspected = []
    for segment in segments:
        info = {d["slot"]: media_info(ffprobe, segment[d["slot"]]) for d in devices}
        if any(not v["video"] or v["duration"] < 0.05 for v in info.values()):
            raise RuntimeError("录制片段太短或没有画面，请检查设备后重新录制。")
        inspected.append(info)
    if not inspected:
        raise RuntimeError("尚未录到可保存的画面。")
    durations = [max(v["duration"] for v in info.values()) for info in inspected]
    total = sum(durations)
    with_mic = any(s.get("mic") for s in segments)
    notes = [f"{d['title']}没有提供设备声音" for d in devices
             if d["audio"] and not any(info[d["slot"]]["audio"] for info in inspected)]
    jobs = len(segments) * (len(devices) + bool(with_mic)) + len(devices) * 2 + 3
    completed = 0

    def run(args, label):
        nonlocal completed
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-filter_complex_threads", "1"] + args
        result = subprocess.run(command, cwd=work, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", **kwargs)
        if result.returncode:
            raise RuntimeError(f"{label}失败。已录片段保留在 {parts}\n{result.stderr.strip()[-900:]}")
        completed += 1
        progress(min(95, round(completed * 95 / jobs)), label)

    codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-threads", "2"]
    audio_codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    joined = {}
    for device in devices:
        slot = device["slot"]
        first = inspected[0][slot]["video"]
        width, height = int(first["width"]), int(first["height"])
        rotation = next((s.get("rotation", 0) for s in first.get("side_data_list", []) if "rotation" in s), 0)
        if abs(int(rotation)) % 180 == 90:
            width, height = height, width
        width, height = max(2, width // 2 * 2), max(2, height // 2 * 2)
        names = []
        for index, (segment, info, duration) in enumerate(zip(segments, inspected, durations)):
            name = f"video_{slot}_{index:03d}.mp4"
            args = ["-i", str(Path(segment[slot]).resolve())]
            have_audio = info[slot]["audio"]
            if device["audio"] and not have_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            args += ["-map", "0:v:0", "-vf",
                     f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={duration:.6f}"]
            if device["audio"]:
                args += ["-map", "0:a:0" if have_audio else "1:a:0", "-af", "aresample=48000,apad"] + audio_codec
            else:
                args += ["-an"]
            args += codec + ["-t", f"{duration:.6f}", "-movflags", "+faststart", name]
            run(args, f"整理{device['title']}片段")
            names.append(name)
        listing = work / f"concat_{slot}.txt"
        listing.write_text("".join(f"file '{name}'\n" for name in names), encoding="utf-8")
        joined[slot] = work / f"joined_{slot}.mp4"
        run(["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", "-movflags", "+faststart",
             joined[slot].name], f"合并{device['title']}片段")

    mic = None
    if with_mic:
        names = []
        for index, (segment, duration) in enumerate(zip(segments, durations)):
            name = f"mic_{index:03d}.m4a"
            args = ["-i", str(Path(segment["mic"]).resolve())] if segment.get("mic") else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            run(args + ["-af", "aresample=48000,apad", "-t", f"{duration:.6f}"] + audio_codec + [name], "整理讲解声音")
            names.append(name)
        listing = work / "concat_mic.txt"
        listing.write_text("".join(f"file '{name}'\n" for name in names), encoding="utf-8")
        mic = work / "joined_mic.m4a"
        run(["-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", mic.name], "合并讲解声音")

    outputs = []
    if plan["mode"] in ("both", "separate"):
        for index, device in enumerate(devices):
            slot = device["slot"]
            safe_title = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in device["title"]).strip(" .")[:50] or slot
            output = folder / f"{index+1:02d}_{safe_title}.mp4"
            args = ["-i", str(joined[slot]), "-map", "0:v:0", "-c:v", "copy"]
            if mic:
                args = ["-i", str(joined[slot]), "-i", str(mic), "-map", "0:v:0", "-c:v", "copy"]
                if device["audio"]:
                    args += ["-filter_complex", "[0:a]volume=0.65[a0];[a0][1:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]", "-map", "[a]"]
                else:
                    args += ["-map", "1:a:0"]
                args += audio_codec
            elif device["audio"]:
                args += ["-map", "0:a:0", "-c:a", "copy"]
            else:
                args += ["-an"]
            run(args + ["-t", f"{total:.6f}", "-movflags", "+faststart", str(output)], f"保存{device['title']}")
            outputs.append(str(output))

    if plan["mode"] in ("both", "combined"):
        shutil.copyfile(find_font(), work / "font.ttc")
        (work / "title.txt").write_text(plan.get("title") or "屏幕录制", encoding="utf-8")
        (work / "date.txt").write_text(plan.get("date", ""), encoding="utf-8")
        for index, device in enumerate(devices):
            (work / f"label_{index}.txt").write_text(device["title"][:16], encoding="utf-8")
        args = []
        for device in devices:
            args += ["-i", str(joined[device["slot"]])]
        if mic:
            args += ["-i", str(mic)]
        boxes = presentation_boxes(len(devices), plan.get("layout", "presentation"))
        filters = [f"color=c=0x101722:s=1920x1080:r=30:d={total:.6f}[base]"]
        decoration = "drawbox=x=48:y=40:w=8:h=52:color=0x55c9c1:t=fill,drawtext=fontfile=font.ttc:textfile=title.txt:fontcolor=0xf4f7fb:fontsize=42:x=80:y=42,drawtext=fontfile=font.ttc:textfile=date.txt:fontcolor=0x8fa1b8:fontsize=22:x=w-tw-48:y=57"
        for index, (x, y, width, height) in enumerate(boxes):
            decoration += f",drawbox=x={x}:y={y-52}:w={width}:h={height+52}:color=0x243245:t=fill,drawbox=x={x+2}:y={y+2}:w={width-4}:h={height-4}:color=0x090f18:t=fill,drawtext=fontfile=font.ttc:textfile=label_{index}.txt:fontcolor=0xd9e9f5:fontsize=28:x={x+18}:y={y-40}"
        filters.append(f"[base]{decoration}[bg0]")
        for index, (x, y, width, height) in enumerate(boxes):
            filters.append(f"[{index}:v]scale={width-4}:{height-4}:force_original_aspect_ratio=decrease,pad={width-4}:{height-4}:(ow-iw)/2:(oh-ih)/2:color=0x090f18,setsar=1[v{index}]")
            filters.append(f"[bg{index}][v{index}]overlay=x={x+2}:y={y+2}:eof_action=repeat[bg{index+1}]")
        audio_inputs = []
        selected_audio = [i for i, device in enumerate(devices) if device["audio"]]
        for index in selected_audio:
            filters.append(f"[{index}:a]volume={0.65/max(1,len(selected_audio)) if mic else 1/max(1,len(selected_audio)):.4f}[a{index}]")
            audio_inputs.append(f"[a{index}]")
        if mic:
            audio_inputs.append(f"[{len(devices)}:a]")
        if audio_inputs:
            filters.append("".join(audio_inputs) + f"amix=inputs={len(audio_inputs)}:duration=longest:normalize=0,alimiter=limit=0.95[aout]")
        output = folder / "合成视频.mp4"
        args += ["-filter_complex", ";".join(filters), "-map", f"[bg{len(devices)}]"]
        args += ["-map", "[aout]"] + audio_codec if audio_inputs else ["-an"]
        run(args + codec + ["-t", f"{total:.6f}", "-movflags", "+faststart", str(output)], "生成合成视频")
        outputs.append(str(output))

    for path in outputs:
        if not media_info(ffprobe, path)["video"]:
            raise RuntimeError(f"输出视频验证失败：{Path(path).name}")
    progress(100, "视频已保存")
    # Own generated segments are retained for recovery/re-export; never erase recordings here.
    return {"outputs": outputs, "notes": notes, "duration": total, "folder": str(folder)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="重新导出保留的录屏片段")
    parser.add_argument("plan", type=Path, help="录屏目录里的 recording.json")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe"))
    args = parser.parse_args()
    if not args.ffmpeg or not args.ffprobe:
        parser.error("需要 FFmpeg 和 ffprobe，可用 --ffmpeg / --ffprobe 指定")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    # Make a moved/copied recording folder usable without exposing device identifiers.
    plan["folder"] = str(args.plan.resolve().parent)
    for segment in plan["segments"]:
        for key, value in segment.items():
            segment[key] = str(args.plan.resolve().parent / ".parts" / Path(value).name)
    result = export_recording(plan, args.ffmpeg, args.ffprobe, lambda p, label: print(f"{p}% {label}"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
