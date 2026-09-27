.pragma library

// Keep path fields readable on Windows and seed native file dialogs beside the target.
function localPath(value) {
    var path = String(value).trim()
    if (path.indexOf("file:///") === 0) path = decodeURIComponent(path.slice(8))
    return path.replace(/\//g, "\\")
}

function folderUrl(value) {
    var normalized = localPath(value).replace(/\\/g, "/")
    var slash = normalized.lastIndexOf("/")
    if (slash < 0) return ""
    return directoryUrl(normalized.slice(0, slash + 1))
}

function directoryUrl(value) {
    var folder = localPath(value).replace(/\\/g, "/")
    if (folder === "") return ""
    if (folder.charAt(folder.length - 1) !== "/") folder += "/"
    var encoded = encodeURI(folder).replace(/#/g, "%23").replace(/\?/g, "%3F")
    return folder.indexOf("//") === 0 ? "file:" + encoded : "file:///" + encoded
}
