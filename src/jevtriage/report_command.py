from __future__ import annotations

import csv, json, random
from pathlib import Path

from .backends import JevBackend, MockBackend
from .cache import ResultCache
from .provenance import permits_accuracy_commitment, report_title
from .reporting import best_case_minimum, confidence_bins, evaluate, fit_size_limitation, fit_threshold, threshold_failure_message
from .sampling import stratified_sample


def _rows(path: str, text_column: str) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle: rows = list(csv.DictReader(handle))
    if not rows or not {"id", "label", text_column}.issubset(rows[0]): raise ValueError("labeled CSV needs id, text, and label columns")
    return rows


def _predict(rows, config, backend_name, args):
    cache = ResultCache(); uncached = [r for r in rows if cache.get(r[config.text_column], config.digest, backend_name, config.model) is None]
    from .cli import _estimate_tokens
    print(json.dumps({"rows":len(rows),"would_send":len(uncached),"estimated_input_tokens":_estimate_tokens(uncached, config.text_column, config),"estimate_note":"粗略估算，以返回的 usage 为准","backend":backend_name,"model_requested":config.model}, ensure_ascii=False))
    if backend_name == "jev" and uncached and not args.yes:
        answer=input(f"将向 Jev 发送 {len(uncached)} 条文本（每条一次请求）。继续？[y/N] ")
        if answer.lower() not in {"y","yes"}: raise RuntimeError("已取消")
    backend = MockBackend() if backend_name == "mock" else JevBackend(lambda name, usage: cache.log_request(args.run_id, name, usage)); records=[]
    for number, row in enumerate(rows, 1):
        result=cache.get(row[config.text_column], config.digest, backend_name, config.model)
        if result is None:
            result=backend.triage(row[config.text_column], config); cache.put(row[config.text_column],config.digest,config.model,result)
        if number % 10 == 0:
            args.run_lock.heartbeat(); print(json.dumps({"progress":{"processed":number,"total":len(rows)}},ensure_ascii=False))
        item={**row,"backend":result.backend,"model":result.model,"prediction":result.classification.choice,"confidence":result.classification.confidence,"top_probability":result.classification.top_probability,"probabilities":result.classification.probabilities,"correct":result.classification.choice == row["label"]}
        records.append(item)
    summary=cache.request_summary(args.run_id) if backend_name == "jev" else {"requests":0,"input_tokens":0,"output_tokens":0}
    if backend_name == "jev": print(json.dumps({"actual_requests_this_run":summary["requests"],"cumulative_usage_this_run":{"input_tokens":summary["input_tokens"],"output_tokens":summary["output_tokens"]},"estimated_input_cost_usd":round(summary["input_tokens"]*.042/1_000_000,10)},ensure_ascii=False))
    return records, summary


def _write_disagreements(records, scenario):
    path=Path("reports") / scenario / "disagreements.csv"; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=["id","text","label","jev_choice","confidence","top_probability"]); writer.writeheader()
        for r in records:
            if not r["correct"]: writer.writerow({"id":r["id"],"text":r["text"],"label":r["label"],"jev_choice":r["prediction"],"confidence":r["confidence"],"top_probability":r["top_probability"]})


def report(args, config) -> int:
    rows=stratified_sample(_rows(args.labeled,config.text_column),args.sample,args.seed)
    if args.sample is not None: print(json.dumps({"sampled":[{"id":r["id"],"ambiguous":r.get("ambiguous","false")} for r in rows]},ensure_ascii=False))
    records, usage=_predict(rows,config,args.backend,args)
    rng=random.Random(args.seed); rng.shuffle(records); midpoint=len(records)//2; fit, validation=records[:midpoint],records[midpoint:]
    thresholds=[fit_threshold(fit,metric,args.target) for metric in ("confidence","top_probability")]
    validations={item.metric:evaluate(validation,item) for item in thresholds}
    viable=[item for item in thresholds if item.feasible]
    selected=max(viable,key=lambda x:(evaluate(fit,x)["coverage"], validations[x.metric]["coverage"])) if viable else None
    title=report_title({r["backend"] for r in records}); mock=not permits_accuracy_commitment({r["backend"] for r in records})
    lines=[f"# {title}","", "**样本量小，结论仅供参考；合成数据仅用于演示，不代表真实业务。**","",f"样本来源：synthetic；样本数：{len(records)}；拟合/验证：{len(fit)}/{len(validation)}；实际模型：{', '.join(sorted({r['model'] for r in records}))}；backend：{args.backend}","", "## 样本量可行性", "", "| 目标 | 全部答对时所需高置信度样本 | 本次拟合阈值集最大样本数 |", "|---|---:|---:|"]
    for target in (.85,.90,.95): lines.append(f"| {target:.2f} | {best_case_minimum(target)} | {len(fit)} |")
    if mock:
        lines += ["", "本报告来自 **MOCK 演示数据，无意义**。不产生真实准确率、自动处理比例或任何准确率承诺。"]
    else:
        lines += ["", "## 指标对比", "", "| 指标 | 阈值 | 拟合样本/正确/95%下界 | 验证自动数/比例/实际准确率/95%下界 |", "|---|---:|---:|---:|"]
        for item in thresholds:
            value=validations[item.metric]
            threshold="—" if item.threshold is None else f"{item.threshold:.3f}"
            lines.append(f"| {item.metric} | {threshold} | {item.count}/{item.correct}/{item.lower:.3f} | {value['count']}/{value['coverage']:.1%}/{value['accuracy']:.1%}/{value['lower']:.3f} |")
        if selected is None: lines += ["",threshold_failure_message(thresholds, args.target), "当前数据下无法承诺该目标；confidence 与 top_probability 均不适合作为当前阈值。"]
        else:
            selected_value = validations[selected.metric]
            peers = [item for item in viable if abs(validations[item.metric]["coverage"] - selected_value["coverage"]) < 1e-12]
            reason = "两项指标在本次验证集的自动覆盖率并列；以 confidence 作为确定性平局规则。该选择不是两者效果存在差异的证据。" if len(peers) > 1 else "拟合集达到目标后自动覆盖率更高；验证集统计仅用于独立检查。"
            lines += ["",f"选择 `{selected.metric}`，阈值 `{selected.threshold:.3f}`：{reason}","", "## 三档分流"]
            auto=[r for r in validation if r[selected.metric]>=selected.threshold]; review=[r for r in validation if selected.threshold-config.review_band<=r[selected.metric]<selected.threshold]; human=[r for r in validation if r[selected.metric]<selected.threshold-config.review_band]
            lines += ["",f"自动处理：{len(auto)}；建议复核：{len(review)}；转人工：{len(human)}（{len(human)/len(validation):.1%}）。",f"建议复核带下沿为阈值减 `review_band={config.review_band:.2f}`；这是默认配置，尚未经过数据验证。"]
        lines += ["", "## Confidence 校准表", "", f"以下按全部 records（n={len(records)}）统计，而非只用验证集。", "", "| 区间 | 样本数 | 实际准确率 |", "|---|---:|---:|"]
        for interval,count,accuracy in confidence_bins(records): lines.append(f"| {interval} | {count} | {'—' if not count else f'{accuracy:.1%}'} |")
        confusion={}
        for r in records:
            if not r["correct"]: confusion[(r["label"],r["prediction"])]=confusion.get((r["label"],r["prediction"]),0)+1
        top=sorted(confusion.items(),key=lambda x:x[1],reverse=True)[:2]
        lines += ["", "## 易混淆选项", "", "；".join(f"{a} → {b}（{n}）：建议改写两者名称和边界说明" for ((a,b),n) in top) if top else "未观察到错误。"]
    near_perfect = records and sum(r["correct"] for r in records) / len(records) >= .95
    lines += ["", "## 局限", "", "数据均为合成数据，标签与提示词按同一判定规则编写，因此不能代表真实业务的准确率。", fit_size_limitation(len(records), args.target)]
    if near_perfect: lines.append("数据可能过于简单，校准表信息量有限。")
    output_dir = Path("reports") / config.scenario; output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    if args.backend == "jev": _write_disagreements(records, config.scenario)
    return 0
