"""Raw upload/download endpoints shared by session and task owners."""

from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from ..attachments import AttachmentStore, IMAGE_MIMES, MAX_FILE_BYTES


def route(handler, state, respond, method: str) -> bool:
    url = urlparse(handler.path)
    parts = url.path.strip("/").split("/")
    if not (len(parts) in (4, 5) and parts[0] == "api" and
            parts[1] in ("sessions", "tasks") and parts[3] == "attachments"):
        return False
    if (method == "POST" and len(parts) != 4) or (method == "GET" and len(parts) != 5):
        return False
    try:
        kind, owner = parts[1:3]
        store = AttachmentStore(state["store"].home)
        store.owner_path(kind, owner)
        if kind == "sessions":
            if state["sessions"].load(owner) is None:
                raise FileNotFoundError("session not found")
        else:
            task = state["tasks"].get(owner)
            if method == "POST" and (task.archived or task.state in ("completed", "cancelled")):
                raise ValueError("请先重新启用任务，再上传附件")
        if method == "POST":
            query = parse_qs(url.query)
            size = int(handler.headers.get("Content-Length") or "-1")
            if not 0 <= size <= MAX_FILE_BYTES:
                raise ValueError("附件上限为 25 MiB")
            item = store.save(kind, owner, query.get("name", ["attachment.bin"])[-1],
                              handler.headers.get("Content-Type", ""), handler.rfile, size)
            respond(handler, 201, item)
        else:
            item = store.get(kind, owner, unquote(parts[4]))
            data = Path(item["original_path"]).read_bytes()
            handler.send_response(200)
            handler.send_header("Content-Type", item["mime"] if item["mime"] in IMAGE_MIMES else "application/octet-stream")
            handler.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(item["name"]))
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()
            handler.wfile.write(data)
    except FileNotFoundError as error:
        handler.close_connection = True
        respond(handler, 404, {"error": str(error)})
    except ValueError as error:
        handler.close_connection = True
        respond(handler, 400, {"error": str(error)})
    except OSError as error:
        handler.close_connection = True
        respond(handler, 500, {"error": str(error)})
    return True
