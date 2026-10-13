# 真实 LLM 全流程演示报告

> 运行环境：OpenRouter 免费模型 `nvidia/nemotron-3-super-120b-a12b:free`（主模型，备用 `google/gemma-4-31b-it:free`、`openrouter/free`）。
> 本报告的每个数字都标注了产生它的命令或查询。数字来自 `scripts/live_demo.sh` 的一次完整运行（2026-10-12，UTC 04:0x–04:31）。
> 密钥只存在于本地 `.env`，本报告与录制文件中均没有密钥。

## 一、怎么运行

```bash
bash scripts/live_demo.sh
```

脚本依次做四件事：

1. 重置独立数据库 `sourcehero_live` 并迁移（`scripts/db_ensure_e2e.py`、`alembic upgrade head`）。
2. 用真实模型灌入演示语料（`python -m app.demo.seed`，含排空队列）。所有回答录制到 `evals/cassettes/live_demo.jsonl`（已提交），每日请求预算为 300（`LLM_DAILY_REQUEST_BUDGET=300`）。
3. 用 `scripts/live_demo_probe.py` 在进程内调用 `/v1/ask` 三次，结果写入 `.dev/logs/live-demo.json`。
4. 用 fake 提供者把同一语料灌入 `sourcehero_live_fake` 作为对比（不联网）。

运行中出现过两个问题，都已修复并在下文说明：探针的导入路径错误（脚本改为 `PYTHONPATH=.` 后重新运行探针，灌库阶段未重跑）；Ask 流出现空句子（见第五节）。

## 二、请求与耗时

| 指标 | 数值 | 来源 |
|---|---|---|
| 真实 HTTP 请求（本次演示） | 41 | OpenRouter 账户的 `used` 计数：运行前 65，运行后 106 |
| 录制的成功应答（cassette 行数） | 41 | `wc -l evals/cassettes/live_demo.jsonl`（灌库 38 + 探针 3） |
| 灌库耗时 | 1240 s | `scripts/live_demo.sh` 输出 `live: seed finished in 1240s` |
| `llm_calls` 行（ok / error） | 26 / 8 | `live_report.py` 键 `llm_calls_by_status` |
| 成功调用的 token（prompt / completion） | 29 476 / 45 382 | 键 `llm_ok_tokens` |
| 成功调用的平均延迟 | 24.6 s | 键 `llm_ok_tokens`（`mean_latency_ms`） |
| 预算拒绝 | 0 | 键 `llm_calls_by_status`（无 `budget` 行） |
| 429 与额度耗尽 | 0 | 键 `llm_failures`（无 `quota` 行） |

说明：`llm_calls` 每次 `complete()` 记一行，而真实 HTTP 请求有 41 次，二者的差额来自结构化输出失败后的修复轮（同一行内的第二次请求）。`llm_calls` 的 26 行 ok 中包含探针的两次运行（每次 3 行），第二次运行完全从录制文件回放，没有产生真实请求。

## 三、主张（claims）

| 指标 | 数值 | 来源 |
|---|---|---|
| 主张总数 | 94 | 键 `claims_created_by_extract_jobs` |
| 由 LLM 抽取 | 49（约 52%） | 键 `claims_by_extractor`，`extractor = llm(openai_chat)` |
| 启发式（LLM 回退） | 45 | 键 `claims_by_extractor`，`extractor = heuristic` |
| 段落的 LLM 抽取结果 | 9 段成功，7 段回退 | 键 `llm_calls_by_task_and_status`（`claim_extract`：ok 9、error 7） |
| 引文对齐 | 0 条被拒绝（`rejected_unaligned` 合计 0） | 键 `quotes_rejected_unaligned` |

## 四、关系、冲突与 wiki

| 指标 | 数值 | 来源 |
|---|---|---|
| 关系总数 | 27 | 键 `relations_by_method_and_label` |
| `method = llm` 的关系 | 12（supports 7、contradicts 3、refines 2） | 同上 |
| `method = heuristic` 的关系 | 15 | 同上 |
| 冲突（contradicts） | 10（启发式 7、LLM 3） | 键 `contradictions` |
| wiki 页面 | 3，状态均为 clean | 键 `wiki_pages_by_status_and_compile_method` |
| 其中由模型编译 | 2（`llm:nvidia/nemotron-3-super-120b-a12b:free`） | 同上 |
| 其中启发式回退 | 1（模型的第二次回答未通过 `WikiBody` 校验，`sections.2`） | 同上；键 `llm_failures` 与 `wiki_compile` error 行 |

## 五、Ask 示例（逐句判定）

来自 `scripts/live_demo_probe.py`，原始结果在 `.dev/logs/live-demo.json`；每次流都以 `done` 结束，`method = llm(openai_chat)`，没有 `error` 事件。

1. **What is the energy density of the HX-2?**（检索 8 条证据）
   - supported：Halcyon Cells states that the HX-2 cell delivers an energy density of 450 Wh/kg.
   - supported：Independent laboratory testing measured the HX-2 energy density at 380 Wh/kg.
2. **How many tokens was Orchid-7 trained on?**
   - supported：Orchid-7 was trained on 2 trillion tokens.
3. **What launch price did the HX-2 pack have?**
   - disputed：Halcyon Cells expects the HX-2 pack price to be $120/kWh at launch.
   - contradicted：However, Meridian Equity Research projects that first-year HX-2 pack prices will be above $150/kWh.

三个问题的回答都在录制文件中；探针第二次运行没有产生新的真实请求。

**空句子缺陷（已修复）**：模型把引用写在句号之后（`… tokens. [1, 2]`），切句器把引用当作独立片段，去掉引用后为空，流中因此出现 `unsupported` 的空句子。修复：`app/verify/answer.py` 的 `_emit_new_sentences` 跳过清理后为空的片段；回归测试 `tests/test_ask.py::test_ask_never_emits_an_empty_sentence_for_trailing_whitespace`（修复前失败：`['…', '…', '']`）。修复后，探针的三个流中没有空句子。

## 六、失败与回退

`llm_calls` 中的错误共 8 行，按消息统计（键 `llm_failures`）：

- `empty response`：5 行（模型返回的内容为空，推理耗尽了输出预算）；
- `response is not a JSON object`：主张抽取的回答被截断或夹杂说明文字，计 2 行；
- `WikiBody` 校验失败：1 行，对应一页回退为启发式。

回退的结果：7 段落回退为启发式抽取（见第三节）；1 页 wiki 回退为启发式编译（见第四节）。Ask 没有回退。

## 七、与 fake 模式的对比

同一语料，fake 提供者，`sourcehero_live_fake`（命令：`uv run python scripts/live_report.py --db-url <sourcehero_live_fake> --cassette none.jsonl --probe none.json`）：

| 指标 | 真实模型（live） | fake |
|---|---|---|
| 主张总数（键 `claims_created_by_extract_jobs`） | 94（LLM 49 + 启发式 45） | 85（全部 `llm(fake)`） |
| 关系总数 | 27（LLM 12 + 启发式 15） | 21（全部启发式） |
| 冲突（contradicts） | 10 | 7 |
| wiki 页面 | 3（LLM 2 + 启发式 1） | 3（全部启发式） |
| LLM 调用行（`llm_calls`） | 34（ok 26、error 8） | 24（ok） |

fake 的结果是确定性的，不代表模型质量；它的意义在于说明：同一代码路径在真实模型下会产出 LLM 来源的主张、关系与 wiki 页面，而这些在 fake 中不会出现。

## 八、局限

- 推理模型会先消耗输出预算；claim_extract、verify、relation_judge、wiki_compile 的输出预算已分别提高到 4000、6000、6000、6000。
- 实时 wiki 用例仍为 skip，回放为 strict xfail。本次演示的两页 LLM 编译 wiki 证明输出预算的修改有效，但它不替代实时检查。
- relation_judge 在 dev 与 test 上未达到目标，本次演示的关系判定带有同样的局限。
- 本报告只描述一次运行；同一脚本重跑时，录制文件中已有的回答会被回放，新的回答会产生真实请求。
