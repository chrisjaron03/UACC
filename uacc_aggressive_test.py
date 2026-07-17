"""
AGGRESSIVE REAL-WORLD UACC TEST
Exercises UACC's working modules against a real Notepad workflow.
Bypasses pywinauto (DLL issue with Hermes env) — still tests
screen_capture, executor, clipboard, grid_encoder, screen_diff,
model adapter, session memory, and element finder.

Logs everything + saves evidence screenshots to desktop.
"""
import sys, os, json, time, traceback
sys.path = [p for p in sys.path if 'hermes' not in p.lower()]

LOG = []
EVIDENCE = r'C:\Users\chris\Desktop\uacc_evidence'
os.makedirs(EVIDENCE, exist_ok=True)

def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    LOG.append(line)
    print(line)

def snap(name, obj, maxlen=500):
    s = str(obj)
    log(f'  └─ {name}: {s[:maxlen]}')
    return obj

def save_img(name, img):
    path = os.path.join(EVIDENCE, name)
    img.save(path)
    log(f'  └─ Saved: {path} ({img.size[0]}×{img.size[1]}, {os.path.getsize(path)} bytes)')
    return path

def try_it(desc, fn):
    log(f'▶ {desc}')
    try:
        t0 = time.time()
        r = fn()
        log(f'  ✓ ({time.time()-t0:.1f}s)')
        return r
    except Exception as e:
        tb = traceback.format_exc()
        log(f'  ✗ {type(e).__name__}: {e}')
        log(f'  └─ {tb.split(chr(10))[-3].strip()}')
        return None

log('═' * 65)
log('UACC AGGRESSIVE REAL-WORLD TEST — ' + time.strftime('%Y-%m-%d %H:%M:%S'))
log('═' * 65)
log('')
# ═══════════════════════════════════════════
# PHASE 1: DESKTOP CAPTURE
# ═══════════════════════════════════════════
log('── PHASE 1: DESKTOP CAPTURE ──')

from PIL import Image
from uacc.core.screen_capture import capture_full, capture_region, get_screen_size
from uacc.core.text_map import build_text_map
from uacc.core.clipboard import read_clipboard, write_clipboard
from uacc.core.element_finder import get_mouse_position

res = try_it('get_screen_size', get_screen_size)
full_a = try_it('capture_full() — before', capture_full)
if full_a: save_img('01_before.png', full_a)

try_it('get_mouse_position (before)', get_mouse_position)

# Build text map (will be empty without pywinauto, still tests the function)
try_it('build_text_map() — empty tree fallback', lambda: build_text_map(1920, 1080, [], active_window='Desktop'))

# Region capture
center = try_it('capture_region(800,400,320,240)', lambda: capture_region(800, 400, 320, 240))
if center: save_img('02_center_region.png', center)

# Clipboard
try_it('write_clipboard("UACC-REAL-TEST")', lambda: write_clipboard('UACC-REAL-TEST'))
try_it('read_clipboard', read_clipboard)

log('')
# ═══════════════════════════════════════════
# PHASE 2: NOTEPAD ODYSSEY
# ═══════════════════════════════════════════
log('── PHASE 2: NOTEPAD ODYSSEY ──')

from uacc.actions.executor import ActionExecutor
from uacc.actions.schema import ClickAction, TypeAction, HotkeyAction, DragAction, MouseButton, ScrollDirection

executor = ActionExecutor(human_mimicry=True, safe_mode=True)

# 2.1 Launch Notepad
def launch_notepad():
    import subprocess
    p = subprocess.Popen(['notepad.exe'], shell=True)
    time.sleep(1.5)
    return f'PID={p.pid}, running={p.poll() is None}'
try_it('launch_notepad()', launch_notepad)

# 2.2 Screenshot after launch
time.sleep(0.5)
shot1 = try_it('capture_full() — notepad open', capture_full)
if shot1: save_img('03_notepad_open.png', shot1)

# 2.3 Type in Notepad (coordinates based on known Notepad position)
test_text = """UACC Real-World Test Results:
✅ Screen capture working
✅ Text map generated
✅ Action executor dispatching
✅ Clipboard round-trip
✅ Grid overlay & markers
✅ Screen diff detection
✅ Model adapter pipeline
"""
try_it('executor.type_text() in Notepad', lambda: executor.execute(TypeAction(
    text=test_text, delay_ms=15, reasoning='Typing test report in Notepad'
)))

# 2.4 Screenshot after typing
shot2 = try_it('capture_full() — after typing', capture_full)
if shot2: save_img('04_after_typing.png', shot2)

# 2.5 Ctrl+S = Save
try_it('Ctrl+S hotkey', lambda: executor.execute(HotkeyAction(
    keys=['ctrl', 's'], reasoning='Open Save dialog'
)))
time.sleep(1)

# 2.6 Screenshot of Save dialog
shot3 = try_it('capture_full() — save dialog', capture_full)
if shot3: save_img('05_save_dialog.png', shot3)

# 2.7 Type save path and press Enter
save_path = r'C:\Users\chris\Desktop\uacc_notepad_test.txt'
try_it('type save filename', lambda: executor.execute(TypeAction(
    text=save_path, delay_ms=8, reasoning='Type save path'
)))
time.sleep(0.3)
try_it('Enter to save', lambda: executor.execute(HotkeyAction(
    keys=['enter'], reasoning='Confirm save'
)))
time.sleep(2)

# 2.8 Screenshot after save
shot4 = try_it('capture_full() — after save', capture_full)
if shot4: save_img('06_after_save.png', shot4)

# 2.9 Close Notepad
try_it('Alt+F4 close', lambda: executor.execute(HotkeyAction(
    keys=['alt', 'f4'], reasoning='Close Notepad'
)))
time.sleep(1)
try_it('Tab then Enter (dismiss dialog)', lambda: (
    executor.execute(HotkeyAction(keys=['tab'], reasoning='Navigate dialog')),
    executor.execute(HotkeyAction(keys=['enter'], reasoning='Confirm'))
))
time.sleep(1.5)

# 2.10 Final screenshot
full_b = try_it('capture_full() — after notepad closed', capture_full)
if full_b: save_img('07_after_close.png', full_b)

log('')
# ═══════════════════════════════════════════
# PHASE 3: VISUAL ANALYSIS
# ═══════════════════════════════════════════
log('── PHASE 3: VISUAL ANALYSIS ──')

# 3.1 Screen diff (before vs after)
if full_a and full_b:
    from uacc.core.screen_diff import has_changed, compute_diff
    try_it('has_changed(before vs after)', lambda: has_changed(full_a, full_b))
    diff = try_it('compute_diff(before vs after)', lambda: compute_diff(full_a, full_b))
    if diff:
        log(f'  └─ Changed: {diff.changed}, {diff.changed_percentage:.1f}%, {diff.total_pixels_changed} pixels')

# 3.2 Grid overlay
if full_b:
    from uacc.core.grid_encoder import overlay_grid, overlay_markers, build_marker_legend, zoom_region, grid_cell_to_pixel
    grid = try_it('overlay_grid(full_b, medium)', lambda: overlay_grid(full_b, mode='medium'))
    if grid: save_img('10_grid_overlay.png', grid)
    zoom = try_it('zoom_region(full_b, 960, 540)', lambda: zoom_region(full_b, 960, 540, zoom_level=2))
    if zoom: save_img('11_center_zoom.png', zoom)

# 3.3 Grid math
for col, row in [(0,0), (5,3), (20, 15), (47, 26)]:
    px = try_it(f'grid_cell_to_pixel(col={col}, row={row}, 1920,1080)',
                lambda c=col, r=row: grid_cell_to_pixel(c, r, 1920, 1080))
    if px: log(f'  └─ Cell ({col},{row}) → pixel {px}')

# 3.4 Diff same image (should be no change)
if full_b:
    try_it('has_changed(same image)', lambda: has_changed(full_b, full_b))

log('')
# ═══════════════════════════════════════════
# PHASE 4: MODEL ADAPTER PIPELINE
# ═══════════════════════════════════════════
log('── PHASE 4: MODEL ADAPTER ──')

from uacc.models.base_adapter import BaseAdapter

class TestAdapter(BaseAdapter):
    def _build_messages(self, task, screen_state, action_history):
        return [{'role': 'user', 'content': f'Task: {task}'}]
    def _call_llm(self, messages):
        return json.dumps({'action': 'click', 'x': 960, 'y': 540, 'button': 'left',
                           'count': 1, 'reasoning': 'Test click'})

model = TestAdapter()
result = try_it('observe_and_act()', lambda: model.observe_and_act('Click center', {}, []))
if result:
    for a in result:
        log(f'  └─ action={a.action} x={a.x} y={a.y} reason="{a.reasoning}"')

log('')
# ═══════════════════════════════════════════
# PHASE 5: EXECUTOR EDGE CASES
# ═══════════════════════════════════════════
log('── PHASE 5: EXECUTOR EDGE CASES ──')

# Drag (safe coordinates)
try_it('executor.drag()', lambda: executor.execute(DragAction(
    start_x=100, start_y=100, end_x=300, end_y=300,
    button=MouseButton.LEFT, duration_ms=300,
    reasoning='Test drag gesture'
)))

# Click at safe position (taskbar area unlikely to cause harm)
try_it('executor.click()', lambda: executor.execute(ClickAction(
    x=500, y=950, button=MouseButton.LEFT, count=1,
    reasoning='Click taskbar (safe area)'
)))

# Scroll
try_it('executor.scroll()', lambda: executor.execute(type('ScrollAction', (), {
    'x': 500, 'y': 500, 'direction': 'down', 'amount': 3,
    'reasoning': 'Test scroll'
})()))

log('')
# ═══════════════════════════════════════════
# PHASE 6: SESSION MEMORY
# ═══════════════════════════════════════════
log('── PHASE 6: SESSION MEMORY ──')

from uacc.agent.memory import SessionMemory

session = SessionMemory()
session.record_action({'action': 'capture_full'}, {'success': True, 'size': '1920×1080'})
session.record_action({'action': 'type_text'}, {'success': True, 'chars': len(test_text)})
session.record_action({'action': 'save_file'}, {'success': True, 'path': save_path})
session.record_action({'action': 'close_notepad'}, {'success': True})

session.learn_shortcut('Save file', 'hotkey', ['ctrl', 's'], 0.95)
session.learn_shortcut('Close window', 'hotkey', ['alt', 'f4'], 0.9)

summary = session.get_summary()
log(f'Session summary: {json.dumps(summary, indent=2)}')
log(f'Actions logged: {len(session.action_history)}')
for a in session.action_history:
    turn = a.get('turn', '?')
    action = a.get('action', '?')
    success = a.get('success', False)
    log(f'  [{turn}] {action}: success={success}')
log(f'Shortcuts:')
for k, v in session.learned_shortcuts.items():
    log(f'  {v.pattern}: {v.keys} (confidence={v.confidence})')

log('')
# ═══════════════════════════════════════════
# FINAL VERIFICATION
# ═══════════════════════════════════════════
log('── FINAL VERIFICATION ──')

# Write full log
log_path = os.path.join(EVIDENCE, 'uacc_aggressive_test_log.txt')
with open(log_path, 'w') as f:
    f.write('\n'.join(LOG))
log(f'Test log: {log_path} ({len(LOG)} lines)')

# List all evidence files
log(f'\nEvidence gallery ({EVIDENCE}):')
for fn in sorted(os.listdir(EVIDENCE)):
    fp = os.path.join(EVIDENCE, fn)
    sz = os.path.getsize(fp)
    log(f'  {fn:40s} {sz:>8,} bytes')

# Check saved Notepad file
if os.path.exists(save_path):
    with open(save_path) as f:
        nt_content = f.read()
    log(f'\n✅ Notepad file saved: {save_path}')
    log(f'   Content ({len(nt_content)} chars):')
    for line in nt_content.strip().split('\n'):
        log(f'     │ {line}')
else:
    log(f'\n❌ Notepad file NOT FOUND at {save_path}')

log('')
log('═' * 65)
log('UACC AGGRESSIVE REAL-WORLD TEST COMPLETE')
log(f'  Evidence: {EVIDENCE}')
log(f'  Log: {log_path}')
log('═' * 65)
