import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pathlib import Path
import ros2_video
from extractor import extract_files

src = Path(sys.argv[1])
fps = None if sys.argv[2] == 'auto' else int(sys.argv[2])
try:
    layout = ros2_video.detect_layout(src)
    if layout == 'ego':
        res = extract_files([src], fps=fps)[0]
        res['layout'] = 'ego'
    else:
        res = ros2_video.extract_ros2(src, fps=fps)
except Exception as e:
    res = {'source': str(src), 'status': 'failed', 'outputs': [], 'message': f'{type(e).__name__}: {e}'}
print(json.dumps(res, ensure_ascii=False))
sys.exit(0 if res['status'] == 'success' else 1)
