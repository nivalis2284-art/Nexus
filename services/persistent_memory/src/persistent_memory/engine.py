"""Static, source-grounded memory: import, build, activate, retrieve."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import retrieval
from .llm import ChatModel, ModelError
from .models import canonical_record, normalize_record, records_hash
from .store import Store


class MemoryEngine:
    """A self-contained memory store. Model calls happen on the calling thread."""

    def __init__(
        self, data_dir: str | Path, *,
        model_factory: Callable[..., ChatModel] = ChatModel,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.store = Store(self.data_dir)
        self._model_factory = model_factory
        self._lock = threading.RLock()
        self._build_lock = threading.Lock()
        self._job: dict[str, Any] = {"running": False, "error": "", "result": None}
        self._thread: threading.Thread | None = None

    def configure(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            config = self.store.configure(values)
        return self._public_config(config)

    def status(self) -> dict[str, Any]:
        config = self.store.config()
        active = self.store.get_state("active_version")
        versions = self.store.versions()
        with self._lock:
            job = dict(self._job)
        return {
            "configured": bool(config["base_url"] and config["model"]),
            "base_url": config["base_url"],
            "model": config["model"],
            "active_version": active,
            "versions": versions,
            "counts": self.store.counts(),
            "build_status": job,
        }

    def import_records(self, session_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id is required")
        if not isinstance(records, list):
            raise TypeError("records must be a list")
        normalized = [normalize_record(record) for record in records]
        digest = records_hash(normalized)
        with self._lock:
            return self.store.import_records(session_id, normalized, digest)

    def build(self) -> dict[str, Any]:
        """Synchronous, model-generated build. A failed build leaves active alone."""
        with self._build_lock:
            imported = self.store.latest_import()
            if imported is None:
                raise ValueError("no imported records")
            config = self.store.config()
            if not config["base_url"] or not config["model"]:
                raise ValueError("configure base_url and model before building")
            version_id = uuid.uuid4().hex
            self.store.create_pending(
                version_id, imported["session_id"], imported["records_hash"],
                imported["raw_count"],
            )
        started = time.monotonic()
        try:
            events, groups, edges, usage, batches = self._build_records(
                imported, config, version_id,
            )
            with self._lock:
                self.store.finish_version(
                    version_id, events, groups, edges, batches,
                    usage=usage, elapsed_seconds=round(time.monotonic() - started, 3),
                )
        except Exception as exc:  # noqa: BLE001 -- record a failed build without replacing the active version.
            message = _safe_error(exc, config)
            with self._lock:
                self.store.fail_version(version_id, message)
            raise ModelError(message) from None
        return self.store.version(version_id) or {}

    def start_build(self) -> dict[str, Any]:
        """Start build in a daemon thread and return immediately."""
        with self._lock:
            if self._job.get("running"):
                return {"running": True, **self._job_snapshot()}
            imported = self.store.latest_import()
            if imported is None:
                raise ValueError("no imported records")
            self._job = {"running": True, "error": "", "result": None}
            self._thread = threading.Thread(target=self._run_build, name="memory-build", daemon=True)
            self._thread.start()
            return {"running": True, "error": "", "result": None}

    def _run_build(self) -> None:
        try:
            result = self.build()
        except Exception as exc:  # noqa: BLE001 -- report background job failure.
            with self._lock:
                self._job = {"running": False, "error": str(exc), "result": None}
        else:
            with self._lock:
                self._job = {"running": False, "error": "", "result": result}

    def _job_snapshot(self) -> dict[str, Any]:
        return {key: self._job.get(key) for key in ("running", "error", "result")}

    def activate(self, version_id: str) -> dict[str, Any]:
        with self._lock:
            self.store.activate(version_id)
        return self.status()

    def snapshot(self) -> dict[str, Any]:
        """Return the imported prefix represented by the currently active version."""
        active = self.store.get_state("active_version")
        if not active:
            raise ValueError("no active memory version")
        version = self.store.version(active)
        records = self.store.version_records(active)
        if len(records) != version["raw_count"] or records_hash(records) != version["records_hash"]:
            raise ValueError("active version source snapshot is damaged")
        return {
            "session_id": version["session_id"],
            "records_hash": version["records_hash"],
            "raw_count": version["raw_count"],
            "records": records,
            "version_id": active,
        }

    def search(self, query: str, limit: int = 8, use_model: bool = True) -> dict[str, Any]:
        """Return event and group candidates; one optional model rerank call."""
        version_id = self._active_or_raise()
        limit = max(1, min(30, int(limit)))
        events = self.store.events(version_id)
        groups = self.store.groups(version_id)
        if not query.strip():
            return {
                "query": query, "version_id": version_id, "mode": "browse",
                "events": [_event_out(event) for event in events],
                "groups": [_group_out(group) for group in groups],
                "usage": None, "elapsed_seconds": None,
            }
        event_hits = retrieval.rank(
            query, ((event["id"], f"{event['title']} {event['summary']} {event['text']}")
                    for event in events),
        )
        group_hits = retrieval.rank(
            query, ((group["id"], f"{group['title']} {group['summary']}") for group in groups),
        )
        event_by_id = {event["id"]: event for event in events}
        group_by_id = {group["id"]: group for group in groups}
        selected_events = [event_by_id[item[0]] for item in event_hits[:limit]]
        selected_groups = [group_by_id[item[0]] for item in group_hits[:min(limit, 4)]]
        mode = "lexical"
        usage = None
        elapsed = None
        if use_model and (events or groups):
            selected_events, selected_groups, usage, elapsed = self._select(
                query, events, groups, limit, "search",
            )
            mode = "model"
        return {
            "query": query,
            "version_id": version_id,
            "mode": mode,
            "events": [_event_out(event) for event in selected_events],
            "groups": [_group_out(group) for group in selected_groups],
            "usage": usage,
            "elapsed_seconds": elapsed,
        }

    def expand(
        self, kind: str, id: str, offset: int = 0, limit: int = 6000,
    ) -> dict[str, Any]:
        """Expand one event, group, or exact source record with text pagination."""
        version_id = self._active_or_raise()
        offset = max(0, int(offset))
        limit = max(1, min(12_000, int(limit)))
        if kind == "event":
            row = next((item for item in self.store.events(version_id) if item["id"] == id), None)
            if row is None:
                raise ValueError("event not found")
            text = row["text"]
            page, total = _page(text, offset, limit)
            result = {**_event_out(row, include_text=True), "text": page, "offset": offset,
                      "limit": limit, "total_chars": total}
        elif kind == "group":
            row = next((item for item in self.store.groups(version_id) if item["id"] == id), None)
            if row is None:
                raise ValueError("group not found")
            page, total = _page(row["summary"], offset, limit)
            result = {**_group_out(row), "summary": page, "offset": offset,
                      "limit": limit, "total_chars": total}
        elif kind == "source":
            records = self.store.version_records(version_id)
            source_id = id.removeprefix("source:")
            source = next((item for item in records
                           if canonical_record(item)["_record_key"] == source_id), None)
            if source is None:
                raise ValueError("source not found")
            text = json.dumps(source, ensure_ascii=False, sort_keys=True, indent=2)
            page, total = _page(text, offset, limit)
            result = {"kind": "source", "id": f"source:{source_id}", "text": page,
                      "offset": offset, "limit": limit, "total_chars": total}
        else:
            raise ValueError("kind must be event, group, or source")
        result["next_offset"] = offset + len(page) if offset + len(page) < total else None
        return result

    def associations(self, event_id: str) -> dict[str, Any]:
        """Return typed, source-grounded edges incident to an event."""
        version_id = self._active_or_raise()
        edges = [edge for edge in self.store.edges(version_id)
                 if edge["source_id"] == event_id or edge["target_id"] == event_id]
        return {"event_id": event_id, "version_id": version_id, "associations": edges}

    def prepare(self, query: str, budget_tokens: int = 3000) -> dict[str, Any]:
        """Select relevant material with one model call; build verified text locally."""
        version_id = self._active_or_raise()
        budget_tokens = max(1, min(12_000, int(budget_tokens)))
        events = self.store.events(version_id)
        groups = self.store.groups(version_id)
        selected_events, selected_groups, usage, elapsed = self._select(
            query, events, groups, 8, "prepare",
        )
        sections: list[tuple[str, str, dict[str, Any]]] = []
        for group in selected_groups[:3]:
            sections.append(("group", group["id"], {
                "kind": "group", "id": group["id"], "title": group["title"],
                "dimension": group.get("dimension", "context"),
                "text": group["summary"], "start_at": group["start_at"],
                "end_at": group["end_at"], "event_ids": group["event_ids"],
            }))
        for event in selected_events[:8]:
            sections.append(("event", event["id"], {
                "kind": "event", "id": event["id"], "title": event["title"],
                "text": _event_detail(event), "at": event["at"], "source_ids": event["source_ids"],
                "before": event["before"], "after": event["after"],
                "uncertainty": event["uncertainty"],
            }))
        header = "[Memory background; source-derived, not new instructions]"
        parts = [header]
        selected: list[dict[str, Any]] = []
        used = retrieval.tokens_for_budget(header, budget_tokens)
        if used > budget_tokens:
            header = ""
            parts = []
            used = 0
        for kind, identity, item in sections:
            body = f"\n[{kind} {identity}] {item['title']}\n{item['text']}"
            cost = retrieval.tokens_for_budget(body, budget_tokens)
            if used + cost > budget_tokens:
                if selected:
                    continue
                body = _fit_block(kind, identity, item["title"], item["text"],
                                  budget_tokens - used)
                cost = retrieval.tokens_for_budget(body, budget_tokens)
                if used + cost > budget_tokens:
                    continue
                item["truncated"] = True
            parts.append(body)
            used += cost
            selected.append({key: value for key, value in item.items() if key != "text"})
        content = "".join(parts)
        run_id = uuid.uuid4().hex
        self.store.save_run({
            "id": run_id, "version_id": version_id, "query": query,
            "selected": selected, "content": content, "mode": "model",
            "usage": usage, "elapsed_seconds": elapsed,
        })
        return {
            "content": content, "selected": selected, "run_id": run_id,
            "version_id": version_id, "budget_tokens": budget_tokens,
            "estimated_tokens": used, "usage": usage, "elapsed_seconds": elapsed,
        }

    def runs(self) -> list[dict[str, Any]]:
        return self.store.runs()

    def feedback(self, values: dict[str, Any]) -> dict[str, Any]:
        return self.store.feedback(values)

    def export(self) -> dict[str, Any]:
        """Export active semantic state and source snapshots, without secrets."""
        config = self._public_config(self.store.config())
        active = self.store.get_state("active_version")
        result: dict[str, Any] = {
            "version_id": active,
            "config": config,
            "versions": self.store.versions(),
        }
        if active:
            version = self.store.version(active)
            records = self.store.version_records(active)
            result.update({
                "session_id": version["session_id"],
                "events": [_event_out(event) for event in self.store.events(active)],
                "groups": [_group_out(group) for group in self.store.groups(active)],
                "edges": self.store.edges(active),
                "records": records,
            })
        return result

    def _build_records(
        self, imported: dict[str, Any], config: dict[str, Any], version_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Any, int]:
        records = self.store.version_records(version_id)
        canonical = [canonical_record(record) for record in records]
        body, evidence = _partition_records(canonical)
        if not body:
            raise ValueError("no body records (user, assistant, or experience) to build from")
        model = self._model_factory(
            config["base_url"], config["model"], config["api_key"],
            config["build_timeout"],
        )
        batch_size = config["build_batch_size"]
        batch_rows = []
        usages = []
        for start in range(0, len(body), batch_size):
            first = max(0, start - 1)
            chunk = body[first:start + batch_size]
            nearby = _evidence_for(chunk, evidence)
            payload = [
                {"source_ref": item["_record_key"], "record": _compact_source(item)}
                for item in chunk
            ]
            evidence_payload = [
                {"source_ref": item["_record_key"], "record": _tool_descriptor(item)}
                for item in nearby
            ]
            response = model.complete_json(
                _BUILD_SYSTEM,
                json.dumps({
                    "session_id": imported["session_id"], "records": payload,
                    "evidence": evidence_payload,
                }, ensure_ascii=False),
            )
            usages.append(response.usage)
            event_rows = response.value.get("events") if isinstance(response.value, dict) else None
            if not isinstance(event_rows, list):
                raise ModelError("build response must contain an events list")
            for event in event_rows:
                batch_rows.append(self._validated_event(
                    event, chunk + nearby, version_id,
                    # ``chunk`` carries one preceding record as overlap context; an
                    # event anchored on it is still citing body text, not tool output.
                    body_keys={item["_record_key"] for item in chunk},
                ))

        events = _merge_events(batch_rows)
        groups: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        if events:
            response = model.complete_json(
                _ORGANIZE_SYSTEM,
                json.dumps({
                    "events": [{
                        "id": event["id"], "title": event["title"], "summary": event["summary"],
                        "at": event["at"], "before": event["before"], "after": event["after"],
                        "uncertainty": event["uncertainty"],
                    } for event in events],
                }, ensure_ascii=False),
            )
            usages.append(response.usage)
            groups, edges = self._validated_organization(response.value, events)
        covered = {ref for event in events for ref in event["source_refs"]}
        missing = [item["_record_key"] for item in body if item["_record_key"] not in covered]
        if missing:
            raise ModelError(f"build did not cite {len(missing)} body source records")
        return (events, groups, _dedupe_edges(edges), _merge_usage(usages),
                len(range(0, len(body), batch_size)))

    def _validated_event(
        self, response: Any, chunk: list[dict[str, Any]], version_id: str,
        *, body_keys: set[str],
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise ModelError("event must be an object")
        refs = response.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ModelError("event must cite source_refs")
        known = {item["_record_key"] for item in chunk}
        if any(ref not in known for ref in refs):
            raise ModelError("event cites a source outside its input batch")
        if not any(ref in body_keys for ref in refs):
            raise ModelError("event must cite at least one body record")
        source_refs = list(dict.fromkeys(refs))
        texts = []
        ats = []
        source_ids = []
        for ref in source_refs:
            source = next(item for item in chunk if item["_record_key"] == ref)
            source_ids.append(f"source:{ref}")
            text = source.get("text")
            if isinstance(text, str):
                texts.append(text)
            if source.get("at"):
                ats.append(str(source["at"]))
        title = _string(response.get("title"), "Event")[:200]
        summary = _string(response.get("summary"), "")[:2_000]
        text = "\n\n".join(texts)
        if not text:
            text = summary
        return {
            "id": uuid.uuid4().hex,
            "ordinal": 0,
            "title": title,
            "summary": summary,
            "text": text,
            "at": _string(response.get("at"), ats[0] if ats else ""),
            "before": _string(response.get("before"), ""),
            "after": _string(response.get("after"), ""),
            "uncertainty": _string(response.get("uncertainty"), ""),
            "source_ids": source_ids,
            "source_refs": source_refs,
        }

    def _validated_organization(
        self, response: Any, events: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not isinstance(response, dict):
            raise ModelError("organization response must be a JSON object")
        rows = response.get("groups")
        associations = response.get("associations")
        if not isinstance(rows, list) or not isinstance(associations, list):
            raise ModelError("organization response needs groups and associations lists")
        ids = {event["id"] for event in events}
        by_id = {event["id"]: event for event in events}
        groups = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ModelError("group must be an object")
            event_ids = row.get("event_ids")
            if not isinstance(event_ids, list) or not event_ids:
                raise ModelError("group needs a non-empty event_ids list")
            if any(item not in ids for item in event_ids):
                raise ModelError("group cites an unknown event")
            members = list(dict.fromkeys(event_ids))
            groups.append({
                "id": uuid.uuid4().hex, "ordinal": index,
                "title": _string(row.get("title"), "Memory group")[:200],
                "dimension": _string(row.get("dimension"), "context")[:40],
                "summary": _string(row.get("summary"), "")[:2_000],
                "start_at": min((by_id[item]["at"] for item in members if by_id[item]["at"]),
                                default=""),
                "end_at": max((by_id[item]["at"] for item in members if by_id[item]["at"]),
                              default=""),
                "event_ids": members,
            })
        edges = []
        for row in associations:
            if not isinstance(row, dict):
                raise ModelError("association must be an object")
            source, target = row.get("source_id"), row.get("target_id")
            if source not in ids or target not in ids or source == target:
                raise ModelError("association cites an unknown event")
            source_refs = by_id[source]["source_ids"] + by_id[target]["source_ids"]
            edges.append({
                "id": uuid.uuid4().hex, "source_id": source, "target_id": target,
                "relation": _string(row.get("relation"), "related")[:80],
                "reason": _string(row.get("reason"), "")[:1_000],
                "source_refs": list(dict.fromkeys(source_refs)),
            })
        return groups, edges

    def _select(
        self, query: str, events: list[dict[str, Any]], groups: list[dict[str, Any]],
        limit: int, purpose: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any, float]:
        config = self.store.config()
        if not config["base_url"] or not config["model"]:
            raise ValueError("configure base_url and model for model retrieval")
        event_hits = retrieval.rank(
            query, ((event["id"], f"{event['title']} {event['summary']} {event['text']}")
                    for event in events),
        )[:max(limit * 3, 12)]
        group_hits = retrieval.rank(
            query, ((group["id"], group["title"] + " " + group["summary"]) for group in groups),
        )[:12]
        event_by_id = {event["id"]: event for event in events}
        group_by_id = {group["id"]: group for group in groups}
        if not query.strip():
            event_ids = []
            group_ids = [group["id"] for group in groups[:12]]
        else:
            event_ids = [identity for identity, _score in event_hits]
            group_ids = [identity for identity, _score in group_hits]
            if not event_ids and not group_ids:
                group_ids = [group["id"] for group in groups[:12]]
        neighbor_ids: list[str] = []
        for edge in self.store.edges(self._active_or_raise()):
            if edge["source_id"] in event_ids:
                neighbor_ids.append(edge["target_id"])
            if edge["target_id"] in event_ids:
                neighbor_ids.append(edge["source_id"])
        for identity in neighbor_ids:
            if identity in event_by_id and identity not in event_ids:
                event_ids.append(identity)
        if not event_ids and not group_ids:
            return [], [], None, 0.0
        model = self._model_factory(
            config["base_url"], config["model"], config["api_key"], config["timeout"],
        )
        response = model.complete_json(
            _SELECT_SYSTEM,
            json.dumps({
                "purpose": purpose, "query": query, "limit": limit,
                "events": [{"id": identity, "title": event_by_id[identity]["title"],
                            "summary": event_by_id[identity]["summary"]} for identity in event_ids],
                "groups": [{"id": identity, "title": group_by_id[identity]["title"],
                            "summary": group_by_id[identity]["summary"]} for identity in group_ids],
            }, ensure_ascii=False),
        )
        value = response.value
        if not isinstance(value, dict):
            raise ModelError("selection must be a JSON object")
        chosen_events = value.get("event_ids", [])
        chosen_groups = value.get("group_ids", [])
        if not isinstance(chosen_events, list) or not isinstance(chosen_groups, list):
            raise ModelError("selection ids must be lists")
        selected_events = [event_by_id[item] for item in chosen_events
                           if isinstance(item, str) and item in event_by_id][:limit]
        selected_groups = [group_by_id[item] for item in chosen_groups
                           if isinstance(item, str) and item in group_by_id][:4]
        return selected_events, selected_groups, response.usage, response.elapsed_seconds

    def _active_or_raise(self) -> str:
        active = self.store.get_state("active_version")
        if not active:
            raise ValueError("no active memory version")
        return active

    @staticmethod
    def _public_config(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "base_url": config["base_url"], "model": config["model"],
            "configured": bool(config["base_url"] and config["model"]),
            "timeout": config["timeout"], "build_timeout": config["build_timeout"],
            "build_batch_size": config["build_batch_size"],
        }


def _page(text: str, offset: int, limit: int) -> tuple[str, int]:
    return text[offset:offset + limit], len(text)


def _string(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _event_out(row: dict[str, Any], *, include_text: bool = False) -> dict[str, Any]:
    result = {
        "kind": "event", "id": row["id"], "title": row["title"],
        "summary": row["summary"], "at": row["at"], "source_ids": row["source_ids"],
        "before": row["before"], "after": row["after"], "uncertainty": row["uncertainty"],
    }
    if include_text:
        result["text"] = row["text"]
    return result


def _partition_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Body records create events; tool records can only support them."""
    body_kinds = {"user", "assistant", "experience"}
    body = [item for item in records if item.get("kind") in body_kinds
            and isinstance(item.get('text'), str) and item['text'].strip()]
    body_keys = {item['_record_key'] for item in body}
    evidence = [item for item in records if item['_record_key'] not in body_keys]
    return body, evidence


def _evidence_for(
    body: list[dict[str, Any]], evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Include tool records between this and the following body record."""
    if not body:
        return []
    first = body[0].get("at", "")
    last = body[-1].get("at", "")
    nearby = [item for item in evidence
              if item.get("at") and first <= item.get("at", "") <= last]
    return nearby[:12]


def _compact_source(item: dict[str, Any]) -> dict[str, Any]:
    """Omit bulky attachments; body text remains complete for evidence extraction."""
    result = dict(item)
    for field in ("tool_calls", "images", "attachments", "usage", "_meta"):
        result.pop(field, None)
    return result


def _tool_descriptor(item: dict[str, Any]) -> dict[str, Any]:
    """Send tool identity and short result anchors, never the full bulk output."""
    result = {
        "kind": item.get("kind"), "at": item.get("at", ""),
        "name": item.get("name") or item.get("tool"),
    }
    text = item.get("text")
    if isinstance(text, str):
        result["text_excerpt"] = text[:500]
    arguments = item.get("arguments")
    if arguments is not None:
        result["arguments"] = _compact_value(arguments, 600)
    return result


def _compact_value(value: Any, maximum: int) -> Any:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value if len(text) <= maximum else text[:maximum] + "..."


def _event_detail(event: dict[str, Any]) -> str:
    """Concise verified detail for automatic context; raw text needs expand()."""
    fields = [event.get("summary", "")]
    for label, key in (("before", "before"), ("after", "after"),
                       ("uncertainty", "uncertainty")):
        if event.get(key):
            fields.append(f"{label}: {event[key]}")
    return "\n".join(fields).strip()[:2_000]


def _fit_text(value: str, token_budget: int) -> str:
    if token_budget <= 0:
        return ""
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if retrieval.tokens_for_budget(value[:middle], token_budget) <= token_budget:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def _fit_block(kind: str, identity: str, title: str, text: str, token_budget: int) -> str:
    prefix = f"[{kind} {identity}] {title}\n"
    allowed = token_budget - retrieval.tokens_for_budget(prefix, token_budget)
    if allowed <= 0:
        return ""
    return prefix + _fit_text(text, allowed)


def _group_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "group", "id": row["id"], "title": row["title"],
        "dimension": row.get("dimension", "context"), "summary": row["summary"],
        "start_at": row["start_at"], "end_at": row["end_at"],
        "event_ids": row["event_ids"],
    }


def _merge_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge only duplicates sharing both title and at least one source span."""
    aliases: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    for row in rows:
        key = row["title"].strip().casefold()
        existing = aliases.get(key)
        if existing is not None and set(existing["source_ids"]) & set(row["source_ids"]):
            for field in ("summary", "before", "after", "uncertainty"):
                if row[field] and row[field] not in existing[field]:
                    existing[field] = (existing[field] + "\n" + row[field]).strip()
            existing["source_ids"] = list(dict.fromkeys(existing["source_ids"] + row["source_ids"]))
            if row["text"] and row["text"] not in existing["text"]:
                existing["text"] = existing["text"] + "\n\n" + row["text"]
            for ref in row["source_refs"]:
                if ref not in existing["source_refs"]:
                    existing["source_refs"].append(ref)
            continue
        aliases[key] = row
        merged.append(row)
    for index, row in enumerate(merged):
        row["ordinal"] = index
    return merged


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for edge in edges:
        key = (edge["source_id"], edge["target_id"], edge["relation"])
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _merge_usage(usages: list[Any]) -> Any:
    values = [usage for usage in usages if isinstance(usage, dict)]
    if not values:
        return None
    result: dict[str, Any] = {}
    for usage in values:
        for key, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = result.get(key, 0) + value
    return result


def _safe_error(error: Exception, config: dict[str, Any]) -> str:
    message = f"{error.__class__.__name__}: {error}"
    secret = config.get("api_key")
    if secret:
        message = message.replace(secret, "[redacted]")
    return message[:2_000]


_BUILD_SYSTEM = """You organize immutable session records into event blocks. Return JSON
{"events":[{"title":...,"summary":...,"at":...,"before":...,"after":...,
"uncertainty":...,"source_refs":[...] }]}. Each event MUST cite one or more exact
source_ref values from the supplied records. Summaries may interpret but must not
invent facts. Preserve uncertainty and before/after state. Do not output tool results
as answers or create notes not supported by the records. Cover every supplied body
record in at least one event's source_refs; one event may span several records and
records may support multiple events. Tool descriptors are supporting evidence only,
not full verified tool output. Keep the language of the source conversation.
Return JSON only."""

_ORGANIZE_SYSTEM = """Organize supplied event blocks. Return JSON
{"groups":[{"title":...,"dimension":...,"summary":...,"event_ids":[...]}],
"associations":[{"source_id":...,"target_id":...,"relation":...,"reason":...}]}.
Use only supplied event ids. Groups are overlapping overviews along dimensions such
as topic, people, project, or time; a single event may belong to several groups.
Every group summary must be supported by its cited events. Associations must
preserve direction and must not claim that similarity proves causality. Omit
unsupported records. Return JSON only."""

_SELECT_SYSTEM = """Select memory context for the query. Return JSON
{"event_ids":[...],"group_ids":[...]} using only supplied ids, most relevant first.
Do not write summaries or answers; the caller reconstructs verified text. Respect
the requested limit. Return JSON only."""
