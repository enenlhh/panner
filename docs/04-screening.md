# 04 基于原子信号问题的筛选

> 本环节只完成一件事：**基于原子信号问题的筛选**——对确认池中的每条待筛 citation，回答冻结协议的原子信号问题，执行冻结的组合规则，产出 `excluded` / `retained` 两态结果及可回查的依据。
> 输入：冻结的 `protocol/vN/protocol.json`、确认的 `citation-pool.jsonl`（只读 `duplicate_of` 为空的行）、版本化的运行配置。产物：`screening/vN/` 下的判定记录、筛选结果与 manifest。
> MCP 接口与宿主指令以 `server.py` 为运行时正本。

## 职责与判断逻辑

04 只读取冻结协议、确认池与运行配置，不修改前两者。对每条待筛 citation 的判断顺序是：`field_rule` 信号先求值（确定性、零调用成本），`semantic` 信号交判断后端按该题四值条件作答，随后用与 03 试读**同一份求值器**（[evaluate.py](../src/panner_mcp/evaluate.py)）执行组合规则。

**回答与对象。** 对每道原子问题直接依据 citation 选择四值答案。问题涉及具体组或方案而文本不足以确定其对象时，该答案为 `unclear`；不凭空补齐对象。只有在规则要据“所有可能的比较均不合格”排除时，才需要完整、可靠的对象范围；范围不能确定，规则保持未决并保留。回答中的 `on` 必须指向 `record` 或该 citation 中已明确的对象。

**semantic 回答要求。** 每条回答记录 `{signal, on, answer}`；值域外或依据不足的回答记为 `unclear`，绝不猜测。比较不是信号对象（见 [03](03-compile.md)）；比较资格完全由组合规则表达。

**判题模型的组级题。** 一次判题请求同时提交记录级题、明确组数题，以及按题名摘要首次提及顺序指向第 1 至第 6 组的组级题。只有判题模型明确给出完整组数时，才把相应答案分别记在 `group_1`、`group_2` 等对象上；组数不明或多于六组时，组级答案保持未决，不据组级规则排除。结构题和组级题由同一个低成本判题请求回答，不构成独立的高成本筛选阶段。

**field_rule 匹配语义。** 按冻结模式匹配结构化字段：对 `publication_types` 这类列表字段，模式值为**大小写不敏感的整值相等**，列表中任一元素命中即按该模式作答；不做子串或包含匹配。空值与未匹配值进入 `unclear`，未匹配值不得自动排除。

**核心信号聚合。** core 题先汇总同题各对象的已知答案与未决对象，不因某一个组为 `unclear` 就把整题视为 `unclear`。全部核心题仍为 `unclear` 且没有其他排除依据时，该 citation 保留，并在导出中标记 `topic_unclear` 供复核排序。

**相关度路由。** 协议保留宽泛的 `relevance.facets`，供后续单独验证。当前运行时不调用相关度后端，只按核心题是否全部未决排列复核顺序，不产生相关度排除候选。摘要很短或缺失本身不是排除理由。

### 判断后端与生效条件

正式筛选使用判题模型回答冻结的原子问题，并由确定性求值器计算记录结论。**四值答案的语义本身已含证据性**——只有明确有证据才作答 hit/partial/miss，拿不准即 unclear；生效规则是**排除候选即排除**，不设二次验证、不引入置信度或行为确认：

| 通道 | 适用后端 | 回答形态 |
|---|---|---|
| 判题模型 | `calibrated`：校准判题模型（如 jev），provider 与模型由配置指定 | decisions API 记录级调用，逐题返回选项 |
| 确定性事实 | 本地规则 | `field_rule` 信号按冻结模式命中即生效，无随机性 |

已接受的取舍，留痕：判题模型不返回可逐字校验的引文；判定记录保存四值回答，排除依据由回答和冻结规则重算。
**并发与成本。** 并发为**记录级**：一条 citation 的全部语义信号尽可能在同一请求内回答，默认并发 15。确定性规则已形成排除的记录不调用模型。preview 按未筛数、预设输入输出 token 数及配置单价估算上界，实际调用与费用写入 manifest。

**失败与恢复。** 单条 citation 的调用失败按配置重试，失败不阻塞其他记录。一个 MCP 调用只处理至多 `batch_size` 条；`cost_cap` 限制该批的 API 请求尝试数，包括重试。事件日志持续保存已完成项，未完成项保持 `pending`；宿主用同一 `plan_hash` 调用 `panner_screen_continue` 处理下一批。

**确定性声明。** manifest 记录判题模型和配置。同配置重跑不承诺逐字节相同的模型响应；判定可复现指的是：给定落盘的判定记录，求值器重算的结果必须一致。

**结论与运行状态分开。** 完成判定的记录取 `excluded` 或 `retained`；运行中可有 `pending`，调用失败可有 `failed`。`retained` 表示题名摘要阶段没有足够依据排除，交后续评估，不代表最终纳入综述；`pending` 和 `failed` 也必须进入后续评估的交接文件，不能从中消失。

## 运行配置（run config）

运行配置是用户指定的 JSON 文件；preview 将其哈希绑入 `plan_hash`，确认运行时在 `screening/vN/run-config.json` 保存逐字节快照。改变配置后需重新预览并生成新的运行版本。schema：

| 字段 | 含义 |
|---|---|
| `backend` | 固定 `calibrated` |
| `provider` / `model` / `endpoint` | 判题服务的提供方、模型及 API 端点 |
| `concurrency` | 记录级并发，默认 30 |
| `batch_size` | 单次 MCP 调用最多处理的 citation 数，默认 250 |
| `cost_cap` | 单批最多 API 请求尝试数，包括重试 |
| `price_per_1m_input` / `price_per_1m_output` | 预览估算所用单价；未填时采用明确标出的规划默认值，并非实时服务报价 |
| `retry` | 失败重试次数与退避 |
| `relevance` | 仅当协议启用路由时：路由后端与阈值；首版默认仅复核排序，解锁排除须记录验证依据 |
| `confidence_floor` | 可选，0–1；判题模型给出的 confidence 低于该值的回答降级为 unclear（低置信不是证据，不改写回答本身，只改判定输入）；判题模型不返回 confidence 时该设置无效 |
| `judge_channel` | 声明本次判定实际由谁作出，默认 `calibrated-decision-model`；用本机 LLM 代理（`tools/local_decision_proxy.py`）代替 jev 时必须显式标注，因为该通道不返回概率，而 manifest、筛选预览与导出报告的 Summary 都会带上这个字段 |

**密钥不进 workspace**：API 密钥由 `PANNER_DECISION_API_KEY` 提供；预览展示端点及将发送的题名摘要字段。默认只允许 `openrouter.ai`，其他主机须由运行环境的 `PANNER_ALLOWED_DECISION_HOSTS` 明确列入；远程端点须使用 HTTPS。

## 数据契约

判定记录与结果按版本目录 `screening/vN/` 保存；更换运行配置或重跑生成新目录，旧结果可回查。

| 文件 | 内容 |
|---|---|
| `events.jsonl` | 逐条追加的已完成判定事件，供中断后恢复 |
| `run-config.json` | 运行配置的逐字节快照 |
| `judgments.jsonl` | 已完成 citation 的 `{citation_id, entities, answers: [{signal, on, answer, confidence?}]}`；判题模型返回的 confidence 一并保存，供复核与阈值使用 |
| `screening-results.jsonl` | 每条待筛 citation 一行：`{citation_id, status: excluded\|retained\|failed\|pending, basis?, undetermined?, error?}` |
| `manifest.json` | 计划哈希、协议与池哈希、运行配置、用户确认、各状态计数、调用数与成本、结果哈希 |

判定记录的完整性由工具强制：每道语义信号有回答或明确的 unclear 原因；结果与判定记录可由求值器重算复现。

## 结果导出

筛选结果以**派生视图**交用户：一个 MCP 工具从确认产物（筛选结果、池、冻结协议）生成三份文件，写入用户指定的新目录。导出只读、幂等、不构成新版本，因此单段式（不走 preview/confirm）：

```text
panner_screen_export(workspace_path, output_dir, results_path?) → 三份文件路径与计数；results_path 缺省取 active_screening
```

| 文件 | 内容 |
|---|---|
| `screening-report.xlsx` | Summary（协议版本、池、后端与模型、调用数与成本、计数）；Citations（一行一条 citation：id、status 按值着色、题录字段、排除依据 = 触发规则 + 条款原文引文；冻结表头、自动筛选）；Signals（每条回答一行：signal、question、on、answer、confidence） |
| `screening-retained.ris` | 所有未自动排除条目的标准 RIS，包括 retained、pending、failed；notes 注明实际状态；交文献管理软件做后续评估 |
| `screening-excluded.ris` | 排除条目的标准 RIS，notes 记 Panner 判定（规则 + 条款引文），导入后仍可核对 |

派生视图不引入新事实：题录字段来自确认池的统一结构（原始来源中未映射进 17 字段的信息不回带，如关键词）；续跑获得新判定后可重新导出。

## MCP 工具与调用契约

工具接口（预览与确认分开，长运行按批继续）：

```text
panner_screen_guide() → 执行与审阅指引
panner_screen_preview(workspace_path, protocol_path, pool_path, run_config_path) → 计划：待筛数、信号数、模型及端点、并发、批量上限、预计调用量与成本、拟写入文件、plan_hash
panner_screen_confirm(workspace_path, protocol_path, pool_path, run_config_path, plan_hash, user_confirmation) → 确认计划并执行首批
panner_screen_continue(workspace_path, plan_hash) → 基于已确认计划继续处理下一批
panner_screen_status(workspace_path) → 当前结果版本及一致性检查
panner_screen_export(workspace_path, output_dir, results_path?) → 结果导出：xlsx 报告 + retained/excluded 两个 RIS
```

预览核对协议与池都属于当前 workspace 的活动确认版本，并把**预计调用量与成本、端点、并发及单批上限**展示给用户；用户确认后执行。整个流程的最新状态用 `panner_workflow_status` 查询（见 [00](00-positioning.md) 的工作流编排）。

## 给宿主 agent 的工作流指令

英文与 `server.py` 中的运行时指引对应；中文供审阅。

```text
You are running PANNER step 04: screening. Converse with the user in the user's language.
Read the active frozen protocol, the citation pool derived from the active citations, and the calibrated-model run configuration. Only rows with an empty duplicate_of are screened.
Call panner_screen_preview and show the user the plan: how many citations, how many semantic signals, the model and destination endpoint, the concurrency, the maximum calls, the estimated cost with its basis, and the files to be written. Explain that title and abstract are sent to the configured decision endpoint. Resolve every issue before confirming.
After the user confirms, call panner_screen_confirm with the same plan_hash. A call processes at most one configured batch. While its result is partial with pending citations, call panner_screen_continue with that plan_hash; the saved confirmation remains valid for this unchanged plan. Failures retry per the config and never block other records. Failed or pending citations remain visible for later assessment.
Show the user the results - excluded with their rule basis, retained with their undetermined reasons, and failed or unfinished with their errors - then call panner_workflow_status to continue.
```

```text
你正在执行 PANNER 的 04：基于原子信号问题的筛选。使用用户的语言与用户交谈。
读取活动冻结协议、从活动 citation 版本派生的确认池与判题模型运行配置；只筛选 duplicate_of 为空的行。
调用 panner_screen_preview，向用户说明待筛数、语义信号数、模型与端点、并发、调用上限、成本估算依据及拟写入文件；题名摘要将发送到配置的判题端点。先解决问题再确认。
用户确认后，以同一 plan_hash 调用 panner_screen_confirm。每次调用最多处理一批；若有 pending，继续调用 panner_screen_continue，不重复征求同一计划的确认。失败按配置重试且不阻塞其他记录；pending 与 failed 始终保留在后续评估交接中。
向用户说明 excluded 的规则依据、retained 的未决原因、failed 或 pending 的状态，再调用 panner_workflow_status。
```

→ 首轮筛选之后：`retained` 记录交后续评估（复核、全文获取与最终资格判断），那是 PANNER 工作范围的边界，见 [00](00-positioning.md)。
