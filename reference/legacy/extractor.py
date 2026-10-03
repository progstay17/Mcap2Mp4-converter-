"""Window-independent extraction service; reuse the supplied MP4 writer."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import mcap_to_mp4 as core


def extract_files(sources, fps=None, notify=None):
    if fps is not None and (not isinstance(fps, int) or not 1 <= fps <= 90000):
        raise ValueError('帧率必须是 1–90000 之间的整数')
    sources = list(dict.fromkeys(Path(p).resolve() for p in sources))
    emit = notify or (lambda event: None)
    results = []
    started = time.monotonic()
    for index, source in enumerate(sources):
        temps, published = [], []
        result = {'source': str(source), 'status': 'failed', 'outputs': []}
        emit({'kind': 'file', 'index': index, 'status': 'processing', 'source': str(source)})
        last_progress = 0

        def progress(stage, base, weight):
            def update(done, total):
                nonlocal last_progress
                now = time.monotonic()
                if done < total and now - last_progress < 0.15:
                    return
                last_progress = now
                fraction = min(1, max(0, done / total)) if total else 0
                emit({'kind': 'progress', 'stage': stage, 'index': index,
                      'percent': (index + base + fraction * weight) / len(sources) * 100,
                      'elapsed': now - started})
            return update

        try:
            if not source.is_file() or source.suffix.lower() != '.mcap':
                raise ValueError('请选择存在的 .mcap 文件')
            outputs = {topic: source.with_name(f'{source.stem}_{suffix}.mp4')
                       for topic, suffix in core.CAMS.items()}
            if any(p.exists() for p in outputs.values()):
                result.update(status='skipped', message='已有同名 MP4，已跳过；不覆盖文件。')
            else:
                actual_fps = core.get_fps(source.parent, fps)
                if not 1 <= actual_fps <= 90000:
                    raise ValueError('session.json 中的帧率必须在 1–90000 之间')
                result['fps'] = actual_fps
                tracks = core.collect_all(source, progress('读取 MCAP', 0, 0.75))
                for camera, (topic, output) in enumerate(outputs.items()):
                    track = tracks[topic]
                    samples = core.finalize(track, topic)
                    fd, name = tempfile.mkstemp(prefix='.' + output.stem + '_',
                                                suffix='.partial', dir=source.parent)
                    os.close(fd)
                    temp = Path(name)
                    temps.append((temp, output))
                    core.write_mp4(temp, samples, track.info, track.params, actual_fps,
                                   progress(f'生成 {core.CAMS[topic]}', 0.75 + camera * 0.125, 0.125))
                    del samples
                for temp, output in temps:
                    if os.name == 'nt':
                        # Windows rename refuses to replace an existing target;
                        # unlike hard links it also works on exFAT/FAT volumes.
                        os.rename(temp, output)
                    else:
                        os.link(temp, output)
                    published.append(output)
                result.update(status='success', outputs=[str(p) for p in published],
                              message=f'已提取双相机视频，帧率 {actual_fps} FPS。')
        except Exception as exc:
            for output in published:
                output.unlink(missing_ok=True)
            result['message'] = f'{type(exc).__name__}: {exc}'
        finally:
            for temp, _ in temps:
                temp.unlink(missing_ok=True)
        results.append(result)
        emit({'kind': 'result', 'index': index, **result})
        emit({'kind': 'progress', 'index': index, 'stage': '文件处理结束',
              'percent': (index + 1) / len(sources) * 100, 'elapsed': time.monotonic() - started})
    return results
