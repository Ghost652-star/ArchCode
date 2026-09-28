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

            # 6. 工作区文件端点(设计 §12:圈定 + 惰性列目录 + 二进制探测)
            ws = Path(tmp)
            (ws / "sub").mkdir()
            (ws / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
            (ws / "sub" / "b.md").write_text("# 标题\n\n正文", encoding="utf-8")
            (ws / "bin.dat").write_bytes(b"\x00\x01\x02binary")
            files = client.get("/api/files").json()
            names = [e["name"] for e in files["entries"]]
            assert "sub" in names and "a.txt" in names, names
            assert files["entries"][0]["type"] == "directory", files
            sub = client.get("/api/files", params={"path": "sub"}).json()
            assert sub["entries"][0]["name"] == "b.md"
            text = client.get("/api/file", params={"path": "sub/b.md"}).json()
            assert text["text"].startswith("# 标题") and not text["binary"], text
            binary = client.get("/api/file", params={"path": "bin.dat"}).json()
            assert binary["binary"] is True, binary
            assert client.get("/api/files", params={"path": "../"}).status_code == 403
            assert client.get("/api/files", params={"path": "nope"}).status_code == 404
            assert client.get("/api/file", params={"path": "sub"}).status_code == 400
            print("[6] 文件端点 OK: 列目录/读文本/二进制/圈定")

            # 7. 会话管理(设计 §10.9:标题/改名/删除)+ 历史去污染
            sessions = client.get("/api/sessions").json()
            row = sessions[0]
            assert "title" in row and "last_active_ms" in row and "message_count" in row, row
            sid = row["id"]
            assert client.post(f"/api/sessions/{sid}/rename",
                               json={"title": "冒烟改名"}).json()["ok"] is True
            assert client.get("/api/sessions").json()[0]["title"] == "冒烟改名"
            # 历史去污染:注入三类内部消息,均不应出现在 /api/history
            import archcode.webui.server as _srv
            from archcode.conversation.models import Message as _Msg

            conv = _srv.STATE.conversation
            conv.history.append(
                _Msg(role="user", content="<system-reminder>\n注入\n</system-reminder>")
            )
            conv.history.append(
                _Msg(role="user", content="<会话恢复材料>\n降级线索\n</会话恢复材料>")
            )
            conv.history.append(_Msg(role="user", content="[恢复提示] 距离上次超过 24 小时"))
            conv.history.append(_Msg(role="user", content="真实用户消息"))
            hist = client.get("/api/history").json()
            contents = [m["content"] for m in hist if m["role"] == "user"]
            assert any(c == "真实用户消息" for c in contents), contents
            assert not any(
                c.startswith(("<system-reminder>", "<会话恢复材料>", "[恢复提示]"))
                for c in contents
            ), contents
            # 删除非当前会话:新建一个再删它
            new_sid = client.post("/api/sessions").json()["session_id"]
            assert client.delete(f"/api/sessions/{new_sid}").json()["ok"] is True
            # 删除当前会话:服务端应自动开新会话
            cur = client.get("/api/sessions").json()
            cur_sid = next(s["id"] for s in cur if s["current"])
            assert client.delete(f"/api/sessions/{cur_sid}").json()["ok"] is True
            after = client.get("/api/sessions").json()
            assert any(s["current"] for s in after) and all(s["id"] != cur_sid for s in after)
            print("[7] 会话管理 OK: 字段/改名/删除/自动新会话/历史去污染")

            # 8. 上下文占用真值 + 文件名搜索(§13-A4/B1)
            ctx = client.get("/api/context").json()
            assert {"total_tokens", "percent", "window"} <= set(ctx), ctx
            assert 0.0 <= ctx["percent"] <= 1.0 and ctx["window"] == 131072, ctx
            (ws / "node_modules").mkdir(exist_ok=True)
            (ws / "node_modules" / "skipme.qmd").write_text("x")
            (ws / "sub" / "deep").mkdir(exist_ok=True)
            (ws / "sub" / "deep" / "notes.bmd").write_text("x")
            hits = client.get("/api/files/search", params={"q": "bmd"}).json()["results"]
            assert hits == ["sub/deep/notes.bmd"], hits  # node_modules 被剪枝,POSIX 分隔
            empty = client.get("/api/files/search", params={"q": ""}).json()
            assert empty["results"] == []
            print("[8] 上下文占用 + 文件搜索 OK")

    print("=== 链路冒烟全部通过 ===")


if __name__ == "__main__":
    main()
