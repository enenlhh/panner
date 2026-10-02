# 01 获取并确认初始资格标准

> 本环节只完成一件事：由宿主 agent 与用户形成一份**准确记录当前研究意图**的资格标准文本。用户可以提供已有标准，也可以从综述问题开始建立初稿。
> 输入：用户的已有标准文件、口述或对话。产物：经用户确认其表述准确的 `criteria/vN/eligibility-criteria.md` 及 manifest。01 的确认是对**当前文字**的确认；03 在编译时针对具体缺口继续与用户澄清，并在需要时形成新版本。

## 职责与判断逻辑

### 两种入口

**用户已有标准。** 用户可以提供方案、综述原文、文件或直接叙述。agent 保留原始材料及出处，整理成便于引用的工作文本，并核对整理是否忠实。已有标准不因格式、语言或写法不同而被补成新的限制。只有原文互相矛盾、关键术语无法准确记录，或用户给出的文件缺少必要部分时，agent 在 01 请用户澄清；为生成题名摘要信号和组合规则才需要的细节，交给 03 在编译时针对性询问。

**用户尚未建立标准。** agent 先请用户用自己的话说明综述目标、希望评估的研究，以及目前已经想到的纳入与排除边界。随后分轮帮助用户把这些想法写成文字；一次只问少量直接关系到**当前标准是什么**的问题，不预设每项综述都要设置同一套限制。干预性综述通常会涉及人群、干预、对照与研究设计；其他类型按其研究问题选择适用维度。[Cochrane Handbook 第 3 章](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-03)说明了这些资格维度，也区分综述关心的结局与真正决定资格的结局要求。[PRISMA-P 清单](https://www.prisma-statement.org/s/PRISMA-P-checklist.pdf)区分研究特征和报告特征。

agent 不将未讨论的维度写成"无限制"，也不从常见做法或检索结果补出条款。用户明确决定某项不设限制时，按原意记录。若用户目前尚不能决定某个边界，agent 保留其原句并将问题列入待澄清清单；03 会在编译到相关信号或规则时，说明**缺少哪项决定、为什么影响结果**，再向用户追问。

### 完成条件

1. 综述目标及用户目前能够确定的资格标准已有实质内容，来源材料与 agent 整理出的文字可相互回查。
2. 已经发现但尚未决定的问题如实列出；它们可以交给 03，不能伪装成已决定或默认"不限制"。
3. agent 展示**将保存的全文和待澄清清单**，用户确认它们准确反映当前研究意图。

如果连综述目标或拟评估什么类型的研究都还无法表达，agent 继续帮助用户形成初稿。01 不把文本确认等同于标准已足够详细、可以直接自动筛选；可执行协议须在 03 另行编译和确认。

### 01 与 03 的交接

01 的多轮对话解决**如何忠实记录用户当前的资格标准**。03 的多轮对话解决**怎样将该标准编译成题名摘要信号和组合规则**。因此 01 无需预先穷尽混合人群、多研究组、比较方向、联合措施等所有可能出现的边界；03 只有在这些细节会改变某条信号的回答或组合规则时才问。编译所需的判断单位、量词、组间关系及限制范围由 [03](03-compile.md#编译时的资格语义澄清) 按实际标准逐项检查。

03 的澄清若只是确认原句本来的意思，可连同原句记录在编译审阅材料中；若用户的回答**增加或修改了资格限制**，先把修改后的全文交用户确认，形成新的 `eligibility-criteria.md` 版本，再冻结相应协议。对话仍在 03 的编译过程中继续，不要求用户重新开始 01。

## 数据契约

`eligibility-criteria.md` 必须包含实际内容的 `# Review objective` 与 `# Eligibility criteria` 两节；下层标题由用户标准决定。示意：

```markdown
# Review objective
比较目标治疗与对照治疗在指定人群中的效果。

# Eligibility criteria
## Inclusion
- 目标人群：……
- 干预与对照：……
- 研究设计：……

## Exclusion
- ……（用户已明确决定时）
```

确认后 workspace 生成版本目录 `criteria/v0001/`：`eligibility-criteria.md`（按字节保存的原文）、`manifest.json`（版本、哈希、plan_hash、上一版本、origin_note、用户确认原话、unresolved_questions、来源快照）、`sources/`（用户提供的原始文件副本）。修改标准生成 `v0002` 等新版本，旧版保留；workspace marker 记录 `active_eligibility_criteria`。`unresolved_questions` 允许非空，随版本保存并交给 03。

## MCP 工具与调用契约

Panner 提供 `establish_eligibility_criteria` MCP prompt 作为用户可选的启动入口，也提供 `panner_eligibility_guide` 工具，使普通对话中的宿主 agent 能取得同一任务指引。prompt 的展示与加载取决于宿主；Panner 工具负责文本结构、来源和版本检查，无法独立证明 agent 已准确理解用户。[MCP Python SDK 的 prompt 说明](https://py.sdk.modelcontextprotocol.io/servers/prompts/)区分用户选择的 prompt 与模型调用的工具。

```text
panner_eligibility_guide() → 初始标准访谈指引
panner_eligibility_preview(workspace_path, markdown, origin_note, source_paths, unresolved_questions) → 全文、待澄清清单和 plan_hash
panner_eligibility_confirm(workspace_path, markdown, origin_note, source_paths, unresolved_questions, plan_hash, user_confirmation) → 已确认的文本版本
panner_eligibility_status(workspace_path) → 当前版本及待澄清清单
```

`workspace_path` 必须是绝对路径，指向新目录、空目录或已有 Panner workspace。预览不写文件，返回将保存的全文及与全文、出处、原始文件和当前版本绑定的 `plan_hash`。宿主同时展示待澄清清单；用户确认这份记录准确后，确认工具按字节保存原件并生成版本。内容或来源变化后须重新预览，旧 `plan_hash` 失效。整个流程的最新状态可随时用 `panner_workflow_status` 查询（见 [00](00-positioning.md) 的工作流编排）。

宿主可读资源：本环节数据契约的英文运行时正本在 `panner://contracts/criteria`（两节结构、版本目录、manifest 字段、工具强制与行为规则的分工），编排总览在 `panner://workflow`。

| 文档描述 | 实现 |
|---|---|
| 预览、确认、状态三个工具的完整检查逻辑 | [criteria.py](../src/panner_mcp/criteria.py)：`preview_criteria`、`confirm_criteria`、`criteria_status` |
| workspace 合法性、版本目录与 marker 纪律 | [workspace.py](../src/panner_mcp/workspace.py)：`workspace_state` |
| 工具注册、输入输出结构与两个加载入口 | [server.py](../src/panner_mcp/server.py)：`panner_eligibility_*` 工具、prompt `establish_eligibility_criteria` |
| 行为测试（结构检查、版本保留、旧预览拒绝、marker 防篡改） | [test_eligibility_criteria.py](../tests/test_eligibility_criteria.py) |

## 给宿主 agent 的工作流指令

宿主通过 prompt `establish_eligibility_criteria` 或 `panner_eligibility_guide` 工具取得同一段工作流指令。指令全文以 [server.py 的 `GUIDANCE`](../src/panner_mcp/server.py) 英文正本为唯一运行依据；以下为逐句对照的中文翻译，供审阅。

```text
你正在执行 PANNER 的 01：与用户共同确立一项系统综述的资格标准。
使用用户的语言与用户交谈。
先询问用户是否已有资格标准。已有则接收它们，并忠实记录原文措辞及其出处；尚无则从综述目标出发，分轮帮助用户表达其目前已能确定的人群、干预或暴露、对照、研究设计及其他资格边界。
组织材料，使每一项被记录的决定都能回溯到原文或用户在对话中的明确表述。
只有用户的明确措辞定义标准：用户未讨论的维度保持不记录，而不是写成“无限制”；绝不从常见做法、检索结果或你自己的推断添加限制。
当所提供的材料自相矛盾、使用你无法准确记录的术语、或缺少综述需要的部分时，就在 01 询问用户；只影响信号与组合规则编译的更细细节属于 03。
只追问形成准确初始文本所必需的含混处；不要在此阶段强迫用户穷尽多组比较、联合用药方案等编译细节。
每轮更新工作草稿。把用户尚未决定的问题列入 unresolved_questions，供 03 在编译信号与组合规则时继续追问；绝不把未决定的事项写成“无限制”。
把用户认可的当前文本整理为包含 “# Review objective” 与 “# Eligibility criteria” 两节的完整 Markdown，记录原始材料及每项决定的出处。
调用 panner_eligibility_preview，把返回的全文与待澄清问题清单展示给用户。只有当用户确认它准确记录了**当前**标准后，才调用 panner_eligibility_confirm 并传入用户的确认原话。确认这份文本不表示标准已可直接自动筛选，也不表示所有编译细节已经解决。
```

→ 下一环节：[02-citation-pool.md](02-citation-pool.md)（建立 citation 池）。
