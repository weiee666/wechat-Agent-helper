# -*- coding: utf-8 -*-
"""Agent 运行器：带工具的对话 agent（DeepSeek function-calling）。

每轮：system_prompt + 短期记忆历史 + 当前消息 → DeepSeek（绑定工具）。
- 普通消息 → 正常对话回复（记忆进上下文，支持多轮）
- 用户说"记一下/帮我记录" → 模型自行调用 structure_task 工具 → 结构化并写长期记忆

STT 在更外层（main → stt）先把语音转成文字，到这里已是文本。
"""
from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import re

from app import config, dashboard_tokens, stt
from app.agent import conversation, memory_bridge, summarizer, tools as agent_tools
from app.agent.llm import get_llm
from app.core.memory.bot_users import BotUsersStore
from app.core.memory.recording import RecordingStore
from app.core.memory.settings import UserSettingsStore
from app.models.enums import MessageRole
from app.models.schemas import Message

_MAX_TOOL_ITERS = 5  # 工具调用循环最多轮数（防止失控）


def _email_setup_guide() -> str:
    """发件邮箱设置引导（固定模板，可编辑 prompts/email_setup_guide.txt）。"""
    return (config.PROMPT_DIR / "email_setup_guide.txt").read_text(encoding="utf-8").strip()

# 录制开始/结束/取消触发语（消息去标点后匹配，避免中途误触发）。
# 开始触发用正则，兼容"我想记录一下/开始记录/我要记一段"等口述变体。
_START_RE = re.compile(r"^(我)?(想|要|来|想要)?(开始)?(记录|记|口述)(一下|一段|个东西)?(吧|哈|呗|啊)?$")
_END_PHRASES = {"就这些", "就这些了", "就这些吧", "就这么多", "就这样", "就这样吧",
                "结束", "结束了", "完成", "完成了", "说完了", "说完", "没了", "好了"}
_CANCEL_PHRASES = {"取消记录", "退出记录", "不记了", "取消", "算了不记了"}
# 详细模式（把思考/工具调用推到微信）开关触发语
_VERBOSE_ON = {"打开思考过程", "显示思考过程", "详细模式", "打开详细模式", "显示过程",
               "打开过程", "开启详细模式", "显示工具调用", "打开调试"}
_VERBOSE_OFF = {"关闭思考过程", "关闭详细模式", "简洁模式", "关闭过程", "隐藏思考过程", "关闭调试"}
# 旁观面板触发语：签发一次性 token，返回带 token 的 web URL
_DASHBOARD_TRIGGERS = {"看板", "打开看板", "旁观面板", "面板", "dashboard", "/看板", "/dashboard"}

_START_MSG = "🎙️ 开始记录，你尽管说，我先攒着不打断；说完发一句『就这些』我来整理。"


def _clean(text: str) -> str:
    """去掉标点/空白，便于匹配触发语（语音转写常带句号）。"""
    return re.sub(r"[\s，。、！？!?.,；;：:~～]+", "", text or "")

@dataclass
class Result:
    transcript: str       # 用户原文（语音转写后）
    reply: str            # 要发回微信的回复
    used_tool: bool       # 这轮是否触发了工具
    send: bool = True     # 是否真的发回微信（录制中途累积时为 False，静默不打扰）


def _load_system_prompt() -> str:
    return (config.PROMPT_DIR / "agent_system.txt").read_text(encoding="utf-8")


def _history_to_messages(history: list[Message]) -> list:
    """短期记忆 → LangChain 消息列表。"""
    out = []
    for m in history:
        if m.role == MessageRole.USER:
            out.append(HumanMessage(content=m.content))
        elif m.role == MessageRole.ASSISTANT:
            out.append(AIMessage(content=m.content))
        else:  # SYSTEM（含 [历史摘要]）
            out.append(SystemMessage(content=m.content))
    return out


class VoiceTaskAgent:
    def handle_text(self, text: str, user_id: str = "default", emit=None,
                    realtime_emit=None) -> Result:
        # 用户主键 = user_id（= from_user_id 微信号），记忆/任务/联系人全按它隔离
        # emit(text)：可选回调，用于把中间过程实时推给微信（详细模式）
        # realtime_emit(kind, text)：可选回调，不管 verbose 都调用；kind ∈ {thinking, tool_call, tool_out}
        # 用于把 Agent 中间过程 fanout 到实时面板（Pusher channel）

        # 这是"用户起源消息"—— 重置跨 Agent 对话计数器
        conversation.reset()
        conversation.set_origin(user_id)

        # ── 录制会话拦截（确定性，不依赖 LLM）──
        rec = RecordingStore()
        cleaned = _clean(text)
        if rec.is_active(user_id):
            if cleaned in _CANCEL_PHRASES:
                rec.cancel(user_id)
                return Result(transcript=text, reply="🗑️ 已取消本次记录，没有保存。", used_tool=True)
            if cleaned in _END_PHRASES:
                buffer = rec.finish(user_id)
                if not buffer.strip():
                    return Result(transcript=text, reply="（这次没记录到内容，已结束。）", used_tool=True)
                summary = summarizer.summarize(buffer)
                memory_bridge.store_longterm(user_id, summary, {"type": "record"})
                return Result(transcript=text, reply=f"📝 记录总结：\n{summary}", used_tool=True)
            # 中途：只累积，静默不回复（不打扰用户说话）
            rec.append(user_id, text)
            return Result(transcript=text, reply="", used_tool=True, send=False)

        # 未在录制时，确定性识别"开始记录"指令 → 进入录制（不交给 LLM，避免它只聊不开）
        if _START_RE.match(cleaned):
            rec.start(user_id)
            return Result(transcript=text, reply=_START_MSG, used_tool=True)

        # ── 详细模式开关（确定性）──
        if cleaned in _VERBOSE_ON:
            UserSettingsStore().set_verbose(user_id, True)
            return Result(transcript=text, used_tool=True,
                          reply="🔎 已打开详细模式：之后我会把「💭思考 / 🔧工具调用 / ↩️结果」都发给你。说「关闭详细模式」可关。")
        if cleaned in _VERBOSE_OFF:
            UserSettingsStore().set_verbose(user_id, False)
            return Result(transcript=text, reply="已关闭详细模式，恢复简洁回复。", used_tool=True)

        # ── 旁观面板：签发一次性 token 并返回带 token 的 URL ──
        if cleaned in _DASHBOARD_TRIGGERS:
            if not config.DASHBOARD_URL_BASE:
                return Result(transcript=text, used_tool=True,
                              reply="ℹ️ 旁观面板尚未配置（管理员需设置 DASHBOARD_URL_BASE + PUSHER_* 环境变量）。")
            token = dashboard_tokens.issue(user_id)
            sep = "&" if "?" in config.DASHBOARD_URL_BASE else "?"
            url = f"{config.DASHBOARD_URL_BASE}{sep}token={token}&user_id={user_id}"
            reply = f"🔭 旁观面板（1 小时内有效）：\n{url}"
            # 看板是跨 Agent 对话的入口。用户身份没完善的话，跨 Agent 通信没法进行——顺手提示补上
            me = BotUsersStore().get(user_id)
            hints = []
            if not (me and me.display_name):
                hints.append("• 说「我叫XXX」记下你的名字")
            if not (me and me.agent_name):
                hints.append("• 说「给你起名叫XXX」给我起个名")
            if hints:
                reply += ("\n\n💡 顺便：设完这两项，别人的 Agent 就能用你的名字找到你，"
                          "我们才能相互沟通：\n" + "\n".join(hints))
            return Result(transcript=text, used_tool=True, reply=reply)

        verbose = UserSettingsStore().get_verbose(user_id)

        # emoji 前缀（微信显示用）
        _EMOJI = {"thinking": "💭", "tool_call": "🔧", "tool_out": "↩️"}

        def _say(kind: str, text: str):
            """把中间过程同时推给微信（verbose 时）和实时面板（总是）。"""
            s = str(text).strip()
            if not s:
                return
            emoji = _EMOJI.get(kind, "")
            if verbose and emit:
                try:
                    emit(f"{emoji} {s}" if emoji else s)
                except Exception:  # noqa: BLE001
                    pass
            if realtime_emit:
                try:
                    realtime_emit(kind, s)
                except Exception:  # noqa: BLE001
                    pass

        ctx = memory_bridge.get_context(user_id, text)
        # 是否首次接触（之前没有任何对话历史）——用于主动提醒
        is_first_contact = not ctx.short_term_messages
        messages = [SystemMessage(content=_load_system_prompt())]
        # 注入长期记忆：用户之前记下/口述的内容，让 agent 能跨时间回忆，不会"失忆"
        if ctx.long_term_items:
            mem = "\n".join(f"- {it.content}" for it in ctx.long_term_items)
            messages.append(SystemMessage(
                content="## 用户之前记下的内容（长期记忆，已持久保存，回答时可直接引用）\n" + mem))
        messages += _history_to_messages(ctx.short_term_messages)
        messages.append(HumanMessage(content=text))

        # ── 工具调用循环（ReAct：Reason → Act → Observe → 再 Reason …）──
        # 每轮：调 LLM → 若要调工具则执行、把结果作为 ToolMessage 回填 → 再调 LLM，
        # 直到模型不再调工具、给出最终答案。这样能看结果再决定下一步、串多个工具。
        llm_with_tools = get_llm().bind_tools(agent_tools.TOOLS)
        used_tool = False
        reply = ""
        for _step in range(_MAX_TOOL_ITERS):
            ai = llm_with_tools.invoke(messages)
            calls = getattr(ai, "tool_calls", None)
            if not calls:
                reply = ai.content or ""
                break
            used_tool = True
            # 详细模式 + 实时面板：把模型调工具前的思考推出去
            if ai.content:
                _say("thinking", ai.content)
            messages.append(ai)  # 记下"模型决定调工具"这一轮
            for tc in calls:
                args = tc.get("args") or {}
                arg_str = "、".join(f"{k}={v}" for k, v in args.items() if k not in ("user_id",))
                _say("tool_call", f"调用 {tc['name']}({arg_str})")
                tool = agent_tools.BY_NAME.get(tc["name"])
                if tool is None:
                    out = f"[未知工具 {tc['name']}]"
                else:
                    try:
                        # 注入 user_id（InjectedToolArg，不暴露给 LLM）
                        out = tool.invoke({**args, "user_id": user_id})
                    except Exception as e:  # noqa: BLE001
                        out = f"[工具 {tc['name']} 执行失败] {e}"
                out = str(out)
                _say("tool_out", out[:400])
                if tc["name"] == "structure_task":
                    memory_bridge.store_longterm(user_id, out, {"type": "task"})
                messages.append(ToolMessage(content=out, tool_call_id=tc["id"]))  # 结果回填
        else:
            reply = reply or "（处理步骤较多，已先停下。你可以补充或换个说法。）"

        # 主动提醒：新用户首次说话、且还没设发件邮箱 → 在回复后补上设置引导（不只一问一答）
        if is_first_contact and UserSettingsStore().get_smtp(user_id) is None:
            guide = "💡 顺便：你还没设发任务邮件用的发件邮箱。\n\n" + _email_setup_guide()
            reply = (reply + "\n\n" + guide) if reply.strip() else guide

        # 主动提醒：还没设"自己的名字 / Agent 名字"→ 别人的 Agent 找不到你 → 无法跨 Agent 对话
        me = BotUsersStore().get(user_id)
        need_name = not (me and me.display_name)
        need_agent_name = not (me and me.agent_name)
        if is_first_contact and (need_name or need_agent_name):
            hints = []
            if need_name:
                hints.append("• 告诉我你叫什么：说「我叫XXX」")
            if need_agent_name:
                hints.append("• 给我这个助手起个名字：说「给你起名叫XXX」")
            profile_guide = ("💡 顺便：设完这些之后，别人的 Agent 就能用你的名字找到你，"
                             "我们就能相互沟通了：\n" + "\n".join(hints))
            reply = (reply + "\n\n" + profile_guide) if reply.strip() else profile_guide

        # 更新最后活跃时间
        try:
            BotUsersStore().touch_last_seen(user_id)
        except Exception:  # noqa: BLE001
            pass

        memory_bridge.save_turn(user_id, MessageRole.USER, text)
        memory_bridge.save_turn(user_id, MessageRole.ASSISTANT, reply)
        return Result(transcript=text, reply=reply, used_tool=used_tool)

    def handle_ilink_message(self, msg: dict, from_user_id: str = "default", emit=None,
                              realtime_emit=None) -> Result | None:
        transcript = stt.transcribe_ilink_message(msg)
        if not transcript:
            return None
        # 用户主键 = from_user_id（微信号），稳定、与人一一对应；不再用 account_id(bot)
        return self.handle_text(transcript, user_id=from_user_id, emit=emit,
                                realtime_emit=realtime_emit)

    def handle_agent_message(self, target_user_id: str, from_user_id: str,
                              from_display_name: str, message: str) -> str:
        """另一个用户的 Agent 发来的消息，目标 Agent 处理后返回 reply（不发微信）。

        由 call_agent 工具调用；conversation 里的 round 计数由 call_agent 入口维护，
        本方法不 reset。target 的思考 / 工具调用推到 target 自己的 realtime channel。"""
        # target Agent 的中间过程推到 target 自己的看板（call_agent 已经把消息本身推到双方）
        from app import realtime as _realtime
        _EVENT = {"thinking": "verbose_thinking", "tool_call": "verbose_tool_call",
                  "tool_out": "verbose_tool_out"}

        def _say(kind: str, text: str):
            s = str(text).strip()
            if not s:
                return
            _realtime.publish(target_user_id, _EVENT.get(kind, "verbose"), {"text": s})

        # target 的记忆上下文
        prefixed = f"[来自 {from_display_name} 的 Agent] {message}"
        ctx = memory_bridge.get_context(target_user_id, prefixed)
        # 让 target Agent 知道自己是谁，谁在跟自己说话
        target = BotUsersStore().get(target_user_id)
        my_name = (target.agent_name if target and target.agent_name else "你")
        system_extra = (
            f"\n\n## 特殊场景：本条消息不是你的用户发的，是另一个用户「{from_display_name}」的 Agent。"
            f"\n你可以决定要不要回复对方 Agent。回复要简洁——如果对方问的是明确问题就直接答；"
            f"如果对方要你做事、你的用户不在场无法确认，就先答复'我先确认下再回复你'并结束这轮。"
            f"\n目前对话轮次已到第 {conversation.get_round()} 轮，上限 {conversation.MAX_ROUNDS} 轮。"
        )
        messages = [SystemMessage(content=_load_system_prompt() + system_extra)]
        if ctx.long_term_items:
            mem = "\n".join(f"- {it.content}" for it in ctx.long_term_items)
            messages.append(SystemMessage(
                content="## 用户之前记下的内容（长期记忆）\n" + mem))
        messages += _history_to_messages(ctx.short_term_messages)
        messages.append(HumanMessage(content=prefixed))

        llm_with_tools = get_llm().bind_tools(agent_tools.TOOLS)
        reply = ""
        for _step in range(_MAX_TOOL_ITERS):
            ai = llm_with_tools.invoke(messages)
            calls = getattr(ai, "tool_calls", None)
            if not calls:
                reply = ai.content or ""
                break
            if ai.content:
                _say("thinking", ai.content)
            messages.append(ai)
            for tc in calls:
                args = tc.get("args") or {}
                arg_str = "、".join(f"{k}={v}" for k, v in args.items() if k not in ("user_id",))
                _say("tool_call", f"调用 {tc['name']}({arg_str})")
                tool = agent_tools.BY_NAME.get(tc["name"])
                if tool is None:
                    out = f"[未知工具 {tc['name']}]"
                else:
                    try:
                        out = tool.invoke({**args, "user_id": target_user_id})
                    except Exception as e:  # noqa: BLE001
                        out = f"[工具 {tc['name']} 执行失败] {e}"
                out = str(out)
                _say("tool_out", out[:400])
                if tc["name"] == "structure_task":
                    memory_bridge.store_longterm(target_user_id, out, {"type": "task"})
                messages.append(ToolMessage(content=out, tool_call_id=tc["id"]))
        # 记录到 target 短期记忆（用户之后可问"刚才 xxx 说了什么"）
        memory_bridge.save_turn(target_user_id, MessageRole.USER, prefixed)
        memory_bridge.save_turn(target_user_id, MessageRole.ASSISTANT, reply)
        return reply


if __name__ == "__main__":
    # 命令行多轮测试：连续输入几句，最后说"记一下"看看
    agent = VoiceTaskAgent()
    print("多轮对话测试（Ctrl+C 退出）。试试先聊两句，再说「帮我记录」：")
    while True:
        try:
            text = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        r = agent.handle_text(text, user_id="cli")
        print(f"助理{'[调用了工具]' if r.used_tool else ''}: {r.reply}")
