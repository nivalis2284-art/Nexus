"use strict";

let runtimeStream = null;

function connectRuntimeStream() {
  if (runtimeStream) return;
  runtimeStream = new EventSource("/api/stream");
  runtimeStream.onmessage = event => {
    if (typeof lingView !== "undefined") lingView.connectionError = "";
    receiveRuntimeEvent(JSON.parse(event.data));
  };
  runtimeStream.onopen = async () => {
    await pollLing();
    if (typeof taskView !== "undefined" && taskView.current) {
      await openTask(taskView.current.id, {refresh: true});
    }
  };
  runtimeStream.onerror = () => {
    if (typeof lingView !== "undefined" && lingView.active) {
      lingView.connectionError = "实时连接中断，正在重新连接";
      renderLingStatus();
    }
  };
}
