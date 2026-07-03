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
# 授权模式开关：开启后，其他 Agent 向本用户微信 push 消息需要本人先在微信里授权（"允许"/"不允许"）
# 关闭时（默认），Agent 之间对话完全在旁观面板进行，不打扰本人微信
_AUTH_ON = {"打开授权", "开启授权", "开授权", "打开授权模式"}
_AUTH_OFF = {"关闭授权", "关授权", "取消授权", "关闭授权模式"}
# 旁观面板触发语：签发一次性 token，返回带 token 的 web URL
_DASHBOARD_TRIGGERS = {"看板", "打开看板", "旁观面板", "面板", "dashboard", "/看板", "/dashboard"}

_START_MSG = "🎙️ 开始记录，你尽管说，我先攒着不打断；说完发一句『就这些』我来整理。"


def _push_profile_hint_if_needed(user_id: str) -> bool:
    """检测姓名/Agent 名是否设，缺就用 dispatch 单独 push 一条提示（不污染主 reply）。
    返回 True 表示推送了；False 表示不需要推。"""
    me = BotUsersStore().get(user_id)
    hints = []
    if not (me and me.display_name):
        hints.append("• 说「我叫XXX」记下你的名字")
    if not (me and me.agent_name):
        hints.append("• 说「给你起名叫XXX」给我起个名")
    if not hints:
        return False
    text = ("💡 顺便：设完这两项，别人的 Agent 就能用你的名字找到你，"
            "我们才能相互沟通：\n" + "\n".join(hints))
    try:
        from app.channel import dispatch
        return dispatch.push_to_user(user_id, text)
    except Exception:  # noqa: BLE001
        return False


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

        # ── 授权响应 hook：如果这个用户有 pending 的授权请求，优先处理
        # （B 用户在自己 bot 里回复"允许/不允许"时短路，跳过 LLM）
        from app.kernel import bus as _bus
        _verdict = _bus.resolve_authorization(user_id, text)
        if _verdict == "allowed":
            return Result(transcript=text, used_tool=True,
                          reply="✅ 已授权。你的回复已发送给对方。")
        if _verdict == "denied":
            return Result(transcript=text, used_tool=True,
                          reply="❌ 已拒绝，本次不发送。对方会收到系统「未应答」提示。")
        if _verdict == "unclear":
            return Result(transcript=text, used_tool=True,
                          reply="没听清是允许还是不允许（我在等你对刚才那条授权请求做选择）。请回复「允许」或「不允许」。")
        # _verdict is None → 无 pending，正常走后续 LLM 流程

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

        # ── 授权模式开关（确定性）──
        if cleaned in _AUTH_ON:
            UserSettingsStore().set_require_authorization(user_id, True)
            return Result(transcript=text, used_tool=True,
                          reply="🔒 已打开授权模式：以后其他用户的 Agent 想给你微信发消息前，"
                                "我会在微信里先问你「允许/不允许」。")
        if cleaned in _AUTH_OFF:
            UserSettingsStore().set_require_authorization(user_id, False)
            return Result(transcript=text, used_tool=True,
                          reply="🔓 已关闭授权模式：Agent 之间的对话默认在旁观面板里进行，"
                                "不再打扰你的微信。（要在面板里看：说「看板」拿链接）")

        # ── 旁观面板：签发一次性 token 并返回带 token 的 URL ──
        if cleaned in _DASHBOARD_TRIGGERS:
            if not config.DASHBOARD_URL_BASE:
                return Result(transcript=text, used_tool=True,
                              reply="ℹ️ 旁观面板尚未配置（管理员需设置 DASHBOARD_URL_BASE + PUSHER_* 环境变量）。")
            token = dashboard_tokens.issue(user_id)
            sep = "&" if "?" in config.DASHBOARD_URL_BASE else "?"
            url = f"{config.DASHBOARD_URL_BASE}{sep}token={token}&user_id={user_id}"
            # 姓名/Agent 名字缺失时，走独立 push（不塞进本条 reply 里）
            _push_profile_hint_if_needed(user_id)
            return Result(transcript=text, used_tool=True, reply=url)

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

        # 首次接触且姓名字段没设 → 独立 push 一条提示（不污染主 reply）
        if is_first_contact:
            _push_profile_hint_if_needed(user_id)

        # 更新最后活跃时间
        try:
            BotUsersStore().touch_last_seen(user_id)
        except Exception:  # noqa: BLE001
            pass

        memory_bridge.save_turn(user_id, MessageRole.USER, text)
        # 直传模式：如果 call_agent 已经把对方原话 push 到用户微信，本 Agent 不再多嘴
        if conversation.was_reply_pushed():
            trace = "[已代联络对方 Agent；对方助手的回复已直接以对方名义 push 到用户微信，未在此处二次转述]"
            memory_bridge.save_turn(user_id, MessageRole.ASSISTANT, trace)
            return Result(transcript=text, reply="", used_tool=used_tool, send=False)
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
                              from_display_name: str, message: str,
                              request_authorization: bool = False) -> str:
        """另一个用户的 Agent 发来的消息，目标 Agent 处理后返回 reply。

        由 call_agent 工具调用；conversation 里的 round 计数由 call_agent 入口维护。
        target 的中间过程推到 target 自己的 realtime channel。

        request_authorization=False（默认）：直连模式，纯计算生成 reply 并 return，不打扰 target 微信
        request_authorization=True：授权模式，reply 生成后调 bus.request_authorization 请求 target 授权
        （target 微信收到"允许/不允许"提示；批准后 bus 用令牌 push reply 给 sender）"""
        from app import realtime as _realtime
        _EVENT = {"thinking": "verbose_thinking", "tool_call": "verbose_tool_call",
                  "tool_out": "verbose_tool_out"}

        def _say(kind: str, text: str):
            s = str(text).strip()
            if not s:
                return
            _realtime.publish(target_user_id, _EVENT.get(kind, "verbose"), {"text": s})

        # 拿 target 身份
        target = BotUsersStore().get(target_user_id)
        target_display = (target.display_name if target and target.display_name else "你的用户")
        target_agent_name = (target.agent_name if target and target.agent_name else "小助手")

        # 前缀标注来源（写进历史里，用户后面可回顾）
        prefixed = f"[来自 {from_display_name} 的助手] {message}"
        ctx = memory_bridge.get_context(target_user_id, prefixed)

        # System prompt 补丁：明确身份 + 鼓励多轮对话
        system_extra = (
            f"\n\n## 现在是跨 Agent 对话场景\n"
            f"你是 **{target_display}** 的助手（{target_display} 叫你「{target_agent_name}」）。"
            f"此刻**不是 {target_display} 在跟你说话**，是另一个用户「{from_display_name}」的助手发来的消息。"
            f"你要**代表 {target_display}** 直接跟对方助手对话。\n\n"
            f"行为准则：\n"
            f"1. **绝对不要**说「{target_display} 你要不要…」「我问下 {target_display}」这种把用户当第三方的话——"
            f"{target_display} 此刻不在对话里，说了对方也看不到，只会让对话卡住。\n"
            f"2. 社交寒暄（打招呼、道谢）：直接得体回一句就够。\n"
            f"3. **信息类问题**：知道就答；不知道但对方问题里有线索，可以调 call_agent 反问对方澄清"
            f"（比如对方问「什么时候有空」，你可以反问「你想约什么时间段？」）。\n"
            f"4. **需要用户拍板的事**（重要决定、约见面时间、承诺任务）：答一句「这需要 {target_display} 本人确认，"
            f"我稍后帮 ta 转达」就好，不要擅自代 ta 承诺。\n"
            f"5. **需要澄清才能完成的事**：主动调 call_agent 追问对方（比如对方说「帮我问下时间」，"
            f"你要反问「什么时间段？什么时区？」才能给出有意义的回复）。轻信直接答会让协作低质量。\n"
            f"6. 一轮完成不了的**不要硬要一轮完成**——追问几轮换来准确答复远比一句敷衍强。\n"
            f"7. 5 轮硬上限，超了系统会阻止。当前第 {conversation.get_round()} 轮。"
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
        # 记录到 target 短期记忆
        memory_bridge.save_turn(target_user_id, MessageRole.USER, prefixed)
        memory_bridge.save_turn(target_user_id, MessageRole.ASSISTANT, reply)

        # 授权模式：走 bus.request_authorization，target 微信收到"允许/不允许"
        # 默认（直连）模式：什么都不做，reply 由 call_agent 拿走后同步返回给 sender LLM
        if request_authorization and reply and reply.strip():
            from app.kernel import bus
            asked = bus.request_authorization(
                agent_user_id=target_user_id,
                agent_display=target_display,
                target_user_id=from_user_id,
                target_display=from_display_name,
                proposed_text=reply.strip(),
                conv_round=conversation.get_round(),
            )
            if not asked:
                # target 也没 ctx，授权流走不下去 → 只在看板留下痕迹，主流程不阻断
                _realtime.publish(target_user_id, "system_event",
                                  {"text": "有 Agent 消息到达且已回复，但无法向你请求授权（你还没跟 bot 说过话）。请先给 bot 发一句话激活 ctx。"})

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
