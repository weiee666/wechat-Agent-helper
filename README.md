# weixin-agent

语音转结构化任务工具。微信(iLink) ←→ Agent 后端（STT → 记忆 → DeepSeek 总结）。
单一项目、单一 venv，结构参照 autoweixin 的 `app/` 布局。

## 结构

```
weixin-agent/
├── app/
│   ├── main.py                入口：iLink 收发循环
│   ├── config.py              统一配置（路径 / env / 协议常量）
│   ├── models/                enums + schemas（Message / MemoryContext…）
│   ├── core/
│   │   ├── memory/            记忆系统（SQLite）
│   │   │   ├── store.py           SQLite 连接/建表
│   │   │   ├── short_term.py      短期：滑动窗口 + 超长 LLM 摘要压缩（移植自 autoweixin）
│   │   │   ├── long_term.py       长期：结构化存储 + 关键词召回
│   │   │   └── manager.py         协调短期+长期
│   │   └── tools/             工具系统（BaseTool + 注册中心，移植自 autoweixin）
│   ├── agent/
│   │   ├── llm.py             DeepSeek（LangChain）
│   │   ├── summarizer.py      ≤100字摘要（提示词外置）
│   │   ├── memory_bridge.py   把记忆接进 agent（同步）
│   │   └── runner.py          VoiceTaskAgent：STT→记忆→摘要
│   ├── stt/                   语音转写（当前用 iLink 自带转写）
│   └── channel/ilink.py       iLink 客户端（登录/长轮询/收发）
├── prompts/summarize.txt      可编辑提示词
├── data/                      SQLite 记忆库 + iLink 登录态（运行时生成）
├── requirements.txt
└── .env                       DEEPSEEK_API_KEY 等
```

## 架构要点

- **STT 不是工具**，是渠道层的输入预处理（语音→文字发生在 agent 之前）。
- **结构化/总结是 agent 主业**，放提示词里，不做成工具。
- **真正的工具**（将来）：`send_email`、`lookup_recipient` 等有副作用的动作，经 `core/tools` 注册。
- **记忆**：短期（滑窗+压缩）在 SQLite，长期（任务/事实）结构化存 SQLite；去掉了 Milvus/Redis/embedding 的重型后端，压缩算法原样保留。

## 跑起来

```bash
cd weixin-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填 DEEPSEEK_API_KEY

# 命令行测一段口述（不连微信）
python -m app.agent.runner    # 或见下方单测

# 连微信跑全流程
python -m app.main            # 首次扫码；之后复用登录态
python -m app.main --login    # 强制重新扫码
```
