"""Conversation rows stay visible and refresh after append, advice, and reopen."""

from test_ai import wait_for
from test_editor import settled
from test_import_export import ui


def _descendant_texts(item):
    stack = [item]
    values = []
    while stack:
        child = stack.pop()
        value = child.property("text")
        if isinstance(value, str):
            values.append(value)
        stack.extend(child.childItems())
    return values


def test_conversation_rows_append_refresh_and_restore(ui):
    editor, window, find, warnings, tmp_path = ui
    chat = find("conversationList")
    assert chat.property("count") == 0
    recipe = dict(editor.parameters)
    recipe["exposure"] = .4
    binding = editor._document_signature()
    message = editor._message(
        "assistant", "建议增加曝光", state="proposed", recipe=recipe,
        origin={"binding": binding, "layer_id": editor._selected,
                "layer_name": editor.activeLayerName, "model": "测试模型"},
    )
    wait_for(lambda: chat.property("count") == 1)
    assert "建议未应用" in " ".join(_descendant_texts(find("msg_" + message["id"])))
    editor.applyAdvice(message["id"])
    wait_for(lambda: chat.property("count") == 2)
    assert editor.conversationMessageState(message["id"]) == "applied"
    assert "已应用" in " ".join(_descendant_texts(find("msg_" + message["id"])))
    project = tmp_path / "conversation.iphoto"
    editor.saveProject(str(project))
    editor.openProject(str(project))
    wait_for(lambda: settled(editor) and editor.projectPath == str(project))
    assert chat.property("count") == 2
    assert "已应用" in " ".join(_descendant_texts(find("msg_" + message["id"])))
    assert not warnings
