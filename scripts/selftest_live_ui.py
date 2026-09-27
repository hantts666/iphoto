import os, sys, json, time
from pathlib import Path
if "--live" not in sys.argv:
    raise SystemExit("opt-in live QA: pass --live (uses the saved Windows credential)")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"tests"))
os.environ.setdefault("QT_QUICK_BACKEND","software")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QFontDatabase
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
QQuickStyle.setStyle("Basic")
app = QGuiApplication.instance() or QGuiApplication([])
app.setOrganizationName("iPhoto"); app.setApplicationName("iPhoto")
for name in ("msyh.ttc","msyhbd.ttc","segoeui.ttf"):
    f = Path("C:/Windows/Fonts")/name
    if f.exists(): QFontDatabase.addApplicationFont(str(f))
from iphoto.workspace import Editor, ROOT
from test_ai import wait_for
from test_editor import settled

warnings = []
shots = ROOT/"artifacts"/"live-selftest"; shots.mkdir(parents=True, exist_ok=True)
log = []
def note(msg):
    log.append(msg); print(msg, flush=True)

editor = Editor()
if not editor.ai.ready:
    editor.close(); raise SystemExit("no saved AI key")
note("AI: " + editor.ai.settings.provider + " / " + editor.ai.settings.model)
engine = QQmlApplicationEngine()
engine.warnings.connect(lambda items: warnings.extend(i.toString() for i in items))
engine.rootContext().setContextProperty("editor", editor)
engine.load(QUrl.fromLocalFile(str(ROOT/"src/iphoto/ui/Main.qml")))
assert engine.rootObjects(), warnings
window = engine.rootObjects()[0]; window.resize(1440,930)
window.setProperty("chatOpen", True)

def find(name):
    obj = window.findChild(QObject, name); assert obj is not None, name; return obj
def find_deep(name):
    # Repeater/ListView delegate items are not reachable via QObject.findChild.
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None
def scroll_to(obj):
    scroll = find("propertyScroll"); flick = scroll.property("contentItem")
    point = obj.mapToScene(QPointF(0,0)); top = scroll.mapToScene(QPointF(0,0))
    offset = point.y() - top.y() - scroll.height()*0.4
    flick.setProperty("contentY", max(0, min(flick.property("contentHeight")-flick.property("height"), flick.property("contentY")+offset)))
    QTest.qWait(80)
def click(name, scroll=True):
    obj = find(name)
    assert obj.property("enabled") and obj.property("visible"), name
    if scroll: scroll_to(obj)
    point = obj.mapToScene(QPointF(obj.width()/2, obj.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point); QTest.qWait(80)
def idle():
    return not editor.busy and not editor._active and not editor._queue and not editor._pending_render and not editor._timer.isActive()
def wait_ai(condition, seconds=240, retry=None):
    # Cloud hiccups (rate limits / transient retries) must not fail the walk-through.
    try:
        wait_for(condition, seconds=seconds)
    except AssertionError:
        note("  (ai step slow/failed once: " + editor.status + "; retrying)")
        if retry is not None:
            retry()
        wait_for(condition, seconds=seconds)
def shot(name):
    QTest.qWait(300); assert window.grabWindow().save(str(shots/f"{name}.png"))

editor.loadDemo(); wait_for(lambda: editor.hasImage and settled(editor) and window.property("previewReady"))
wait_for(idle)

# ---- A: text selection through the redesigned pane (live AI)
editor.selection.clearPick(); QTest.qWait(150)
field = find("selectionDescriptionInput"); scroll_to(field)
field.setProperty("text", "选中天空，不包含树枝")
click("aiSelectionButton")
wait_ai(lambda: editor.hasSelectionDraft and idle() and window.property("selectionPreviewReady"), retry=lambda: click("aiSelectionButton"))
note("A1 draft: " + editor.draftLabel + " | " + editor.selectionQuality)
shot("A1-text-selection")
assert find("draftStateCaption").property("text").startswith(("暂存", "当前范围"))

click("refineAutoButton")
wait_for(lambda: idle() and not editor.pixelBusy, seconds=240)
note("A2 refined: " + editor.selectionQuality)
shot("A2-smart-refine")

click("moreOutputButton", scroll=False)
click("acceptSelectionButton", scroll=False)
wait_for(lambda: not editor.hasSelectionDraft and idle())
note("A3 replaced layer mask: " + editor._layer()["mask"]["label"])
shot("A3-replace-mask")

editor.selection.chooseTool("rect")
editor.drawDraft("rect","replace",[[.45,.55],[.85,.95]],.02)
wait_for(lambda: idle() and window.property("selectionPreviewReady"))
click("selectionToLayerButton", scroll=False)
wait_for(lambda: not editor.hasSelectionDraft and idle())
note("A4 new layer: " + str(len(editor.layers)) + " layers, active=" + editor.activeLayerName)
shot("A4-new-layer")
editor.undo(); wait_for(idle)
assert len(editor.layers) == 1, editor.layers
note("A5 undo ok")

# ---- B: region draft through chat (live AI)
mode = find("chatModeBox"); mode.setProperty("currentIndex", 2)
prompt = find("descriptionInput"); prompt.setProperty("text", "木屋提亮，天空和树林分别压暗")
click("applyDescriptionButton", scroll=False)
wait_ai(lambda: editor.hasRegionDraft and idle() and window.property("selectionPreviewReady"), retry=lambda: click("applyDescriptionButton", scroll=False))
note("B1 regions: " + str(len(editor.regionDrafts)) + " | " + editor.regionSummary[:40])
shot("B1-regions")
click("regionrefineAutoButton")
wait_for(lambda: idle(), seconds=240)
note("B2 region refined: " + editor.regionDrafts[0]["quality"][:40])
shot("B2-region-refine")
layers_before = len(editor.layers)
click("selectionToLayerButton", scroll=False)
wait_for(lambda: not editor.hasRegionDraft and idle())
note("B3 accepted regions: +%d layers" % (len(editor.layers)-layers_before))
shot("B3-regions-accepted")

# ---- C: other features polish pass
editor.selection.pickLayer(editor.activeLayerId); QTest.qWait(150)
click("groupLayerButton", scroll=False)
wait_for(idle)
click("addLayerButton", scroll=False)
wait_for(idle)
name_input = find("layerNameInput"); scroll_to(name_input)
name_input.forceActiveFocus()
name_input.setProperty("text", "自测层")
QTest.keyClick(window, Qt.Key_Return)
wait_for(lambda: editor.activeLayerName == "自测层")
note("C1 rename ok: " + editor.activeLayerName)
row = find_deep("layerSelect_" + editor.activeLayerId)
pt = row.mapToScene(QPointF(row.width()/2, row.height()/2)).toPoint()
QTest.mouseClick(window, Qt.RightButton, Qt.NoModifier, pt)
QTest.qWait(200)
click("layerMenuUngroup", scroll=False)
wait_for(idle)
note("C2 move out ok, parent=" + repr(editor.activeParentId))
slider = find_deep("parameter_exposure"); assert slider is not None
scroll_to(slider)
point = slider.mapToScene(QPointF(slider.width()*0.7, slider.height()/2)).toPoint()
QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, point)
QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, point)
wait_for(idle)
assert editor.parameters["exposure"] != 0, editor.parameters["exposure"]
note("C3 exposure slider drag ok: " + str(editor.parameters["exposure"]))
click("undoButton", scroll=False); wait_for(idle)
assert editor.parameters["exposure"] == 0
note("C4 undo button ok")

# ---- D: live AI edit + advice on the current layer
mode.setProperty("currentIndex", 0)
prompt.setProperty("text", "整体稍微提亮一点")
click("applyDescriptionButton", scroll=False)
wait_ai(lambda: idle() and editor._pending_request is None, retry=lambda: click("applyDescriptionButton", scroll=False))
note("D1 edit applied, exposure=" + str(editor.parameters["exposure"]))
shot("D1-ai-edit")
mode.setProperty("currentIndex", 1)
prompt.setProperty("text", "给我一条修图建议")
click("applyDescriptionButton", scroll=False)
wait_ai(lambda: idle() and editor._pending_request is None, retry=lambda: click("applyDescriptionButton", scroll=False))
proposed = [m for m in editor.conversation if m["state"]=="proposed"]
note("D2 advice proposed: " + str(len(proposed)) + " (model may answer advice as a direct edit)")
if proposed:
    editor.applyAdvice(proposed[-1]["id"]); wait_for(idle)
    note("D3 advice applied")
else:
    last = [m for m in editor.conversation if m["role"]=="assistant"][-1]
    note("D3 advice outcome state: " + last["state"])
shot("D2-advice")

# ---- E: v1.8 features through the real UI
editor.selection.pickLayer(editor.activeLayerId); QTest.qWait(150)
click("autoAdjustButton", scroll=False)
wait_for(idle)
note("E1 auto adjust: " + json.dumps({k: v for k, v in editor.parameters.items() if v}, ensure_ascii=False))
shot("E1-auto")
editor.selection.chooseTool("heal")
area = find("selectionMouse")
a = area.mapToScene(QPointF(area.width()*0.3, area.height()*0.4)).toPoint()
b = area.mapToScene(QPointF(area.width()*0.4, area.height()*0.5)).toPoint()
QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, a)
QTest.mouseMove(window, b, 40)
QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, b)
wait_for(idle)
heal_ops = len(editor._layer().get("heal", {}).get("ops", []))
note("E2 heal stroke ops: " + str(heal_ops))
assert heal_ops == 1
editor.undo(); wait_for(idle)
assert "heal" not in editor._layer()
editor.beginSelection("empty")
editor.drawDraft("rect", "replace", [[.3, .3], [.6, .6]], .02)
wait_for(lambda: idle() and window.property("selectionPreviewReady"))
editor.setEdgeShift(-2); editor.finishSelectionGesture()
assert editor._candidate["edge_shift"] == -2
note("E3 edge shift ok")
click("inpaintButton")
wait_for(idle, seconds=120)
note("E4 inpaint layer: " + json.dumps(editor._layers[-1].get("inpaint"), ensure_ascii=False))
shot("E4-inpaint")
editor.undo(); wait_for(idle)
assert "inpaint" not in editor._layers[-1]

note("WARNINGS: " + repr(warnings))
window.close(); editor.close()
print("SELFTEST DONE", flush=True)
