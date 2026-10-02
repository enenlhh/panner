# 03 编译原子信号与组合规则

> 本环节只完成一件事：把 01 已确认的资格标准形式化为可审阅、可校验、可冻结的题名摘要筛选协议。协议包含**原子信号问题**和**组合这些答案的规则**；单题答案只陈述 citation 显示了什么，不直接给出 `excluded` 或 `retained`。
> 输入：01 已确认其表述准确的 `eligibility-criteria.md` 及随版本保存的待澄清清单。02 的 `citation-pool.jsonl` 用于样例试读，不产生新的资格限制。产物：经用户确认的 `protocol/vN/protocol.json` 及同目录 `manifest.json`。

## 职责与判断逻辑

### 编译时的资格语义澄清

01 提供用户目前能准确表达的标准；03 才逐条检查这些文字能否转成原子信号问题和组合规则。宿主 agent 编译到某条标准时，针对实际缺口与用户多轮交流：**缺少什么决定、它会怎样改变题目的选择条件或最后的组合结果**。标准已经足够明确的部分直接编译，不为完成固定问卷重复询问。

| 编译需要明确的含义 | agent 何时追问用户 |
|---|---|
| 判断单位 | 无法确定标准约束整个研究、某类人群、研究组、干预方案、一项组间比较，还是某份报告时 |
| 量词 | 无法确定只需存在一项符合条件的比较，还是所有相关组或比较均须符合时 |
| 比较关系与方向 | 无法确定哪些组构成目标比较，或作者标注的干预／对照角色能否对调时 |
| 方案组成 | 单用、联合使用、剂量、安慰剂、其他有效措施或共同背景治疗会改变某组是否属于允许方案时 |
| 限制范围 | 某项不允许的特征可能使整个研究失格，也可能只使涉及它的组、方案或比较失格时 |
| 部分符合与可观察性 | 混合人群、多项比较、缺少组间细节或只有 citation 信息时，无法决定何种证据足以作答或必须留待全文时 |

这些是**按需使用的语义检查维度**，不是预设纳排条款，也不是 01 必须先完成的问卷。agent 可用简短情形检验用户决定是否一致：交换组的作者标签、增加一个不参与目标比较的组、在一组或两组增加附加措施。情形只用于找出影响编译的缺口，不直接变成通用规则。[Cochrane Handbook 第 3 章](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-03)要求预先界定比较对象和联合干预；[第 23 章](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-23)按能否形成符合综述要求的比较判断多组研究中哪些组相关。

03 读取 01 留下的待澄清清单，也会在编译中发现新的问题。agent 在**当前编译对话**中向用户提问并记录回答；若回答增加或修改资格限制，调用 01 的文本预览与确认工具保存新的 `eligibility-criteria.md` 版本，再依据该版本继续编译。无需重新开始 01。02 的 citation 内容可用于检验协议是否忠实表达标准，不能反过来生成新限制。

### 每条标准的作用与去向

宿主 agent 将 01 的文本拆成可单独追踪的条款，赋予 `E001…` 编号。每个条款记录其作用、约束对象、所需信息和后续去向；同一条款可产生多个信号，也可同时留下全文核查事项。

| 条款情形 | 03 的处理 |
|---|---|
| 研究资格要求，可从 citation 观察相关事实 | 编译所需信号，再以对应范围和量词纳入组合规则 |
| 研究资格要求，需要全文、亚组、多个报告或研究层面信息 | 保留在标准去向表中；可观察部分仍可编译，剩余部分交后续评估 |
| 用户明确规定的摘要文本要求 | 只按已确认的文本要求编译；例如用户明确规定"摘要中必须测量某结局"，即摘要必须写明研究测量了该结局，并解释其资格作用 |
| 综述关注的结局或要提取的数据 | 记录为研究问题或数据收集目标，不自行成为题名摘要资格限制 |
| 全文是否可获取 | 交全文获取流程，不作为 citation 中的资格信号 |
| 背景说明与定义 | 保存为解释相关条款的上下文 |

"研究测量了结局"与"摘要写明测量了结局"是不同要求。前者不能从摘要未提及推断为假；后者只有在用户明确把**完整摘要文本中的陈述**设为资格限制并说明理由时，才可能按文本有无作答。若 `abstract` 字段缺失或来源导出不完整，仍为 `unclear`。用户若原本只写"必须测量结局"，03 需澄清或留待全文，不能改写成摘要文本要求。[Cochrane Handbook 第 3 章](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-03)提醒，不能仅因没有报告可用结局数据而排除研究。

标准没有规定的语言、年份、出版形式、国家等限制不进入协议。"纳入 RCT"本身不推出"排除会议摘要／protocol"等。全文可得、试验是否终止、是否为同一研究的唯一报告等需要其他来源或跨报告核对的事项，记录相应去向。

### 标准覆盖、澄清与冻结

`criteria_map` 对每个条款记录准确原文及位置、`role`、`target`、关联信号、关联组合规则与后续交接。工具核对每个 `criterion_refs` 的原文片段与 01 的已确认版本一致，列出未映射文本供人审阅。

待澄清问题不设默认答案。尤其当 03 无法确定判断单位、量词、比较方向、联合措施或限制范围时，协议保持草稿，agent 在编译对话中继续问用户；若决定改变资格标准文本，先形成新的 `eligibility-criteria.md` 版本，再继续编译。若初始标准已经足够明确，03 直接编译，不重复访谈。

冻结前，从 02 的 citation 池挑选明确相关、明确不相关和边界／信息缺失的样例，逐题试读四值答案，并试算组合规则对多组、角色互换、联合措施等适用情形的结果；这些情形按实际标准挑选，不作为固定测试清单。[Cochrane Handbook 第 4 章](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-04)也建议用明确符合、明确不符合和存疑的报告试测资格标准。试读引用的 citation ID、池版本、期望答案及用户反馈随协议保存。

用户确认标准去向、全部原子信号问题及其四值条件、组合规则、核心标记、试读结果与后续事项后，工具把草稿冻结为新的 `protocol/vN/protocol.json`，同目录 `manifest.json` 记录 01 标准版本与哈希、协议哈希、确认原话、数量汇总和试读的输入与试算结果；旧版本保留，workspace 指向最新冻结版本。04 只读取冻结协议与 02 的 citation 池执行。

## 数据契约

### 原子信号问题：回答事实，不给结论

一道**原子信号问题**询问一个可独立从指定 citation 字段判断的事实维度。问题必须说明它针对的对象，取五个固定值之一：研究（`study`）、人群（`population`）、研究组（`arm`）、干预方案（`regimen`）、citation 文本（`citation_text`）。**比较不作为信号对象**：一组是否构成合格比较由组合规则表达（组级信号的命中加上范围与方向条件），这使每条回答的绑定都落在单个可观察对象上。同一道题不能把不同判断维度合成一个不可拆解的答案；同一维度的允许值可以在题干中列出。

每题有 `hit`、`partial`、`miss`、`unclear` 四个答案，并为**每个答案**写出本题的选择条件：

| 答案 | 通用含义 |
|---|---|
| `hit` | citation 明确支持题干所问的事实 |
| `partial` | citation 明确显示同一维度内有符合与不符合的子群或成分；不能用来代替信息不足 |
| `miss` | citation 明确支持与题干相反的事实；研究特征未被提及不算 `miss` |
| `unclear` | 未陈述、表述不足、来源缺失或相互矛盾，无法可靠归入前三值；运行记录注明原因 |

若用户明确规定"完整摘要必须写明某信息"，并核实摘要文本完整，所要求内容的缺席可以按照该**文本要求**的选择条件作答；这不能推广为研究未实施某措施。`partial` 和 `unclear` 的后果由组合规则处理，不能在单题层预设"通过"或"排除"。

每条信号用 `unit` 声明对象层级；**对象绑定不是信号的独立字段，而是出现在回答与规则中**。04 的每条回答记录 `{signal, on, answer}`：`on` 指向该 citation 内的局部对象名——记录级固定为 `record`，组或方案级为该 citation 内的局部组名，比较级由规则用 `member0`、`member1` 指认一对组中的成员；回答同时附 citation 的实际证据片段与位置。若题名摘要只表明存在某个比较、没有完整列举各组，回答只绑定已观察到的对象；不能凭空补齐其他组。示意信号如下，它只记录一个组的观察：

```json
{
  "id": "S001",
  "unit": "arm",
  "question": "Does this study group receive the target intervention?",
  "core": true,
  "evaluation_mode": "semantic",
  "evidence_fields": ["title", "abstract"],
  "answer_criteria": {
    "hit": "The citation explicitly states that this group receives the target intervention.",
    "partial": "The citation explicitly states that only a defined part of this group or treatment period receives it.",
    "miss": "The citation explicitly states this group's regimen and it does not contain the target intervention.",
    "unclear": "The regimen of this group is absent, incomplete or conflicting."
  },
  "criterion_refs": [{"criterion_id": "E001", "quote": "<用户标准中的准确原文片段>"}]
}
```

`S001=hit` 只说明**某个被绑定的组**出现了目标措施。它尚未说明是否存在合格对照、两组是否可比较、联合措施是否被允许，也不产生记录层排除。PANNER 的全部生成内容使用英文：题干、四值条件、facet、任务说明与场景描述必须是英文，工具在校验中拒绝这些字段的中日韩文本；`criterion_refs` 保留用户标准的原文，翻译对照供用户核对。信号的全部结构校验（对象枚举、四值条件、字段约束、引文逐字性、语言）见 [protocol.py 的 `_check_signal`](../src/panner_mcp/protocol.py)。

### 组合规则：在正确范围内解释信号

协议草稿分开保存 `signals` 与 `decision_rules`。规则字段固定为 `{id, kind, unit, quantifier, condition, criterion_refs}`，只有两类：

| `kind` | `unit` 与 `quantifier` | 何时形成排除候选 |
|---|---|---|
| `disqualifier` | `record`，`quantifier: "all"` | 条件被明确满足：作用于整个记录的失格事实成立 |
| `disqualifier` | `arms`，`quantifier: "any"` 或 `"all"` | 任一组（`any`）或所有组（`all`）明确满足失格条件 |
| `qualification` | `comparison`，`quantifier: "exists"` | 全部候选比较都明确不合格；候选比较是已枚举组构成的有序对 |

`condition` 由 `all`、`any`、`not` 组合，叶子固定为 `{signal, on, expect}`。`expect` 只允许 `hit` 或 `miss`——partial 与 unclear 永远不构成依据。`on` 把叶子绑定到对象：record 规则用 `record`；组级规则用 `each`；比较规则用 `member0`、`member1` 指认一对组中的成员（引用组级信号），也可引用 `record` 级信号。绑定、限制范围、完备性与未决处理不是逐条书写的字段，而由这套结构统一承载：

- **对象绑定**：叶子 `on` 与 04 回答的 `on` 指向同一个局部对象；两道信号的命中是否来自同一对组，由绑定一致性决定。
- **限制范围**：不允许的特征作用于整个研究时，写成 `disqualifier`（`arms` + `any`）；只作用于用户关心的比较时，写成 `qualification` 条件内的 `not` 叶子。同一事实在两种范围下结论不同；范围由 01／03 的澄清决定，不由编译器猜测。
- **完备性**：求值只在所有候选比较都明确不合格且枚举非空时得出"无合格比较"。只要存在方案未说明的组、未决的回答或枚举不全的对象，结论自动为未决并保留；规则不得、也无须另行声明完备性证据。
- **未决处理**：partial、unclear、缺失或相互矛盾的答案一律按未决求值；未决永不排除，整条 citation 保留并注明未决来源。

方向可对调的 A–C 比较由对称析取表达；成员角色互换的组对同样满足条件：

```json
{
  "id": "R001",
  "kind": "qualification",
  "unit": "comparison",
  "quantifier": "exists",
  "condition": {
    "all": [
      {"any": [{"signal": "S003", "on": "member0", "expect": "hit"}, {"signal": "S003", "on": "member1", "expect": "hit"}]},
      {"any": [{"signal": "S004", "on": "member0", "expect": "hit"}, {"signal": "S004", "on": "member1", "expect": "hit"}]},
      {"not": {"signal": "S005", "on": "member0", "expect": "hit"}},
      {"not": {"signal": "S005", "on": "member1", "expect": "hit"}}
    ]
  },
  "criterion_refs": [{"criterion_id": "E002", "quote": "<标准原文片段>"}]
}
```

作用于整个记录的失格规则示意：

```json
{
  "id": "R002",
  "kind": "disqualifier",
  "unit": "record",
  "quantifier": "all",
  "condition": {"signal": "S002", "on": "record", "expect": "hit"},
  "criterion_refs": [{"criterion_id": "E003", "quote": "<标准原文片段>"}]
}
```

上面的求值语义（三值真值表、存在性否定的完备性要求、未决永不排除）实现为纯函数，见 [evaluate.py](../src/panner_mcp/evaluate.py)：叶子与布尔组合在 [`_condition`](../src/panner_mcp/evaluate.py)，两类规则在 [`_disqualifier_value`](../src/panner_mcp/evaluate.py) 与 [`_qualification_value`](../src/panner_mcp/evaluate.py)，入口为 [`evaluate_rules`](../src/panner_mcp/evaluate.py)。这份求值器是 03 试读与 04 执行共用的同一份代码。多组与角色互换、无关组不改变比较资格、联合干预不可判定、组枚举不全等情形已作为固定断言钉在 [test_compile.py](../tests/test_compile.py)。

对于以比较为判断单位的要求，通用逻辑是：

```text
若已确认一项作用于整个研究或报告的失格条件：形成 excluded 候选。
否则，在同一研究内按 01 的标准检查每项可能的比较：
  所用的组、方案、方向及共同措施均在这项比较的范围内判断；
  与这项比较无关的其他组，依 01 确认的作用范围处理。
若完整且可靠的证据穷尽了可能的比较，且每项比较均明确不符合：形成 excluded 候选。
其余情形，包括存在可能符合的比较或组间信息不完整：retained。
```

这个框架也适用于队列研究的暴露组、未暴露组及其他比较结构；具体的分组与可比关系由 01 的综述问题决定。研究作者将两组标为"干预／对照"的方向是否可互换，不能由编译器猜测。若用户确认方向对资格无影响，规则同时允许两种标签顺序；后续效果量的方向仍可在数据提取时统一。

**存在目标措施的组**与**存在对照措施的组**是有用的原子事实，但两题同时为 `hit` 不足以证明两组构成允许的比较。相应地，"不允许的措施出现在某组"为 `hit`，也不足以证明整个研究应排除。组合规则必须利用对象绑定和 01 已确认的作用范围。对于"没有合格比较"的结论，citation 必须明确给出足以穷尽候选比较的组间信息；仅因摘要没有提到某组，不能作出这种结论。

试读情形只用于**检验规则是否表达了用户决定**，不写成针对特定措施的通用规则：

| 试读情形 | 应检查的关系 |
|---|---|
| 两个目标措施在作者标签中互换位置 | 01 是否允许这种比较方向；信号绑定能否识别同一对组 |
| 多组研究中另有一组使用不允许的措施 | 该限制作用于整个研究，还是只作用于涉及该组的比较；是否仍有符合条件的另一项比较 |
| 目标措施与其他措施联合使用 | 联合方案是否被允许；附加措施是否为安慰剂、有效措施或两组共同背景；所选两组是否仍构成允许的比较 |

03 只定义规则与触发条件；04 取得单题答案和证据、执行冻结的规则，并按预先规定的确认门生成 `excluded` 或 `retained`。`retained` 表示题名摘要阶段没有足够依据排除，仍需后续评估，不代表最终纳入综述。

### 协议草稿的结构

协议草稿是一个固定结构的 JSON 文件，顶层七个键：

| 键 | 含义 |
|---|---|
| `format` | 固定 `panner-protocol-v1` |
| `signals` | 原子信号问题列表，字段见上文示意 |
| `decision_rules` | 组合规则列表，字段见上文示意 |
| `criteria_map` | 条款去向表：`{id, quote, occurrence, role, target, signal_ids, rule_ids, handoffs, decisions?}`；`role` 取 `eligibility`、`review_outcome`、`retrieval`、`data_handling`、`context`，`target` 描述条款约束的对象，`handoffs` 记 `{stage, task}`，`stage` 取 `full_text`、`retrieval`、`data_collection` |
| `clarifications` | 未决问题清单；冻结前必须为空 |
| `relevance` | `{enabled, facets, note}`；启用时每个 facet 记 `{id, aspect, question}` |
| `screening_review` | 试读场景列表，每个场景记 `{citation_id, scenario, entities, answers, expected_status, pool?, user_feedback?}`；`pool` 在引用真实池内 citation 时记录其确认版本 |

试读场景中，`entities.arms` 列出该 citation 枚举到的对象名，`answers` 是人工标定的期望回答（`{signal, on, answer}`）；工具按冻结的求值语义逐场景试算并对照 `expected_status`，两者不符时协议保持草稿。场景可以用假设情形检验多组、角色互换、联合措施等结构；引用真实 citation 时从 02 的确认池中选取。协议内容字段的英文要求与用户内容的原文保留同样适用于本表；`clarifications` 是冻结前面向用户的中间产物，不进入冻结协议。草稿整体结构、ID 唯一性、条款覆盖与引文逐字性的完整校验见 [protocol.py 的 `_check_draft`](../src/panner_mcp/protocol.py)；试读试算见 [`_evaluate_trials`](../src/panner_mcp/protocol.py)。

### 核心信号、读取方式与成本

`core: true` 标记界定综述主题的少数信号，如研究设计、人群或目标措施。核心标记只决定相关度路由与复核重点，不赋予单题排除权。可安全编译时协议至少有一道核心题；若无合适题，`relevance.enabled` 记为 false，并在 `relevance.note` 说明原因。按研究组等单位重复回答的核心题，须先汇总同题各对象的已知答案与未决对象，不能因某一个组为 `unclear` 就把整题视为 `unclear`。全部核心题仍为 `unclear`、摘要很短或缺失时，可进入由宽泛 `relevance.facets` 构成的主题相关度路由；该事实本身不导致排除。相关度后端及阈值需经适当真值数据验证并随运行配置冻结，未通过验证时只用于复核排序。

每题列出 `evidence_fields` 和 `evaluation_mode`：`field_rule` 用固定模式读取 `publication_types`、`year` 等结构化字段，空值及未匹配值进入 `unclear`；`semantic` 读取题名摘要并按该题四值条件回答。规则题的模式、末位兜底和读取字段须可校验；题名摘要不使用任意子串模式代替语义判断。实际模型与判题后端由 04 运行配置指定。

## MCP 工具与调用契约

Panner 提供 `compile_screening_protocol` MCP prompt 作为用户可选的启动入口，也提供 `panner_compile_guide` 工具供宿主 agent 取得同一指引：

```text
panner_compile_guide() → 编译任务与审阅指引
panner_compile_preview(workspace_path, criteria_path, draft_path) → 结构与覆盖检查、标准覆盖、待澄清事项、试读试算结果、结构警告和 plan_hash
panner_compile_calibration(workspace_path, criteria_path, draft_path, run_config_path, labels_path?) → 用草稿协议对综述自带的标注纳入集做判题读数，写出 calibration/compile-<draft_sha256[:12]>.json
panner_compile_freeze(workspace_path, criteria_path, draft_path, plan_hash, user_confirmation) → 冻结的 protocol/vN/protocol.json 与 manifest（工作区带标注集时，校准未通过则拒绝冻结）
panner_compile_status(workspace_path) → 当前冻结协议版本及一致性检查
```

`workspace_path` 是 PANNER workspace；`criteria_path` 必须指向当前已确认的 01 标准版本，工具核对其 manifest 与文件哈希（见 [`_confirmed_criteria`](../src/panner_mcp/protocol.py)）。预览不写文件；计划变化后旧 `plan_hash` 失效。

宿主可读资源：本环节数据契约的英文运行时正本在 `panner://contracts/protocol`（顶层键、signal 与 rule 的完整字段、condition 叶子与 on 取值、criteria_map/relevance/screening_review 结构、求值不变量），一份完整可运行的示例草稿（含对应标准原文与试读场景）在 `panner://examples/protocol-draft`——示例通过真实的 preview 校验与试算断言，宿主起草时照此结构。

预览工具检查：`criteria_path` 指向已确认且未被改动的标准版本，每个引用片段逐字存在于该版本；信号、规则与条款 ID 唯一；每题四个 `answer_criteria` 完整；每条规则的单位、量词及条件引用的信号与对象绑定一致，`expect` 只取 `hit` 或 `miss`。两类错误由结构排除，无需逐条校验：单题答案永远不能直接等同记录结论；未报告的信息永远不能证明不存在合格比较。预览还列出未覆盖的标准文本、未解决的澄清、重复引用的片段与仅需字段读取却交给语义后端的题，逐场景试算 `screening_review`——期望与试算不符时协议保持草稿——并给出**结构警告**（见下节）。机械校验不能证明临床解释正确，agent 须把信号与规则的完整文字、组合后的试读结果与全部警告交用户审阅。

### 校准读数：用综述自己的纳入集检验草稿

`screening_review` 的期望答案与期望结论由同一个 agent 撰写，只能检验内部一致性：若 agent 对某条判据的理解本身偏了，试读会与它一起偏。因此工作区可以携带综述自带的纳入清单 `labels/included.txt`（每行一个标识：`pmid:`、`doi:`、`title:`，或来源自带 id），并由 `panner_compile_calibration` 做一次真实读数：用冻结前的草稿协议、按运行配置的判题模型，对标注记录逐条判题并用同一求值器出结论，写出 `calibration/compile-<draft_sha256[:12]>.json`（标签到 citation 的映射、逐条状态、触发规则、回答、调用数与成本）。

判据：

- 工作区存在 `labels/included.txt` 时，**当前草稿没有对应校准报告，冻结被拒绝**；
- 报告中出现被排除的标注纳入记录时，**冻结被拒绝**，错误里列出该标签与触发规则；
- 标签在池中找不到时记为 `unmapped`——那是 02 的问题（检索未召回），不是协议缺陷，应向用户报告而不是改协议去迁就。

校准读数的成本与标注集大小成正比（数十条记录约 $0.01 量级），比跑完整个池再发现误排便宜两个数量级。代价不对称是这一步存在的理由。

### 结构警告：合法但危险的规则形态

两种排除形态的破坏力不同：记录级 disqualifier 命中即排除**整条**记录；比较规则内部的 `not <组信号> hit` 叶子是**成员否决**，一个组的错误回答就会否掉**所有**候选比较，于是记录以"不存在合格比较"被排除。因此预览会对下列结构给出警告（不是结构错误，但冻结前必须回答）：

- 比较规则里出现对组信号（unit 为 `arm`/`regimen`）的 `not` 叶子——对象级限制应当写进读该对象的信号判据，让不合格的组自然不满足干预问题；
- `unit: arms` 且 `quantifier: any` 的 disqualifier——任一组满足即排除整条记录，须确认这正是想要的适用范围。

对应实现见 [`_lint_draft`](../src/panner_mcp/protocol.py) 与 [`_require_calibration`](../src/panner_mcp/protocol.py)（冻结门禁）。校准读数见 [calibration.py](../src/panner_mcp/calibration.py)。

| 文档描述 | 实现 |
|---|---|
| 预览（结构、覆盖、试算、plan_hash） | [protocol.py](../src/panner_mcp/protocol.py)：`preview_compile` |
| 冻结落盘：protocol.json、manifest、版本目录 | [protocol.py](../src/panner_mcp/protocol.py)：`freeze_compile`、`protocol_status` |
| 求值语义（03 试算与 04 执行共用） | [evaluate.py](../src/panner_mcp/evaluate.py)：`evaluate_rules` |
| 工具注册与加载入口 | [server.py](../src/panner_mcp/server.py)：`panner_compile_*` 工具、prompt `compile_screening_protocol` |
| 行为测试（极端案例、流程、MCP 发现） | [test_compile.py](../tests/test_compile.py)、[test_mcp_eligibility.py](../tests/test_mcp_eligibility.py) |

### 规则的三分归位

每条编译规则落在三个载体：文档（给人审）、指令（给宿主 agent 的运行时来源）、校验器（工具强制）。语义规则工具无法校验，必须全部在指令中；下表是本环节的归位清单，新增规则时先填此表：

| 规则 | 文档 | 指令 | 校验器 |
|---|---|---|---|
| 一题一个事实维度，声明对象（六值） | 数据契约 | 句 6 | `_check_signal` |
| 四值各写选择条件；partial 非信息不足；unclear 含矛盾 | 数据契约 | 句 6 | `_check_signal`（四键齐全、非空） |
| 未提及不是 miss；唯一例外为明确的完整摘要要求 | 职责 | 句 5 | 语义规则，人审 |
| 条款不静默丢弃，资格条款须有去向（信号/规则/handoff/显式不限制决定） | 职责 | 句 2 | `_check_draft`（eligibility 无去向报 issue） |
| 潜台词义务：未提的轴默认无限制 | 职责 | 句 3 | 语义规则，人审 |
| 文本要求与研究要求区分 | 职责 | 句 5 | 语义规则，人审 |
| 引文逐字；多题同片段须解释 | 数据契约 | 句 7 | quote 检查 |
| 对象绑定在回答（`on`），不虚构对象 | 数据契约 | 句 7 | 叶子 on 兼容性校验 |
| `expect` 只许 hit／miss | 数据契约 | 句 8 | `_check_leaf` |
| 单题答案不产生排除；结论只出自规则求值 | 数据契约 | 句 9 | 结构保证（求值器唯一入口） |
| 存在性否定需完备枚举 | 数据契约 | 句 9 | `_qualification_value` |
| 未决永不排除，未决即保留 | 数据契约 | 句 9 | `evaluate.py` 三值语义 |
| core 不赋排除权；全 unclear 走路由；无 core 记 note | 核心信号 | 句 10 | `_check_relevance` |
| 协议内容英文；用户原文保留 | 数据契约 | 句 11 | `_requires_english` |
| 澄清无默认答案，冻结前必须为空 | 职责 | 句 4、13 | clarifications 非空 → draft |
| 试读不符保持草稿 | 职责 | 句 12、13 | `_evaluate_trials` mismatch |
| 对象级限制写进读该对象的信号判据，禁止比较内的按组否决 | 职责、数据契约 | 句 9 | `_lint_draft` 警告 |
| arms/any 排除整条记录，须确认适用范围 | 数据契约 | 句 9 | `_lint_draft` 警告 |
| hit 必须是明确陈述，不得从研发阶段/获批状态推断 | 数据契约 | 句 6 | 语义规则，人审 + 校准读数 |
| 有标注纳入集时必须先通过校准读数才可冻结 | 职责、校准节 | 句 12b、13 | `_require_calibration` 门禁 |

## 给宿主 agent 的工作流指令

宿主通过 prompt `compile_screening_protocol` 或 `panner_compile_guide` 工具取得同一段工作流指令。PANNER 的全部生成内容使用英文，指令全文以 [server.py 的 `COMPILE_GUIDANCE`](../src/panner_mcp/server.py) 英文正本为唯一运行依据；以下为逐句对照的中文翻译，供审阅。

```text
你正在执行 PANNER 的 03：把已确认的资格标准编译为可冻结的筛选协议。使用用户的语言与用户交谈。
先读取 panner_eligibility_status 返回的当前标准全文及其待澄清问题清单。把标准拆分为可单独追踪的条款（E001...），并确定每个条款的作用（eligibility、review_outcome、retrieval、data_handling 或 context）与去向；题名与摘要能观察的部分进行编译，其余记为 handoffs 交全文、检索或数据工作，绝不静默丢弃任何条款——资格条款必须至少有一个信号、一条规则、一个 handoff，或 decisions 中的一条显式不限制决定。
潜台词是一项正向义务：标准未提及的轴默认不设限制。不要为标准从未提及的出版形式、语言、年份或国家生成排除问题，也不要从常见做法或检索池的特征推断排除；“纳入 RCT”本身不排除会议摘要或研究方案。
编译某条条款时，若缺少会改变作答条件或组合结果的决定，就该项具体缺口询问用户，覆盖七个维度：判断单位、限制由哪个对象承担（整条记录／比较／单个组）、量词、比较关系与方向、方案组成、限制的作用范围，以及部分证据或有限可观察性对该条款意味着什么。对象是组时，再问该限制是否属于这一组的资格定义，而不是一条独立的排除。待澄清问题没有默认答案；用户的回答若修改了资格标准，先经 01 的预览与确认保存新版本，再继续编译。
区分标准约束的对象：关于研究的要求（“研究测量了该结局”）不是关于摘要文本的要求（“摘要写明测量了该结局”）。只有当用户明确把文本要求设为摘要措辞上的资格条件并说明其作用时才编译它；否则记录 handoff。摘要中的缺席不是 miss，唯一例外是用户明确确认的完整摘要要求，经核实后的缺席按该要求自身的条件作答。
起草原子信号问题：每题只问一个事实维度，说明其针对的对象——研究、人群、组、方案、比较或 citation 文本——并为 hit/partial/miss/unclear 各写选择条件。partial 指 citation 在该维度内明确同时显示符合与不符合的部分，绝不用于表示单纯的信息不足；unclear 涵盖未陈述、不足、缺失或相互矛盾的信息；缺席不是 miss。每条 hit 条件都必须是 citation 必须明确陈述的内容，绝不让 hit 从推断中产生：写着“正在研发”的药不是“未获批”的药，研究该药也不等于“没有 III 期数据”；标准若以获批状态或是否存在 III 期数据设限，hit 条件就必须要求 citation 明说这一事实。
绑定属于回答而不属于信号：04 记录 {signal, on, answer}，on 指明所观察到的对象；citation 未完整描述的对象在回答中保持 unclear，而不是被虚构。在 criterion_refs 中逐字引用标准；当多道问题引用同一片段时，说明每道问题各自捕捉什么不同的排除事实。
按固定 schema 起草组合规则：kind 为 disqualifier（unit 为 record，或 arms 且 quantifier 为 any/all）或 qualification（unit 为 comparison、quantifier 为 exists）；条件由 all/any/not 组合，叶子为 {signal, on, expect}，expect 只取 hit 或 miss。
两种排除形态不能互换。记录级 disqualifier 在其条件被明确满足时排除整条 citation；比较规则只回答一件事——是否存在某一对组构成合格的干预—对照比较；它内部的 `not <组信号>` 叶子是成员否决，一个组的错误回答就会一次否掉所有候选比较。因此对象级限制——哪种药算干预、哪个组合被排除、哪个亚组不合格——应写进读取该对象的信号判据，让不合格的组自然不满足干预问题；只有以"试验"本身为主语的限制才做记录级排除。绝不把对象级限制实现成比较规则里的按组否决。
组合逻辑是固定的：在其已确认范围内被明确满足的 disqualifier 产生排除候选；只有当所有候选比较都明确不合格且对象枚举完整时，才能得出“不存在合格比较”；partial、unclear、缺失或相互矛盾的回答保持未决，未决永不排除——该 citation 被保留并保留原因。
把少数界定综述主题的正向问题标为 core。core 标记绝不赋予单题排除权；当全部 core 问题保持 unclear 时，citation 被送往主题相关度路由（relevance.facets）而不是被排除，且相关度路由只有在经标注数据验证并随运行配置冻结之后才可排除——否则它只用于为复核排序。若不存在安全的核心题，把 relevance.enabled 设为 false，并在 relevance.note 中说明原因。
协议内容全部用英文书写——题干、选择条件、facet、handoff 任务与场景描述；在 criterion_refs 与决定记录中原样保留用户的原文。
从 02 的 citation 池与实际标准中挑选试读场景（明确相关、明确无关、边界、信息缺失、多臂、角色互换、联合用药方案），把期望回答与期望结论写入 screening_review；在判读四值时特别注意 miss 与 unclear 之间、partial 与 unclear 之间的分界。期望答案与期望结论都由你自己撰写，试读只检验内部一致性；综述自带标注纳入集时，池中含有的每条标注记录都必须作为 retained 场景出现，真正检验草稿的是下面的校准读数。
草稿不能只凭结构就冻结。工作区带有标注纳入集（`labels/included.txt`，每行一个标识：pmid:、doi:、title:，或来源自带 id）时，调用 panner_compile_calibration 并传入运行配置：它用判题模型对标注记录逐条判题，报告规则会排除的每一条。只要有一条标注纳入记录被排除，或当前草稿还没有校准报告，冻结即被拒绝。标签在池中找不到时记为 unmapped，那是 02 的召回问题而非协议缺陷：向用户报告，不要改协议去迁就。
调用 panner_compile_preview：解决每一个 issue 与试读不符项——澄清未解决或试读与期望不符时草稿保持草稿——并把信号、规则、标准覆盖、试读结果与每一条警告完整展示给用户。预览会在比较规则带按组否决、或 arms/any disqualifier 排除整条记录时给出警告；警告不是结构错误，但它是冻结前必须回答的问题。
用户确认协议准确表达了标准后，调用 panner_compile_freeze 并传入确认原话。冻结后，04 只读取该协议执行。
```

→ 下一环节：[04-screening.md](04-screening.md)（回答原子信号并执行组合规则）。
