# TicketBuddy RCA Research Framework

Everything needed to run the MSc experiments: fault injection, monitoring
pipeline, LLM-based root cause analysis, and statistical evaluation.

```
fault_injection/
  fault_injector.py     shared module imported by every application Lambda
  inject_fault.py       CLI: activate/deactivate faults, record ground truth
  traffic_generator.py  synthetic workload driver
  setup_experiment.py   one-shot infra: tables, RCA lambda, filters, alarm, rule
rca/
  rca_lambda.py         the core artifact: log collection -> LLM -> diagnosis
evaluation/
  evaluate_results.py   accuracy / precision / recall / F1 / latency stats
```

## Architecture (proposed pipeline)

```
TicketBuddy Lambdas ──> CloudWatch Logs ──> Metric Filter (ErrorEvents)
      ──> CloudWatch Alarm ──> EventBridge ──> TicketBuddy_RCA Lambda
             ──> GPT/Claude ──> {root cause, remediation, confidence}
             ──> DynamoDB (TicketBuddy_RCAResults)  +  SNS email
```

Baseline (traditional) pipeline for comparison: the same alarm with an SNS
alarm action — notification only, no diagnosis.

## Setup (one time)

1. **Bundle the injector into every Lambda.** In `deploy/deploy_lambdas.py`,
   change `zip_lambda()` so each zip also contains the injector:

   ```python
   with zipfile.ZipFile(zip_path, 'w') as z:
       z.write(py_path, arcname=py_file)
       z.write(os.path.join(LAMBDA_DIR, "fault_injector.py"),
               arcname="fault_injector.py")
   ```

   Copy `fault_injection/fault_injector.py` into `lambda_deploy/`.

2. **Instrument the Lambdas.** At the top of each handler:

   ```python
   from fault_injector import apply_fault

   def lambda_handler(event, context):
       fault = apply_fault("TicketBuddy_BookTicket")   # its own name
       if fault and fault.get("fault_type") == "api_failure":
           return {"statusCode": 502, "status": "error",
                   "message": "Upstream API failure"}
       ...
   ```

   Instrument at minimum: BookTicket, UpdateSeat, GetSchedules,
   TaxCalculator, CancelTicket. Redeploy: `cd deploy && python deploy_lambdas.py`.

3. **Create the pipeline.**

   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_API_KEY + LLM_PROVIDER=openai
   cd fault_injection
   python setup_experiment.py
   ```

   Re-run after invoking each Lambda once if any log groups were skipped.

4. **Smoke test.**

   ```bash
   python inject_fault.py activate --target TicketBuddy_BookTicket --type exception
   python traffic_generator.py --base-url http://YOUR-EB-URL --duration 120
   # within ~2-3 min: RCA email arrives; check TicketBuddy_RCAResults
   python inject_fault.py deactivate --target TicketBuddy_BookTicket
   ```

## Experimental protocol (per fault scenario)

N = 10 trials per scenario × 5 scenarios + 10 control trials = 60 runs.

For each trial:
1. `python inject_fault.py activate --target <fn> --type <fault>`
2. `python traffic_generator.py --base-url <url> --duration 300`
3. Wait for the RCA pipeline to fire (alarm period is 60 s).
4. `python inject_fault.py deactivate --target <fn>`
5. Cool-down 5 min (lets the alarm return to OK so the next trial
   re-triggers a fresh ALARM transition).

Controls: `python inject_fault.py control --minutes 10`, then run traffic
with no fault. Any RCA result in a control window is a false positive.

Scenario mapping (thesis Table X):

| Base paper fault   | This study            | --type            | suggested --target          |
|--------------------|-----------------------|-------------------|-----------------------------|
| Packet Loss        | API Request Failure   | api_failure       | TicketBuddy_TaxCalculator   |
| Slow Connection    | Lambda Timeout        | timeout           | TicketBuddy_BookTicket      |
| CPU Overload       | High Resource Usage   | cpu_overload      | TicketBuddy_GetSchedules    |
| DAL Crash          | Lambda Exception      | exception         | TicketBuddy_BookTicket      |
| Fail-over Failure  | DynamoDB Failure      | dynamodb_failure  | TicketBuddy_UpdateSeat      |

## Evaluation

```bash
cd evaluation
python evaluate_results.py
```

Produces `results_summary.csv` (per-category accuracy, precision, recall,
F1, latency mean/variance/std, mean confidence) and `results_per_trial.csv`
(raw data for the appendix). Directly comparable in structure to the base
paper's per-scenario accuracy table (80–99.3%).

## Methodological notes for the thesis

- **Ground truth** is machine-recorded at injection time (timestamps in
  TicketBuddy_GroundTruth), not manually labelled — removes labelling bias.
- **The LLM is explicitly told to ignore FAULT_ACTIVE markers** (see the
  system prompt); it must diagnose from symptoms alone. State this in the
  methodology — it pre-empts the objection that the experiment leaks the
  answer into the logs. For a stricter variant, add a log filter that
  strips FAULT_ACTIVE lines before prompt construction, and report both.
- **Detection latency** = first RCA alarm_time − injected_at. Also report
  analysis_seconds (LLM inference time) separately: total time-to-diagnosis
  vs the base paper's offline analysis is a key comparison axis.
- **Deployment complexity** claim: quantify it — lines of code, number of
  managed services vs the base paper's LTTng + preprocessing + models +
  graph construction; no kernel access required.
- **Threats to validity** to acknowledge: single application, AWS-specific,
  LLM non-determinism (mitigate: report per-scenario variance across the
  10 trials; optionally fix temperature if using OpenAI).
