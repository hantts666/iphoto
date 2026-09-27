"""Unsent chat text must follow its photo rather than whichever photo is open."""

import json

from PIL import Image

from iphoto.document import read_project, validate_project
from iphoto.workspace import Editor
from iphoto.workspace import ROOT
from test_ai import wait_for
from test_editor import settled
from test_import_export import ui


def test_chat_draft_does_not_cross_photos(ui):
    editor, window, find, warnings, tmp_path = ui
    first = tmp_path / "source.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (300, 200), (100, 50, 25)).save(second)
    field = find("descriptionInput")
    field.setProperty("text", "只调整第一张照片的天空")
    editor.openImage(str(second))
    wait_for(lambda: editor.hasImage and settled(editor) and editor.imageName == "second.png")
    assert field.property("text") == ""
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-chat-draft-photo-switch.png"))
    editor.openImage(str(first))
    wait_for(lambda: editor.hasImage and settled(editor) and editor.imageName == "source.png")
    assert field.property("text") == "只调整第一张照片的天空"
    assert not warnings


def test_chat_drafts_follow_distinct_projects_of_same_photo(ui):
    editor, window, find, warnings, tmp_path = ui
    first = tmp_path / "look-a.iphoto"
    second = tmp_path / "look-b.iphoto"
    editor.saveProjectAsync(str(first))
    wait_for(lambda: not editor.savingProject and editor.projectPath == str(first))
    editor.saveProjectAsync(str(second))
    wait_for(lambda: not editor.savingProject and editor.projectPath == str(second))
    field = find("descriptionInput")
    mode = find("chatModeBox")
    editor.openProject(str(first))
    wait_for(lambda: settled(editor) and editor.projectPath == str(first))
    mode.setProperty("currentIndex", 1)
    field.setProperty("text", "第一版的天空更亮")
    editor.openProject(str(second))
    wait_for(lambda: settled(editor) and editor.projectPath == str(second))
    assert field.property("text") == ""
    assert mode.property("currentIndex") == 0
    mode.setProperty("currentIndex", 2)
    field.setProperty("text", "第二版保留暗部")
    editor.openProject(str(first))
    wait_for(lambda: settled(editor) and editor.projectPath == str(first))
    assert field.property("text") == "第一版的天空更亮"
    assert mode.property("currentIndex") == 1
    editor.openProject(str(second))
    wait_for(lambda: settled(editor) and editor.projectPath == str(second))
    assert field.property("text") == "第二版保留暗部"
    assert mode.property("currentIndex") == 2
    assert not warnings


def test_failed_project_open_keeps_current_chat_draft(ui):
    editor, window, find, warnings, tmp_path = ui
    field = find("descriptionInput")
    field.setProperty("text", "修好项目路径后再试")
    editor.openProject(str(tmp_path / "missing.iphoto"))
    assert field.property("text") == "修好项目路径后再试"
    assert editor.conversationDraft == "修好项目路径后再试"
    assert not warnings


def test_chat_draft_moves_when_project_is_first_saved(ui):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "draft.iphoto"
    field = find("descriptionInput")
    field.setProperty("text", "先保留这段未发出的指令")
    editor.saveProjectAsync(str(project))
    wait_for(lambda: not editor.savingProject and editor.projectPath == str(project))
    assert field.property("text") == "先保留这段未发出的指令"
    editor.openProject(str(project))
    wait_for(lambda: settled(editor) and editor.projectPath == str(project))
    assert field.property("text") == "先保留这段未发出的指令"
    assert not warnings


def test_unsent_chat_draft_survives_project_reopen_in_new_editor(ui, ai_store):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "unfinished.iphoto"
    find("chatModeBox").setProperty("currentIndex", 1)
    find("descriptionInput").setProperty("text", "明天继续调整天空和暗部")
    editor.saveProjectAsync(str(project))
    wait_for(lambda: not editor.savingProject and editor.projectPath == str(project))
    assert read_project(project)["conversation_draft"] == "明天继续调整天空和暗部"
    assert read_project(project)["conversation_draft_mode"] == "advice"
    fresh = Editor(ai_store=ai_store)
    try:
        fresh.openProject(str(project))
        wait_for(lambda: fresh.hasImage and settled(fresh))
        assert fresh.conversationDraft == "明天继续调整天空和暗部"
        assert fresh.conversationDraftMode == "advice"
    finally:
        fresh.close()
    assert not warnings


def test_draft_only_change_is_recoverable_after_restart(tmp_path, qt_app, ai_store):
    source = tmp_path / "photo.png"
    Image.new("RGB", (300, 200), "blue").save(source)
    editor = Editor(ai_store=ai_store)
    try:
        editor.openImage(str(source))
        wait_for(lambda: editor.hasImage and settled(editor))
        editor.setConversationDraft("尚未发送的局部调整要求")
        assert editor.dirty
    finally:
        editor.close()
    reopened = Editor(ai_store=ai_store)
    try:
        assert reopened.canRecover
        reopened.recoverLatest()
        wait_for(lambda: reopened.hasImage and settled(reopened))
        assert reopened.conversationDraft == "尚未发送的局部调整要求"
        assert reopened.projectPath == ""
    finally:
        reopened.close()


def test_cleared_draft_stays_cleared_after_project_reopen(ui, ai_store):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "cleared.iphoto"
    field = find("descriptionInput")
    field.setProperty("text", "准备删除的草稿")
    editor.saveProjectAsync(str(project))
    wait_for(lambda: not editor.savingProject and editor.projectPath == str(project))
    field.setProperty("text", "")
    assert editor.dirty
    editor.saveProjectAsync(str(project))
    wait_for(lambda: not editor.savingProject and not editor.dirty)
    assert read_project(project)["conversation_draft"] == ""
    fresh = Editor(ai_store=ai_store)
    try:
        fresh.openProject(str(project))
        wait_for(lambda: fresh.hasImage and settled(fresh))
        assert fresh.conversationDraft == ""
    finally:
        fresh.close()
    assert not warnings


def test_legacy_project_without_chat_draft_opens_empty(ui, ai_store):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "older.iphoto"
    editor.saveProject(str(project))
    payload = json.loads(project.read_text(encoding="utf-8"))
    payload["schema_version"] = "1.6"
    payload.pop("conversation_draft")
    payload.pop("conversation_draft_mode")
    project.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert validate_project(payload)["conversation_draft"] == ""
    assert validate_project(payload)["conversation_draft_mode"] == "edit"
    fresh = Editor(ai_store=ai_store)
    try:
        fresh.openProject(str(project))
        wait_for(lambda: fresh.hasImage and settled(fresh))
        assert fresh.conversationDraft == ""
        assert fresh.conversationDraftMode == "edit"
    finally:
        fresh.close()
    assert not warnings


def test_saved_chat_draft_redacts_key_like_text(ui):
    editor, window, find, warnings, tmp_path = ui
    project = tmp_path / "safe-draft.iphoto"
    secret = "sk-" + "a" * 24
    find("descriptionInput").setProperty("text", "临时粘贴 " + secret)
    editor.saveProject(str(project))
    stored = project.read_text(encoding="utf-8")
    assert secret not in stored
    assert "[Key 已隐藏]" in read_project(project)["conversation_draft"]
    assert not warnings


def test_chat_draft_restores_its_mode_after_photo_switch(ui):
    editor, window, find, warnings, tmp_path = ui
    first = tmp_path / "source.png"
    second = tmp_path / "other.png"
    Image.new("RGB", (300, 200), "orange").save(second)
    mode = find("chatModeBox")
    field = find("descriptionInput")
    mode.setProperty("currentIndex", 1)
    field.setProperty("text", "只分析第一张照片")
    editor.openImage(str(second))
    wait_for(lambda: editor.hasImage and settled(editor) and editor.imageName == "other.png")
    mode.setProperty("currentIndex", 2)
    field.setProperty("text", "把第二张照片自动分区")
    editor.openImage(str(first))
    wait_for(lambda: editor.hasImage and settled(editor) and editor.imageName == "source.png")
    assert field.property("text") == "只分析第一张照片"
    assert mode.property("currentIndex") == 1
    assert window.grabWindow().save(str(ROOT / "artifacts/ux-chat-draft-mode-restore.png"))
    assert not warnings


def test_mode_navigation_without_draft_does_not_mark_photo_edited(ui):
    editor, window, find, warnings, tmp_path = ui
    assert not editor.dirty
    find("chatModeBox").setProperty("currentIndex", 1)
    assert editor.conversationDraft == ""
    assert not editor.dirty
    assert not warnings
