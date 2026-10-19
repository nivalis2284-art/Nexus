"""Read only the attachments actually sent to the current conversation."""

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolSpec


class ReadAttachmentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attachment_id: str = Field(min_length=1)
    offset: int = Field(default=1, ge=1, description="First text line, starting at 1")
    limit: int = Field(default=200, ge=1, le=1000)


def read(context, args):
    if args["attachment_id"] not in context.attachment_ids:
        raise PermissionError("attachment was not sent to this conversation")
    store, kind, owner = context.attachments
    result = store.read(kind, owner, args["attachment_id"], args["offset"], args["limit"])
    if result["attachment"]["status"] in ("error", "stored"):
        result.update(ok=False, error="attachment_not_readable")
    result["content"] = (result["attachment"]["name"] + " · " + result["attachment"]["note"] + "\n" + result["content"] +
                         f"\nNext offset: {result.get('next_offset')}; total lines: {result.get('total_lines')}. " +
                         result.get("note", ""))
    return result


TOOLS = [ToolSpec(
    "read_attachment", "Read uploaded document text with line numbers. Use next_offset to continue. "
    "PDF pages, workbook sheets/cells and slide numbers are labeled. Does not execute files; "
    "unsupported or unreadable files return their original path and limitations.",
    ReadAttachmentArgs, read, read_only=True, requires=("attachments",),
)]
