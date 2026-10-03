import QtQuick

// Keep texture metadata with the displayed frame, rather than the next request.
Image {
    required property var editor
    property bool sourceFrame: false
    property var requestedFrame: null
    property var displayedRect: [0, 0, 0, 0]
    property int displayedGeneration: -1
    property int displayedRevision: -1
    property string displayedPhoto: ""
    property bool hasFrame: false
    readonly property bool frameCurrent: hasFrame && displayedPhoto === editor.originalUrl
        && (sourceFrame || displayedGeneration === editor.documentGeneration && displayedRevision === editor.detailVersion)
    cache: false; asynchronous: true; retainWhileLoading: true; fillMode: Image.Stretch
    onSourceChanged: {
        requestedFrame = {
            url: source.toString(), rect: Array.from(editor.detailRect),
            generation: editor.detailGeneration, revision: editor.detailRevision,
            photo: editor.originalUrl
        }
        if (source.toString().length === 0) hasFrame = false
    }
    onStatusChanged: {
        if (status === Image.Ready && requestedFrame && requestedFrame.url === source.toString()) {
            displayedRect = requestedFrame.rect
            displayedGeneration = requestedFrame.generation
            displayedRevision = requestedFrame.revision
            displayedPhoto = requestedFrame.photo
            hasFrame = true
        } else if (status === Image.Error || status === Image.Null) hasFrame = false
    }
}
