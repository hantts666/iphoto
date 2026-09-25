"""Opt-in real API QA. Reads the saved Windows credential; never accepts/logs a key."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QNetworkRequest
from PySide6.QtTest import QTest
from PIL import Image
from iphoto.ai import AIController
from iphoto.document import empty_mask, overlay_mask
from iphoto.engine import Recipe


class Recorder(AIController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostics = {}
        self.history = []

    def _finish(self, reply, context):
        if reply is self._reply:
            context["body"].extend(bytes(reply.readAll()))
            self.diagnostics = {
                "http_status": reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
            }
            try:
                data = json.loads(
                    context["body"]
                    .decode("utf-8")
                    .replace(context["secret"], "[hidden]")
                )
                choice = data.get("choices", [{}])[0]
                self.diagnostics.update(
                    finish_reason=choice.get("finish_reason"),
                    usage=data.get("usage"),
                    response=choice.get("message", {}).get("content", ""),
                )
            except (ValueError, IndexError):
                self.diagnostics["response"] = "unavailable"
            self.history.append(dict(self.diagnostics))
        super()._finish(reply, context)


def workflow(stage, cached=False, source_project=None):
    from iphoto import workspace
    from iphoto.document import write_project, raster_mask

    workspace.AIController = Recorder
    editor = workspace.Editor()
    if not editor.ai.ready or editor.ai.settings.provider not in (
        "qianwen_token_plan",
        "qianwen",
        "qwen",
    ):
        editor.close()
        raise SystemExit("Saved Qianwen configuration unavailable.")
    folder = ROOT / "artifacts" / ("v14-live-" + stage)
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        "provider": editor.ai.settings.provider,
        "model": editor.ai.settings.model,
        "source": "bundled lake.jpg",
        "cases": [],
    }

    def wait(condition, seconds=100):
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            QTest.qWait(30)
            if condition():
                return
        raise RuntimeError("Workflow QA deadline")

    def idle():
        return (
            not editor.busy
            and not editor._active
            and not editor._pending_render
            and not editor._timer.isActive()
        )

    try:
        editor.openProject(
            str(
                source_project
                or ROOT / "artifacts/v14-live-workflow/real-workflow.iphoto"
            )
        ) if cached else editor.loadDemo()
        wait(lambda: editor.hasImage and idle())
        for index, prompt in enumerate(
            ["选中天空，不包含树枝、山峰和树林", "前景水上的木屋和栈道选在一起"]
        ):
            if editor.hasSelectionDraft:
                editor.discardSelection()
                wait(idle)
            start = time.monotonic()
            count = len(editor.ai.history)
            editor.selectByDescription(prompt)
            wait(
                lambda: (
                    idle()
                    and editor._pending_request is None
                    and not editor._scene_followup
                )
            )
            case = {
                "prompt": prompt,
                "seconds": round(time.monotonic() - start, 2),
                "selected": editor.hasSelectionDraft,
                "objects": len(editor.sceneObjects),
                "requests": editor.ai.history[count:],
                "status": editor.status,
            }
            if editor.hasSelectionDraft:
                picture = Image.open(ROOT / "assets/lake.jpg").convert("RGBA")
                picture.thumbnail((1000, 1000))
                Image.alpha_composite(
                    picture, overlay_mask(editor._candidate, picture.size)
                ).save(folder / f"{index}-selection.png")
                raster_mask(editor._candidate, picture.size).save(
                    folder / f"{index}-mask.png"
                )
            report["cases"].append(case)
            (folder / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {k: case[k] for k in ("seconds", "selected", "objects", "status")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        # A replayable real-API project opens with the last draft and object list.
        write_project(
            folder / "real-workflow.iphoto", editor._payload(), overwrite=True
        )
    finally:
        editor.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--stage", default="baseline")
    parser.add_argument(
        "--project", type=Path, help="Existing catalog for workflow_cached"
    )
    parser.add_argument(
        "--mode",
        choices=["selection", "scene", "workflow", "workflow_cached"],
        default="selection",
    )
    args = parser.parse_args()
    app = QGuiApplication([])
    app.setOrganizationName("iPhoto")
    app.setApplicationName("iPhoto")
    if args.mode in ("workflow", "workflow_cached"):
        return workflow(args.stage, args.mode == "workflow_cached", args.project)
    ai = Recorder()
    if not ai.ready or ai.settings.provider not in (
        "qianwen_token_plan",
        "qianwen",
        "qwen",
    ):
        raise SystemExit(
            "Saved Qianwen configuration is not available in this Windows session."
        )
    folder = ROOT / "artifacts" / ("v14-live-" + args.stage)
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        "provider": ai.settings.provider,
        "model": ai.settings.model,
        "source": "bundled assets/lake.jpg",
        "cases": [],
    }
    outcomes = []
    ai.planReady.connect(lambda result, generation: outcomes.append({"result": result}))
    ai.failure.connect(lambda message: outcomes.append({"error": message}))
    prompts = (
        ["选中天空，不包含山峰和树林", "只选前景的木屋，屋顶和墙面都要"]
        if args.mode == "selection"
        else ["分析画面中的主要可见元素，按语义类别列出可以独立选择的对象。"]
    )
    for index, prompt in enumerate(prompts):
        outcomes.clear()
        workspace = {}
        if args.stage == "baseline":
            import base64
            from io import BytesIO

            buf = BytesIO()
            Image.new("L", (512, 341), 255).save(buf, format="PNG")
            workspace = {
                "selection": empty_mask(True),
                "selection_image": "data:image/png;base64,"
                + base64.b64encode(buf.getvalue()).decode("ascii"),
            }
        start = time.monotonic()
        ai.plan(
            prompt,
            Recipe().to_dict(),
            [],
            ROOT / "assets/lake.jpg",
            index,
            args.mode,
            workspace,
        )
        while not outcomes and time.monotonic() - start < 70:
            QTest.qWait(50)
        case = {
            "prompt": prompt,
            "seconds": round(time.monotonic() - start, 2),
            **ai.diagnostics,
            **(outcomes[0] if outcomes else {"error": "QA deadline"}),
        }
        report["cases"].append(case)
        result = case.get("result", {})
        if "mask" in result:
            with Image.open(ROOT / "assets/lake.jpg") as opened:
                picture = opened.convert("RGBA")
            picture.thumbnail((1000, 1000))
            picture = Image.alpha_composite(
                picture, overlay_mask(result["mask"], picture.size)
            )
            picture.save(folder / f"{index}-selection.png")
        (folder / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "case": index,
                    "seconds": case["seconds"],
                    "status": result.get("status"),
                    "error": case.get("error"),
                    "http_status": case.get("http_status"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    ai.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
