# Autonomous Compute Manager — Project Context

> Original project brief. Decisions that resolve the open questions below live in
> [ARCHITECTURE.md](ARCHITECTURE.md), which takes precedence where the two differ.

## Purpose

Autonomous Compute Manager is an AI system for developers and researchers who run long computational workloads across multiple compute environments.
Users provide an objective, a success metric, workload code, and a resource budget.
The system then manages execution automatically, recovers failed work, and selects further experiments to improve results.

The long-term scope includes machine learning experiments, video processing, and data pipelines.
The first demonstration focuses on machine learning.

## Confirmed direction

- Pursue an ambitious system that combines autonomous recovery with experiment optimization.
- Use an ambitious Kaggle problem as the basis for the ML demonstration.
- Use Meta AI models (Meta Global AI Developer Hackathon).
- The user has access to a school supercomputer that uses Slurm.
- The user can obtain credits from Modal, Baseten, and ThunderCompute.

The user explicitly rejected a conservative, single-machine scope.
The project direction includes autonomous decisions across multiple compute environments.

## Core capabilities

### Intelligent monitoring and recovery

The system analyzes workload code, logs, metrics, and previous executions to learn what healthy progress looks like.
It monitors active jobs for stalled work, repeated failures, and wasted compute.
When it detects a problem, it investigates the cause and selects a recovery action.

Potential recovery actions include a retry, a configuration change, and a restart from a checkpoint.
A checkpoint is saved execution state that lets a job resume without repeating all previous work.
Support for each action depends on the workload and execution environment.

### Autonomous experiment optimization

The system reasons about the user's objective, experiment results, and remaining resources.
It identifies weak approaches, stops experiments when justified, allocates compute to promising approaches, and proposes further experiments.

Operational health and experiment value require separate judgments.
A job can run correctly while producing results that do not justify more compute.
A failed job can still produce evidence that improves the next decision.

## Intended workflow

1. The user supplies a task, workload code, data, a success metric, and a resource budget.
2. The agent analyzes the workload and chooses signals that indicate progress, failure, and result quality.
3. A distributed job queue schedules work across available compute environments.
4. The system collects execution state, logs, metrics, results, and resource usage.
5. The agent investigates failures and starts recovery actions.
6. The agent compares experiments and decides which work to stop, continue, or start next.
7. The system reports decisions, supporting evidence, resource usage, and final results.

## Demonstration and evidence

Compare autonomous execution with a defined baseline under comparable resource limits. Measure:

- Reliability, including successful completion, recovery time, and repeated failures.
- Compute efficiency, including resource use and work lost to failures or weak experiments.
- Final model performance on a consistent evaluation procedure.
- Decision quality, using recorded evidence to explain experiment choices and recovery actions.

An injected failure demonstrates recovery under known conditions.
The comparison must record model calls, queue delays, and resource usage so that gains can be interpreted correctly.
No benchmark improvements have been measured or claimed.
