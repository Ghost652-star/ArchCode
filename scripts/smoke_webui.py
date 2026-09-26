"""链路冒烟:FakeAgent → server → SSE 流(FastAPI TestClient)。跑完即弃。"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from archcode.agent import (
    LoopComplete,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
)
from archcode.webui.server import create_web_server


class FakeClient:
    model_name = "fake-model"


class FakeAgent:
    """最小 agent 壳:run() 产真实事件类实例。"""

    def __init__(self):
        self._abort_event = asyncio.Event()
        self._permission_checker = None
        self._skill_loader = None
        self._hook_engine = None
        self._tool_registry = None
        self._client = FakeClient()

    def clear_active_skills(self):
        pass

    async def run(self, user_input, conversation):
        yield StreamText(text="你好,")
        yield ThinkingText(text="思考片段")
        yield ToolUseEvent(
            tool_id="t1", tool_name="Bash", arguments={"command": "echo hi"}
        )
        yield ToolResultEvent(
            tool_id="t1", tool_name="Bash", output="hi", is_error=False, elapsed=0.2
        )
        yield StreamText(text="世界")
        yield LoopComplete(total_turns=1, text="你好,世界")


def main():
    from archcode.webui.server import create_web_server

    with tempfile.TemporaryDirectory() as tmp:
        agent = FakeAgent()
        app = create_web_server(agent, Path(tmp))

        with TestClient(app) as client:
            # 1. state
            state = client.get("/api/state").json()
            assert state["model"] == "fake-model", state
            assert state["session_id"], state
            print("[1] /api/state OK:", state["session_id"][:12])

            # 2. 静态页(dist 已构建)
            index = client.get("/")
            assert index.status_code == 200 and b"root" in index.content
            print("[2] 静态 index.html OK")

            # 3. 会话 API
            sessions = client.get("/api/sessions").json()
            assert isinstance(sessions, list) and len(sessions) >= 1
            new = client.post("/api/sessions").json()
            assert new["session_id"]
            print("[3] sessions OK:", len(sessions), "→ new", new["session_id"][:12])

            # 4. SSE 聊天链路
            with client.stream("POST", "/api/chat", json={"text": "测试"}) as res:
                assert res.status_code == 200
                events = []
                buffer = ""
                for chunk in res.iter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        for line in frame.split("\n"):
                            if line.startswith("data: "):
                                import json

                                events.append(json.loads(line[6:]))
            types = [e["type"] for e in events]
            assert "text" in types and "thinking" in types, types
            assert "tool_use" in types and "tool_result" in types, types
            assert types[-1] == "done", types
            loop_ev = [e for e in events if e["type"] == "loop_complete"][0]
            assert loop_ev["text"] == "你好,世界"
            print("[4] SSE 链路 OK:", types)

            # 5. 忙时 409(流已结束,锁应释放 → 直接再跑一次确认可重入)
            with client.stream("POST", "/api/chat", json={"text": "再跑"}) as res:
                assert res.status_code == 200
            print("[5] 重入 OK(锁已释放)")

    print("=== 链路冒烟全部通过 ===")


if __name__ == "__main__":
    main()
