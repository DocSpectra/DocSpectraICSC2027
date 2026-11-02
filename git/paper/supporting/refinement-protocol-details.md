# Refinement protocol details retained outside the main paper

The main application now leads with question selection, LT/R0 instructions and a compact evaluation/outcome account. These notes retain procedural details removed for space. They describe the run as executed; they do not change evidence or turn exploratory analyses into confirmatory results.

## Question selection

The fifteen complex tasks comprise five earlier pilot tasks, two tasks from the September panel, and eight new source-grounded model proposals. The existing seven covered seven areas. Eight previously uncovered areas each supplied two candidates; all sixteen passed protocol gates, and one per area was selected by cluster rank. Source checks verified all 272 quotes in the sixteen new candidates. The fifteen selected tasks initially had 75 proposed core requirements; author review accepted 63, revised 10 and rejected two, leaving 73. This review does not validate generated answers.

The FAQ freeze retained 111 of 142 questions after excluding 31 without LLM-verified baseline support. Three duplicate-text clusters were recorded. Both sets were frozen before refinement and withheld from the editor. Later whole-page support rejudgment rebuilt the primary common retrieval set to 105 questions; FAQ answer scoring retained all 111. The source and question populations are separate from the earlier automatic fixed-five-page support labels.

Sources: [task freeze](../../context/evidence/wc-tier1-tasks-v1-2026-10-14.json), [HR-19 verdicts](../../context/evidence/wc-tier1-review-hr19-2026-10-15.json), [FAQ freeze](../../context/evidence/wc-tier2-freeze-v1-2026-10-14.json), [freeze handoff](../../context/handoffs/2026-10-14-wc-freeze-survey-questions.md), [whole-page rejudgment](../../context/evidence/wc-rejudge-19-result-v1-2026-10-24.json).

## Execution and admission

Study-specific drivers orchestrate the containerized editor and host answer agent. The latter reuses RetrievalArena's model-turn contract. RetrievalArena supplies support verification and RAGAS evaluation; study-specific retrieval scores were cross-checked against its ranked scorer on shared inputs. The entire editor/answer workflow is not claimed to be orchestrated by RA.

The corpus scope is Docker `docs_scope_v1`, 1,218 pages from `274681c4`, with the historical 844-page LWCC. One refinement realization per arm proceeds through 25 chained areas and reconciliation. The final admissions are LT 18 and R0 23, with both reconciliations admitted. O is original; S is a byte-identical replay. The editor sees neither evaluation questions nor spectra.

Admission retains mapped baseline pages, constrains connectivity loss, requires incoming links for new pages, preserves protected content and rejects newly broken targets. The applied resolver follows Hugo routes. LT may move content into new pages. The connectivity scoring graph includes both new pages and formerly disconnected existing pages linked in by edits. The initial implementation already included new pages but omitted those newly connected existing pages. Where a new page was reached only through an omitted page, the induced scoring graph was disconnected and its connectivity score was zero; all LT admissions depended on including these connecting pages. This is an evaluation-set defect, not an expected failure of LT or a prohibition on content relocation. Protected-line matching tolerates prompts, whitespace and a repaired code fence. Pre-edit prompt limits were reconciled; the run paused before reconciliation for rule review. Network/authentication failures before editing were voided. One LT area received a fresh pass after implementation defects.

After initial results were reviewed, two LT areas received another editing pass. Both final versions met the original incoming-link requirement. The main text retains this disclosure and Table VII retains completed content-review timing/blinding. These additional passes are not merely rescoring. The exclusion sensitivity includes those areas and the implementation-defect rerun. Exact chronology, identities, rules and receipts remain in the [deviations register](../../context/docs/manuscript-deviations-wc-refinement.md).

Five graph nulls per family and arm start from mapped baseline O′. RN matches edit counts and reciprocity; LN adds locality; PC also matches new-page counts and their one-way incoming-link counts, using random local origins. Nulls are graph counterfactuals, not controls for document semantics.

## Answer and judgment configuration

The gpt-6-luna answerer searches, reads and follows links. Complex tasks cross one/six searches with following off/on and two answer replicates; FAQs use one search, following off/on and one replicate. Read/call budgets and prompts match. Eleven attempts were archived and retried, seven for model behavior, conditioning the retained sample. Of 480 complex-task attempts, 479 produced scored answers; one terminal failure remains unscored.

Luna self-grades five 1–5 measures with two grading passes; qwen3:14b supplies a secondary requirement rubric. Luna has no fixed snapshot and was selected using five of these tasks. FAQ FactualCorrectness uses Claude Haiku 5.5 through RAGAS and historical FAQ references. Faithfulness and ContextRecall remain descriptive after failed repeatability checks. Main Table VII retains qualification outcomes and author checks; no independent human answer-quality validation is claimed.

Page-support judgments assess whether a page supplies evidence for a question. Candidate pools combine passage BM25, passage MiniLM and the top ten from each evaluated run, verified by Haiku. Whole-page rejudgment of undetermined pairs addresses support split across windows. Sensitivities use the earlier common set, count undetermined pages as hits, bracket questions losing support, and credit moved/derived content (O1).

Paired 95% intervals use 10,000 question-cluster bootstrap draws, grouping duplicate texts and averaging conditions/replicates within questions. They are descriptive and lack multiplicity correction. All original structural and outcome tables remain in the manuscript without numerical changes. Authority: [whole-corpus analysis](../../context/reports/2026-10-24-whole-corpus-refinement-report.md), [analysis evidence](../../context/evidence/wc-analysis-20-v1-2026-10-24.json), and the [deviations register](../../context/docs/manuscript-deviations-wc-refinement.md).
