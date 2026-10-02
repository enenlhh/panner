# 02 建立 citation 池（citation pool）

> 本环节只完成一件事：建立可筛选的 citation 池。agent 先把不固定的检索来源整理成统一、不可变的 `citation.jsonl`；固定去重工具再从中生成派生的 `citation-pool.jsonl`。
> 来源结构由宿主 agent 理解并映射；统一结构、去重规则、对账和写入契约由 Panner 固定。
> 输入：用户的一份或多份检索导出。产物：确认的 `citations/vN/citation.jsonl` 与去重输出目录（`citation-pool.jsonl`、`dedup-review.jsonl`、`dedup-manifest.json`）。

## 职责与判断逻辑

### agent 如何处理来源

用户提供一份或多份检索导出或其他 citation 来源。agent 逐份检查实际内容，确定**一条 citation 的边界**、字段位置、重复字段和编码方式，再提出到 `citation.jsonl` 的对应关系。扩展名和数据库名称只能提供线索，不能替代对实际结构的检查。

agent 可以调用宿主已有的解析器、文件工具或脚本处理整份来源。它负责选择和核对转换方法；批量 citations 不逐条塞进对话上下文。遇到无法可靠识别的记录边界、字段含义或内容时，agent 展示具体疑点，请用户提供说明或更合适的导出。Panner 在此阶段不预设一份封闭的文件格式清单，也不要求用户先把文件改造成某种固定格式。

转换前，agent 给用户看**来源与字段计划**：有哪些文件、各文件记录单元如何识别、哪些来源字段进入目标字段、哪些信息只能留在原件，以及少量代表性样本。转换后，agent 对整份输入核对每个来源的记录单元数、成功写入数、失败数及主要字段非空数；无法独立核对的计数明确标出。来源中有摘要而目标 `abstract` 全空，属于映射问题，不能解释成"这些 citations 没有摘要"。

agent 将原始来源放在待确认的 `citation.jsonl` 同目录下的 `sources/` 中，`source.file` 使用该目录内的相对路径。确认工具按字节保存原件与 `citation.jsonl`，将映射和对账决定写入 manifest。每行都能回到来源文件和该文件中的 citation 位置；来源自带 ID 只用于回查。缺少摘要或部分书目信息的 citation 仍进入文件，缺失保持为空，不由 agent 猜填。用户确认来源与字段对账后，该版 `citation.jsonl` 固定为只读输入；调整来源或映射要形成新版本。

### 去重规则：BibDedupe 的确定结果与待审结果

同一出版物可能从几个数据库导出多次；这些导入条目全部保留在 `citation.jsonl`，去重结果仅写在 `citation-pool.jsonl`。同一研究的不同出版物各自进入筛选。[BibDedupe 的书目重复定义](https://joss.theoj.org/papers/10.21105/joss.06318.pdf)与此一致。

Panner 使用固定版本的 [BibDedupe Python 库](https://github.com/CoLRev-Environment/bib-dedupe)作为本环节的**唯一候选发现和配对分类引擎**。初始版本定为 `0.11.0`；升级版本须重新核对同一批 citations 的分组变化。固定工具负责把 `citation.jsonl` 字段交给该库并核对结果；Panner 的发行包负责提供运行它所需的 Python 环境与依赖，用户只安装 Panner。BibDedupe 的 [论文](https://joss.theoj.org/papers/10.21105/joss.06318.pdf)和[公开接口](https://colrev-environment.github.io/bib-dedupe/)分别说明了其书目匹配方法与 `prep`、`block`、`match`、`cluster` 等调用。

| BibDedupe 输入 | `citation.jsonl` 字段 |
|---|---|
| `ID` | `id`；保留 Panner 分配的 citation 身份 |
| `title` / `author` / `year` | `title` / `authors` / `year` |
| `journal` / `booktitle` / `container_title` | `entry_type=article` 时用 `journal` 或 `container_title`；会议录、书籍章节等类型按其 `container_title` 填入 `booktitle`；同时传入通用 `container_title` 供其他书目类型使用 |
| `volume` / `number` / `pages` | `volume` / `issue` / `pages` |
| `abstract` / `doi` | `abstract` / `doi` |
| `ENTRYTYPE` | `entry_type`；为空时固定用 `article`，并在 plan 中报告默认数量 |
| `search_set` | 固定为空；来源身份仍由 `source.file` 保存，不交给这个字段 |

BibDedupe 的 `block` 会排除非空 `search_set` 相同的配对；若将 `source.file` 映射进去，同一来源内的重复 citation 就不会被比较。[BibDedupe 的 blocking 源码](https://github.com/CoLRev-Environment/bib-dedupe/blob/main/bib_dedupe/block.py)可核对这一行为。因此 Panner 保留来源溯源，同时让同文件和跨文件的 citations 都参加匹配。

固定处理顺序是：`prep` 整理书目字段，`block` 找候选配对，`match` 按 BibDedupe 自带条件标为 `duplicate` 或 `maybe`，再仅对 `duplicate` 配对调用 `cluster` 形成候选重复组。[BibDedupe 的 API 示例](https://colrev-environment.github.io/bib-dedupe/)与[匹配源代码](https://github.com/CoLRev-Environment/bib-dedupe/blob/main/bib_dedupe/match.py)定义这些类别与内部条件；Panner 不另定 DOI、作者或标题的相似度门槛。以上调用与结果核对见 [dedup.py 的 `_pairs_and_clusters`](../src/panner_mcp/dedup.py)；字段映射（含 `search_set` 置空）见 [`_bib_rows`](../src/panner_mcp/dedup.py)。

**自动合并同时要求库的 `duplicate` 类别与组内标识符一致**：

| BibDedupe 结果 | Panner 动作 |
|---|---|
| 配对标为 `duplicate` | 仅用这些配对形成候选重复组；组内无相互矛盾的非空 DOI 或 PMID 时自动合并，其他成员的 `duplicate_of` 直接指向进入筛选者 |
| 配对标为 `maybe` | 形成待裁决关系；用户确认前，不因该配对合并两个筛选单位 |
| 未形成上述配对 | citation 独立进入筛选 |

若 BibDedupe 的 `duplicate` 配对连通组内部同时存在标为 `maybe` 的成员配对，或该连通组内有两个不同的非空 DOI / PMID，整个连通组保持分开并交用户裁决。两个连通组之间的 `maybe` 配对只形成待裁决关系，不阻止各组内部安全的自动合并。标识符先去除常见 `doi.org/`、`doi:`、`PMID:` 写法及大小写差异，再比较是否一致（见 [`_identifier_conflicts`](../src/panner_mcp/dedup.py)）。这是自动合并的身份一致性校验，不修改 BibDedupe 的配对条件；该库确实可能把 DOI 不同而题名、作者相同的两条记录标为 `duplicate`。

选择进入筛选的原始 citation 时，有非空摘要的优先；否则按本环节确认的来源导入顺序（见 [`_representative`](../src/panner_mcp/dedup.py)）。选择只决定默认筛选视图，不改写任何成员的题名或摘要。用户可以对一个候选组给出最终分区，也可以拆开自动组。例如三条中 A、B 同一出版物而 C 独立：

```json
{"group":"G000001","partitions":[{"members":["C000001","C000002"],"screen_id":"C000001"},{"members":["C000003"],"screen_id":"C000003"}],"reason":"A、B 指向同一出版物；C 为独立报告"}
```

每个组成员必须恰好出现在一个分区，每个 `screen_id` 必须属于该分区（校验见 [`_resolution_partitions`](../src/panner_mcp/dedup.py)）。每个分区的代表 citation、用户决定及理由随组保存。未裁决的 `maybe` 始终保持分开。派生与对账（组、`duplicate_of` 直接指向、计数不变量）见 [`_derive`](../src/panner_mcp/dedup.py)；去重的预览与确认两段调用见 [`deduplicate`](../src/panner_mcp/dedup.py)。

BibDedupe 的版本、Panner 去重策略版本、输入字段覆盖、原始配对及 `duplicate` / `maybe` 类别进入池化溯源。相同 DOI 本身不构成 Panner 的自动合并规则；它只是 BibDedupe 计算配对时可能使用的一个字段。[BibDedupe 的方法说明](https://joss.theoj.org/papers/10.21105/joss.06318.pdf)明确区分了相同标识符和同一出版物。

第三方发表的表现是选用该库的依据，不构成 Panner 在所有来源上的误并保证。正式自动处理前，须以 Panner 的真实导出和经人工核对的重复关系检验这一固定版本；每次运行保留足以回看和纠正的配对结果。

去重改变的是**需要筛选几条 citation**，不作纳入排除判断。筛选前重复数等于 `citation-pool.jsonl` 中 `duplicate_of` 非空的行数；后续筛选只读取该文件中 `duplicate_of` 为空的行。重复计数与题名摘要筛选后的排除计数分开，符合 [PRISMA 2020 流程图](https://www.prisma-statement.org/prisma-2020-flow-diagram)。每个重复指向须指向现存且自身进入筛选的 citation，不能形成链或环；所有导入 citation 必须恰好属于一个最终分区。

### 完成条件与交接

1. 每个可识别的来源 citation 单元在不可变的 `citation.jsonl` 中恰有一行，来源位置可回查；解析失败和无法解释的数量差异已解决。来源没有独立计数依据时，明确披露这一验证限度。
2. 固定工具读取该文件且不改写它；`citation-pool.jsonl` 保留全部输入 ID 与原有字段，每个 ID 恰好出现一次。用户看到 BibDedupe 的版本、自动结果和待裁决组。
3. `duplicate_of` 仅由该版本 BibDedupe 的 `duplicate` 结果经身份一致性校验后，或用户明确裁决产生，并构成有效的直接对应关系；`maybe` 及标识符冲突默认留开。待筛数 + 重复数 = 导入 citations 总数。
4. 用户确认的池版本有独立的输出目录与 manifest。来源或映射改变时重建 `citation.jsonl` 版本，再从该版本重算池；只修改裁决时重算派生池，不重写 citation 输入。

## 数据契约

`citation.jsonl` **每个导入的 citation 一行**，只承载 citation 内容与来源，不承载去重结论。字段含义不随来源格式变化。以下是一条示意记录：

```json
{"id":"C000001","source":{"file":"sources/pubmed.nbib","position":1,"id":"12345678"},"title":"Example title","abstract":"Example abstract","authors":"A. Author; B. Author","year":2024,"journal":"Example Journal","container_title":"","volume":"","issue":"","pages":"","publication_types":[],"doi":"","pmid":"12345678","entry_type":""}
```

| 字段 | 含义 |
|---|---|
| `id` | Panner 为每条导入 citation 分配的稳定身份；按确认的文件顺序和文件内顺序编号 |
| `source.file` / `source.position` / `source.id` | `sources/` 内原件的相对路径、其中从 1 开始的顺序位置、来源自带 ID；没有来源 ID 时用空字符串 |
| `title` / `abstract` | 题名与摘要原有内容；来源确实缺失时为空 |
| `authors` / `year` / `journal` / `container_title` / `volume` / `issue` / `pages` | 来源提供的书目信息；`year` 缺失时为 `null`，`container_title` 是期刊、会议录或书籍等载体的通用题名；有损变换须在映射中声明 |
| `publication_types` / `doi` / `pmid` | 来源提供的类型词与标识符；保留来源含义 |
| `entry_type` | 来源能明确识别的书目类型，如 `article`、`inproceedings`；不能识别时为空，去重工具按 BibDedupe 的 `article` 默认值处理并报告数量。它与 `publication_types` 含义不同 |

字段固定且每行都必须出现；缺少文本信息用空字符串，`publication_types` 缺失用空数组。`id` 按文件顺序及文件内位置连续编号为 `C000001`、`C000002` 等；同一来源的 citations 连续排列。来源格式变化由 agent 的映射处理，目标结构保持稳定。新增目标字段需说明后续哪个任务读取它，并对相关来源重新映射、对账及版本化。

确认后 workspace 生成版本目录 `citations/v0001/`：`citation.jsonl`（不可变副本）、`sources/`（原件快照）、`manifest.json`（版本、哈希、plan_hash、mapping_note、source_counts、字段覆盖、用户确认原话、来源清单）；workspace marker 记录 `active_citation`。去重确认后，marker 另记录 `active_pool`，供工作流和 04 核对该池确实派生自当前 citation 版本。

**去重输出目录**（用户指定的、独立于输入的新目录）包含三份产物：

| 文件 | 内容 |
|---|---|
| `citation-pool.jsonl` | 与输入行数相同、原有字段逐值相同，只增加 `duplicate_of`（重复指向，进入筛选者为 `null`）、`duplicate_evidence`（BibDedupe 版本与配对类别，或用户裁决）、`review_group`（待裁决组 ID，其余为 `null`） |
| `dedup-review.jsonl` | 全部 `maybe` 配对、组内分类冲突及标识符冲突，供 agent 向用户逐组解释；已裁决项保留原分类并标 resolved |
| `dedup-manifest.json` | 输入与输出哈希、BibDedupe 与策略版本、运行依赖版本、原始配对、用户裁决、数量对账 |

## MCP 工具与调用契约

Panner 提供 `build_citation_pool` MCP prompt 作为用户可选的启动入口，也提供 `panner_citation_guide` 工具，供普通对话中的宿主 agent 取得同一工作流指引。agent 完成全文件转换后，调用：

```text
panner_citation_preview(input_path, workspace_path, mapping_note, source_counts) → 结构错误、每来源字段覆盖、计数差异、plan_hash
panner_citation_confirm(input_path, workspace_path, mapping_note, source_counts, plan_hash, user_confirmation) → 已确认的 citation.jsonl 版本路径
panner_citation_status(workspace_path) → 当前版本及文件一致性检查
```

`mapping_note` 具体记录来源单元、字段对应及有损变换；`source_counts` 逐文件给出**独立于转换结果**的来源记录数，无法独立核对时用 `null`，并向用户说明这一限度。预览分别报告输入行数和有效 citation 数，检查每行结构、连续 ID、来源位置、原件是否存在、计数和题名、摘要、DOI、PMID 覆盖；它不解析任意原始格式，无法替 agent 判断字段映射是否准确。计数不符或结构有误时保持草稿状态。输入文件或原件改变时，旧 `plan_hash` 失效。

固定去重工具在 `citation.jsonl` 确认后调用，不修改脚本内容。工具核对该版本的 manifest 与原件快照，拒绝未经确认或已被改动的输入。每次调用只指定输入路径、**新的输出目录**和可选的用户裁决文件：

```text
panner_deduplicate_preview(input_path, output_dir, resolutions_path?) → plan
panner_deduplicate_confirm(input_path, output_dir, plan_hash, user_confirmation, resolutions_path?) → 结果
panner_deduplicate_status(workspace_path) → 当前活动池及来源一致性
```

不带 `confirm` 时工具只计算计划：输入 citation 数、自动重复组、待裁决组、最终待筛数、库与 Panner 去重策略版本、输入哈希及拟写入文件。用户确认后工具重新核对输入与计划，再向新建或空的 `output_dir` 写入；输出路径不能覆盖输入文件。用户后来修改裁决时，使用同一版 `citation.jsonl` 和**另一输出目录**重算，旧池与旧裁决仍可回查。整个流程的最新状态可随时用 `panner_workflow_status` 查询（见 [00](00-positioning.md) 的工作流编排）。

宿主可读资源：本环节数据契约的英文运行时正本在 `panner://contracts/citations`（17 字段清单、字段含义、去重判定表、三产物结构、对账不变量），一条完整 citation 样本在 `panner://examples/citation`。

| 文档描述 | 实现 |
|---|---|
| 统一结构校验：固定字段集、连续编号、来源连续排列、位置不重复 | [citations.py](../src/panner_mcp/citations.py)：`read_citations`（字段集见 `FIELDS`） |
| 预览对账、来源原件哈希、字段覆盖计数 | [citations.py](../src/panner_mcp/citations.py)：`preview_citations` |
| 确认落盘：不可变副本、sources 快照、manifest | [citations.py](../src/panner_mcp/citations.py)：`confirm_citations`、`citation_status` |
| 工具注册与加载入口 | [server.py](../src/panner_mcp/server.py)：`panner_citation_*` 工具、prompt `build_citation_pool` |
| 行为测试（对账、原件保留、坏行不丢弃） | [test_citation_pool.py](../tests/test_citation_pool.py) |

## 给宿主 agent 的工作流指令

宿主通过 prompt `build_citation_pool` 或 `panner_citation_guide` 工具取得同一段工作流指令。指令全文以 [server.py 的 `CITATION_GUIDANCE`](../src/panner_mcp/server.py) 英文正本为唯一运行依据；以下为逐句对照的中文翻译，供审阅。

```text
你正在执行 PANNER 的 02：建立 citation 池。使用用户的语言与用户交谈。
先逐份检查来源文件的实际结构：确定一条 citation 的边界、字段位置、编码及来源内的顺序。绝不仅凭文件扩展名猜测格式。
当记录边界、字段含义或内容无法可靠识别时，把具体疑点展示给用户，请其说明或提供更好的导出；不要临场拼凑转换。
向用户展示来源与字段的映射计划及代表性样本。保留原件；按来源核对原始记录单元数与转换后的 citation 数，并报告题名、摘要、DOI、PMID 的非空数；无法独立核对的计数标为未知，并向用户说明这一验证限度。绝不静默丢弃 citation，也绝不猜测填写缺失字段。
把原件放在 citation.jsonl 同目录的 sources/ 下；source.file 是该目录内的相对 POSIX 路径。按固定结构创建 citation.jsonl，每行一条 citation，按来源顺序编号。entry_type 是书目类型，无法识别时保持为空；publication_types 保存来源提供的出版类型词；两者含义不同。
调用 panner_citation_preview，先解决全部结构错误与对账差异再展示结果；计数不符或结构无效时计划保持草稿。把计数与字段覆盖展示给用户。用户确认映射后，调用 panner_citation_confirm 取得不可变的 citation.jsonl 版本。
把确认版本传给 panner_deduplicate_preview：预览自动重复、待裁决组与拟写入的文件；用户确认后调用 panner_deduplicate_confirm，以同一 plan_hash 执行。maybe 配对、组内分类冲突及 DOI/PMID 矛盾保持待裁决；裁决文件改变时，重算到新的输出目录。
绝不修改去重逻辑，也不自行批量判定重复关系；重复关系由固定引擎与用户的裁决产生。
后续筛选只读取确认的 citation-pool.jsonl 中 duplicate_of 为空的行；进入 03 之前，把计数（导入数、重复数、待筛数、待裁决组数）与待裁决组展示给用户。
```

→ 下一环节：[03-compile.md](03-compile.md)（把已确立的标准编译成判定规格）。
