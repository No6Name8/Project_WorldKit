# Evidence for judges

## In one minute

1. WorldKit turns a phone video of an object next to a coin into a textured 3D model and a print STL in millimetres, with the scale estimated from the coin.
2. Real mug against a ruler, two independent runs: mean error 2.5% and 4.6%; worst single dimension 6.0% ([accuracy.md](accuracy.md)).
3. A second object (a Hydro Flask bottle) could not be scored: SAM 3.1 found the coin in 1 of 12 frames. This is recorded, not hidden.
4. The AI Edit agent: Muse Wonder picks a geometry tool from plain language, SAM 3.1 supplies the part, the tool edits the mesh, geometry checks and Muse Spark verify, then accept, retry once or refuse ([AGENTIC.md](AGENTIC.md)).
5. 10 of 10 rehearsed edits with the real models were accepted on the first attempt, 13.9-33.6 s each; traces in [agent_traces/](agent_traces/).
6. Meta models used: Muse Spark, Muse Wonder, SAM 3.1 (Meta API); SAM 3D Objects and VGGT (open weights on a Modal A100); Muse Spark Open (open weights on Baseten, verification only) ([MODELS.md](MODELS.md)).
7. Muse Spark Open on Baseten gave the same accept decision as Muse Spark on all 5 rehearsed edits ([results/spark_open_agreement.json](results/spark_open_agreement.json)).
8. Live site with a phone upload, a laptop worker and a viewer: https://web-flame-psi-45.vercel.app (one real upload-to-result run: 715 s, [results/e2e_web_real.json](results/e2e_web_real.json)).
9. Every accuracy number has a JSON file with the command and SHA-256 of its input and STL; `python scripts/verify_claims.py` rechecks them without any API call.
10. Demo video (3 min): https://www.loom.com/share/55fd3cf835994a6a99e1fee8fc2b907d

## Verify in 2 minutes

```bash
pipeline/.venv/Scripts/python.exe scripts/verify_claims.py
```

It recomputes every error % in `docs/results`, regenerates `docs/accuracy.md` and compares it, checks the SHA-256 of every file a claim names (when present), checks the README numbers and the AI Edit tables against the traces, and checks every relative link. Then open the hosted demo: https://web-flame-psi-45.vercel.app/viewer/index.html?demo=1 (Source tab first: the video, the model built from it, the frames used; then Viewer, AI Edit replay, Accuracy).

## Hackathon criteria

| Criterion | Claim | Proof | Number | Verify in 2 minutes |
|---|---|---|---|---|
| Impact | A phone video and a coin give a printable STL in mm | [accuracy.md](accuracy.md), [results/object_mug_coin.json](results/object_mug_coin.json), [results/object_mug_coin_web_run.json](results/object_mug_coin_web_run.json) | mean error 2.5% and 4.6% over two runs on one mug | `python scripts/verify_claims.py` (accuracy lines) |
| Impact | A phone upload is processed end to end without the CLI | [results/e2e_web_real.json](results/e2e_web_real.json), [web/e2e_real_5_viewer.png](web/e2e_real_5_viewer.png) | 14 s upload, 715 s worker, result in the live viewer | open the JSON; the run's checks are listed in `checks` |
| Meta AI tooling | Six Meta models, each with a named job, file and function | [MODELS.md](MODELS.md) | 3 API models, 3 open-weight models | `grep -n "MUSE_SPARK_MODEL\|WONDER_MODEL\|SAM_MODEL\|SPARK_OPEN_MODEL" pipeline/worldkit/*.py` |
| Meta AI tooling | The agent is driven by Muse Wonder function calling and checked by Muse Spark | [WONDER.md](WONDER.md), [agent_traces/](agent_traces/) | 5 traces with tool calls, tokens and verdicts | open [agent_traces/edit_b.json](agent_traces/edit_b.json) |
| Technical execution | Tests at three levels | `pipeline/tests`, `viewer/tests`, `web/tests`, `scripts/test_all.ps1` | pytest 251, browser 223 checks in 10 suites, web 10 unit + 50 mocked end-to-end | `powershell -File scripts\test_all.ps1` (about 12 min) |
| Technical execution | Numbers are reproducible from files | [results/](results/), `scripts/make_results.py`, `scripts/accuracy_table.py --from-results` | 7 claim files with SHA-256 | `python scripts/verify_claims.py` |
| Demo | The original video sits next to the 3D model built from it | [ui/source_mug_1440.png](ui/source_mug_1440.png), [ui/source_desk_1440.png](ui/source_desk_1440.png) | 8 and 9 frames with SAM 3.1 outlines | https://web-flame-psi-45.vercel.app/viewer/index.html?demo=1 |
| Demo | AI Edit can be watched without an API key | `viewer/assets/demo/edit_replay/replay.json` | 5 recorded edits, labelled as a replay | the demo link, AI Edit tab |

## Prizes

| Prize | Claim | Proof | Number | Verify in 2 minutes |
|---|---|---|---|---|
| Best Agentic Use-Case (autonomy) | The agent chooses tool, arguments, part and amount, retries once, and refuses on its own | [AGENTIC.md](AGENTIC.md) "Where the agent decides alone", `pipeline/worldkit/edit.py` `run_edit` | 8 tools; "make the handle thinner" with no amount became -2 mm | [agent_traces/edit_c.json](agent_traces/edit_c.json) |
| Best Agentic Use-Case (effectiveness) | Rehearsed edits completed and verified | [DEMO_EDITS.md](DEMO_EDITS.md), [agent_traces/](agent_traces/) | 10 of 10 accepted on the first attempt; handle wall 5.22 → 7.34 mm and 5.22 → 3.16 mm | `python scripts/verify_claims.py` (traces line) |
| Best Agentic Use-Case (practical use) | Edits land in the print file in mm, with limits that keep it printable | [AGENTIC.md](AGENTIC.md) "Where it stops", [agent_traces/refusal_wall_floor.json](agent_traces/refusal_wall_floor.json) | 1.2 mm wall floor, 50% SAM coverage floor, undo to any version | open the refusal trace |
| Best Creative Build (Muse Wonder) | Muse Wonder edits a 3D print of a real object from plain language, through exact geometry tools | [WONDER.md](WONDER.md) | 5 instructions, 5 different tools and arguments, 4,223-4,463 Wonder tokens each | the before/after renders in [WONDER.md](WONDER.md) |
| Best Build on Meta Open Weight Models | Muse Spark Open, served by Baseten, verifies AI Edit results (`--spark-backend baseten`) | [MODELS.md](MODELS.md), [results/spark_open_agreement.json](results/spark_open_agreement.json), `pipeline/worldkit/spark_open.py` | same decision as Muse Spark on 5 of 5 edits; one needed a second call after the first returned nothing within 6,000 tokens | `python -m pytest pipeline/tests/test_spark_open.py` (fake client), then the JSON |

The open-weight claim covers the verification step only, on stored renders of edits Muse Spark had accepted. Tool choice stays on Muse Wonder (Meta API). SAM 3D Objects and VGGT are also open-weight Meta models, run on a Modal A100.

## Known limits

- **One scored object.** The ruler test is one mug. The second object, a Hydro Flask bottle, was not scored because SAM 3.1 found the coin in 1 of 12 frames ([results/object_hydroflask_coin.json](results/object_hydroflask_coin.json)).
- **Run-to-run variation.** Each run builds a new SAM 3D mesh. The two mug runs differ by up to 4.1% (height); run 2 has a 6.0% height error.
- **Part of run 1 is tuned by hindsight.** The taper fit was added after seeing run 1's base error; run 2 used the same code afterwards.
- **Scenes are layout, not metrology.** Map mode places several objects at one scale; its object sizes are off by up to 22.6% with a card ([accuracy.md](accuracy.md) tables b-e).
- **Geometry is generated.** SAM 3D Objects generates the mesh from one frame; hidden sides are not measured. Parts are estimated: the handle is a vote of SAM 3.1 masks onto the mesh (11 of 12 frames).
- **Replay vs live editing.** The hosted viewer replays recorded edits; live editing needs `python -m worldkit.serve` and a Meta API key.
- **Spark Open comparison covers accepted edits only.** No rejected edit was compared.
